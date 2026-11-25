"""Structured suggestion form details and attachment API (Milestone 2A)."""
from functools import wraps
from pathlib import Path
from uuid import uuid4
from werkzeug.utils import secure_filename
from flask import Blueprint, current_app, jsonify, request, send_file
from app.auth import get_current_user
from app.db import get_db_connection

suggestion_details_bp = Blueprint("suggestion_details", __name__, url_prefix="/api/suggestions")
ASPECTS = {"QUALITY", "COST", "DELIVERY", "SAFETY", "MORAL", "PRODUCTIVITY", "ENVIRONMENT"}
MAX_ATTACHMENTS = 10
MAX_FILE_SIZE = 8 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify(success=False, message="Sesi tidak valid atau sudah berakhir."), 401
        return fn(user, *args, **kwargs)
    return wrapper

def _submission(conn, user, submission_id):
    row = conn.execute("SELECT * FROM ss_submissions WHERE id=?", (submission_id,)).fetchone()
    if not row:
        return None, (jsonify(success=False, message="Suggestion tidak ditemukan."), 404)
    if user["role"] != "ADMIN":
        visible = conn.execute("""
            SELECT 1 FROM ss_submissions s
            LEFT JOIN ss_contributors c ON c.submission_id=s.id
            WHERE s.id=? AND (
                s.submitted_by=? OR c.user_id=? OR EXISTS (
                    SELECT 1 FROM ss_approvals a
                    WHERE a.submission_id=s.id AND a.reviewer_id=?
                )
            )
        """, (submission_id, user["id"], user["id"], user["id"])).fetchone()
        if not visible:
            return None, (jsonify(success=False, message="Anda tidak memiliki akses."), 403)
    return row, None

def _can_edit(row, user):
    return row["status"] in ("DRAFT", "REVISION_REQUESTED") and (
        row["submitted_by"] == user["id"] or user["role"] == "ADMIN"
    )

def _detail_payload(conn, row):
    aspects = [r["aspect"] for r in conn.execute(
        "SELECT aspect FROM ss_submission_aspects WHERE submission_id=? ORDER BY aspect",
        (row["id"],)).fetchall()]
    indicators = [dict(r) for r in conn.execute("""
        SELECT id, indicator_name, unit, before_value, after_value, saving_value,
               pic, measured_at, notes, sort_order
        FROM ss_result_indicators WHERE submission_id=? ORDER BY sort_order,id
    """, (row["id"],)).fetchall()]
    return {
        "location_machine": row["location_machine"],
        "risk_before": row["risk_before"],
        "risk_reduced": row["risk_reduced"],
        "work_process_before": row["work_process_before"],
        "work_process_after": row["work_process_after"],
        "aspects": aspects,
        "indicators": indicators,
    }

@suggestion_details_bp.route("/<int:submission_id>/form-details", methods=["GET", "PUT"])
@require_user
def form_details(user, submission_id):
    with get_db_connection() as conn:
        row, error = _submission(conn, user, submission_id)
        if error:
            return error
        if request.method == "GET":
            return jsonify(success=True, data=_detail_payload(conn, row)), 200
        if not _can_edit(row, user):
            return jsonify(success=False, message="Data hanya dapat diubah oleh pengusul atau Admin saat berstatus DRAFT atau REVISION_REQUESTED."), 409
        data = request.get_json(silent=True) or {}
        text_fields = ("location_machine", "risk_before", "risk_reduced",
                       "work_process_before", "work_process_after")
        updates = {}
        for field in text_fields:
            if field in data:
                value = data[field]
                if value is not None and not isinstance(value, str):
                    return jsonify(success=False, message=f"{field} harus berupa teks."), 400
                updates[field] = (value or "").strip() if value is not None else None
        if updates:
            sql = "UPDATE ss_submissions SET " + ", ".join(f"{k}=?" for k in updates) + ", updated_at=CURRENT_TIMESTAMP WHERE id=?"
            conn.execute(sql, (*updates.values(), submission_id))
        if "aspects" in data:
            aspects = data["aspects"]
            if not isinstance(aspects, list) or any(not isinstance(x, str) or x.upper() not in ASPECTS for x in aspects):
                return jsonify(success=False, message="Aspek harus berupa daftar dari QUALITY, COST, DELIVERY, SAFETY, MORAL, PRODUCTIVITY, ENVIRONMENT."), 400
            normalized = sorted(set(x.upper() for x in aspects))
            conn.execute("DELETE FROM ss_submission_aspects WHERE submission_id=?", (submission_id,))
            conn.executemany(
                "INSERT INTO ss_submission_aspects (submission_id, aspect) VALUES (?,?)",
                [(submission_id, x) for x in normalized])
        if "indicators" in data:
            indicators = data["indicators"]
            if not isinstance(indicators, list) or len(indicators) > 50:
                return jsonify(success=False, message="Indikator harus berupa daftar maksimal 50 baris."), 400
            clean = []
            for i, item in enumerate(indicators):
                if not isinstance(item, dict):
                    return jsonify(success=False, message="Format indikator tidak valid."), 400
                name = str(item.get("indicator_name") or "").strip()
                if not name or len(name) > 120:
                    return jsonify(success=False, message=f"Nama indikator baris {i+1} wajib diisi (maks. 120 karakter)."), 400
                clean.append((
                    submission_id, name,
                    item.get("unit"), item.get("before_value"), item.get("after_value"),
                    item.get("saving_value"), item.get("pic"), item.get("measured_at"),
                    item.get("notes"), i
                ))
            conn.execute("DELETE FROM ss_result_indicators WHERE submission_id=?", (submission_id,))
            conn.executemany("""
                INSERT INTO ss_result_indicators
                (submission_id, indicator_name, unit, before_value, after_value,
                 saving_value, pic, measured_at, notes, sort_order)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, clean)
        conn.execute("""
            INSERT INTO ss_activity_logs (submission_id, actor_id, action, description)
            VALUES (?, ?, 'UPDATE_FORM_DETAILS', 'Detail form SS diperbarui')
        """, (submission_id, user["id"]))
        refreshed = conn.execute("SELECT * FROM ss_submissions WHERE id=?", (submission_id,)).fetchone()
        return jsonify(success=True, message="Detail form berhasil disimpan.", data=_detail_payload(conn, refreshed)), 200

@suggestion_details_bp.post("/<int:submission_id>/attachments")
@require_user
def upload_attachments(user, submission_id):
    files = request.files.getlist("files")
    attachment_type = (request.form.get("attachment_type") or "").strip().upper()
    if attachment_type not in {"BEFORE", "AFTER", "SUPPORTING"}:
        return jsonify(success=False, message="attachment_type harus BEFORE, AFTER, atau SUPPORTING."), 400
    if not files or len(files) > MAX_ATTACHMENTS:
        return jsonify(success=False, message=f"Pilih 1 sampai {MAX_ATTACHMENTS} file."), 400
    with get_db_connection() as conn:
        row, error = _submission(conn, user, submission_id)
        if error:
            return error
        if not _can_edit(row, user):
            return jsonify(success=False, message="Lampiran hanya dapat ditambahkan saat status DRAFT atau REVISION_REQUESTED oleh pengusul atau Admin."), 409
        existing = conn.execute("SELECT COUNT(*) AS n FROM ss_attachments WHERE submission_id=?", (submission_id,)).fetchone()["n"]
        if existing + len(files) > 30:
            return jsonify(success=False, message="Maksimal 30 lampiran per SS."), 400
        # Validate all files before writing any of them.
        prepared = []
        for file in files:
            original = secure_filename(file.filename or "")
            ext = Path(original).suffix.lower()
            if not original or ext not in ALLOWED_EXTENSIONS:
                return jsonify(success=False, message="Format file yang diizinkan: JPG, JPEG, PNG, WEBP, PDF."), 400
            payload = file.read(MAX_FILE_SIZE + 1)
            if len(payload) > MAX_FILE_SIZE:
                return jsonify(success=False, message="Ukuran setiap file maksimal 8 MB."), 413
            prepared.append((file, original, ext, payload))

        base = Path(current_app.instance_path) / "uploads" / "suggestions" / str(submission_id)
        base.mkdir(parents=True, exist_ok=True)
        saved = []
        created_paths = []
        try:
            for file, original, ext, payload in prepared:
                stored = f"{uuid4().hex}{ext}"
                target = base / stored
                target.write_bytes(payload)
                created_paths.append(target)
                relative = str(target.relative_to(Path(current_app.instance_path))).replace("\\", "/")
                cur = conn.execute("""
                    INSERT INTO ss_attachments
                    (submission_id, uploaded_by, original_name, stored_name, relative_path, mime_type, file_size)
                    VALUES (?,?,?,?,?,?,?)
                """, (submission_id, user["id"], original, stored, relative, file.mimetype, len(payload)))
                saved.append({"id": cur.lastrowid, "original_name": original, "attachment_type": attachment_type, "file_size": len(payload)})
                conn.execute("""
                    INSERT INTO ss_activity_logs (submission_id, actor_id, action, description, metadata_json)
                    VALUES (?, ?, 'UPLOAD_ATTACHMENT', ?, ?)
                """, (submission_id, user["id"], f"Lampiran {attachment_type}: {original}",
                      __import__("json").dumps({"attachment_id": cur.lastrowid, "attachment_type": attachment_type})))
        except Exception:
            # The DB context manager rolls back; clean up files already written.
            for created in created_paths:
                try:
                    created.unlink(missing_ok=True)
                except OSError:
                    current_app.logger.exception("Failed to clean up partial DSS attachment: %s", created)
            raise
        return jsonify(success=True, message="Lampiran berhasil diunggah.", data=saved), 201

@suggestion_details_bp.get("/<int:submission_id>/attachments")
@require_user
def list_attachments(user, submission_id):
    with get_db_connection() as conn:
        row, error = _submission(conn, user, submission_id)
        if error:
            return error
        rows = conn.execute("""
            SELECT id, original_name, mime_type, file_size, created_at
            FROM ss_attachments WHERE submission_id=? ORDER BY id
        """, (submission_id,)).fetchall()
        logs = conn.execute("SELECT metadata_json FROM ss_activity_logs WHERE submission_id=? AND action='UPLOAD_ATTACHMENT'", (submission_id,)).fetchall()
        import json
        types = {}
        for log in logs:
            try:
                meta = json.loads(log["metadata_json"] or "{}")
                types[int(meta.get("attachment_id"))] = meta.get("attachment_type", "SUPPORTING")
            except (ValueError, TypeError): pass
        result = []
        for row in rows:
            item = dict(row); item["attachment_type"] = types.get(item["id"], "SUPPORTING"); result.append(item)
        return jsonify(success=True, data=result), 200

@suggestion_details_bp.get("/<int:submission_id>/attachments/<int:attachment_id>/file")
@require_user
def download_attachment(user, submission_id, attachment_id):
    with get_db_connection() as conn:
        row, error = _submission(conn, user, submission_id)
        if error:
            return error
        item = conn.execute("""
            SELECT original_name, relative_path FROM ss_attachments
            WHERE id=? AND submission_id=?
        """, (attachment_id, submission_id)).fetchone()
        if not item:
            return jsonify(success=False, message="Lampiran tidak ditemukan."), 404
        path = (Path(current_app.instance_path) / item["relative_path"]).resolve()
        root = (Path(current_app.instance_path) / "uploads" / "suggestions" / str(submission_id)).resolve()
        if root not in path.parents or not path.is_file():
            return jsonify(success=False, message="File tidak tersedia."), 404
        return send_file(path, as_attachment=False, download_name=item["original_name"])


@suggestion_details_bp.put("/<int:submission_id>/contributors")
@require_user
def update_contributors(user, submission_id):
    data = request.get_json(silent=True) or {}
    contributor_ids = data.get("contributors")
    if not isinstance(contributor_ids, list):
        return jsonify(success=False, message="Daftar kontributor tidak valid."), 400
    try:
        ids = {int(value) for value in contributor_ids}
    except (TypeError, ValueError):
        return jsonify(success=False, message="Daftar kontributor tidak valid."), 400
    with get_db_connection() as conn:
        row, error = _submission(conn, user, submission_id)
        if error: return error
        if not _can_edit(row, user):
            return jsonify(success=False, message="Kontributor hanya dapat diubah saat DRAFT atau revisi oleh pengusul atau Admin."), 409

        # Pengusul asli wajib tetap menjadi kontributor.
        # Admin yang mengelola data tidak otomatis menjadi anggota suggestion.
        ids.add(int(row["submitted_by"]))
        if len(ids) > 20:
            return jsonify(success=False, message="Maksimal 20 anggota termasuk pengusul utama."), 400

        placeholders = ",".join("?" for _ in ids)
        valid = conn.execute(f"SELECT id FROM users WHERE id IN ({placeholders}) AND is_active=1 AND department_id=?", (*ids, row["department_id"])).fetchall()
        if len(valid) != len(ids):
            return jsonify(success=False, message="Semua kontributor harus karyawan aktif dari departemen yang sama."), 400
        conn.execute("DELETE FROM ss_contributors WHERE submission_id=?", (submission_id,))
        for uid in sorted(ids):
            conn.execute("INSERT INTO ss_contributors (submission_id,user_id,contribution_note) VALUES (?,?,?)",
                         (submission_id, uid, "Pengusul utama" if uid == row["submitted_by"] else None))
        conn.execute("INSERT INTO ss_activity_logs (submission_id,actor_id,action,description) VALUES (?,?,?,?)",
                     (submission_id,user["id"],"UPDATE_CONTRIBUTORS","Kontributor diperbarui"))
    return jsonify(success=True, message="Kontributor berhasil diperbarui."), 200


@suggestion_details_bp.delete("/<int:submission_id>")
@require_user
def delete_draft(user, submission_id):
    from pathlib import Path
    with get_db_connection() as conn:
        row, error = _submission(conn, user, submission_id)
        if error: return error
        if not _can_edit(row, user):
            return jsonify(success=False, message="Hanya pengusul utama atau Admin yang dapat menghapus Draft."), 403
        if row["status"] != "DRAFT":
            return jsonify(success=False, message="Suggestion yang sudah disubmit tidak dapat dihapus. Gunakan mekanisme revisi."), 409
        files = conn.execute("SELECT relative_path FROM ss_attachments WHERE submission_id=?", (submission_id,)).fetchall()
        upload_root = (Path(current_app.instance_path) / "uploads" / "suggestions" / str(submission_id)).resolve()
        for table in ("ss_approvals","ss_approval_history","ss_kpi_points","ss_revision_history",
                      "ss_activity_logs","ss_submission_aspects","ss_result_indicators",
                      "ss_attachments","ss_contributors"):
            conn.execute(f"DELETE FROM {table} WHERE submission_id=?", (submission_id,))
        conn.execute("DELETE FROM ss_submissions WHERE id=?", (submission_id,))
    for item in files:
        path = (Path(current_app.instance_path) / item["relative_path"]).resolve()
        if upload_root in path.parents and path.is_file(): path.unlink(missing_ok=True)
    try:
        if upload_root.exists() and not any(upload_root.iterdir()): upload_root.rmdir()
    except OSError: pass
    return jsonify(success=True, message="Draft berhasil dihapus."), 200
