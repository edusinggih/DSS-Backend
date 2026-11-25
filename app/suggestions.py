from datetime import datetime
from functools import wraps
import json
import re

from flask import Blueprint, jsonify, request
from app.auth import get_current_user
from app.db import get_db_connection

suggestions_bp = Blueprint("suggestions", __name__, url_prefix="/api/suggestions")

ALLOWED_ROLES = {"ADMIN", "EMPLOYEE", "LEADER", "SUPERVISOR", "MANAGER"}
MAX_CONTRIBUTORS = 20


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify(success=False, message="Sesi tidak valid atau sudah berakhir."), 401
        return fn(user, *args, **kwargs)
    return wrapper


def _can_view(conn, user, submission_id):
    if user["role"] == "ADMIN":
        return True
    row = conn.execute("""
        SELECT s.id
        FROM ss_submissions s
        LEFT JOIN ss_contributors c ON c.submission_id = s.id
        WHERE s.id = ? AND (
            s.submitted_by = ? OR c.user_id = ? OR EXISTS (
                SELECT 1 FROM ss_approvals a
                WHERE a.submission_id = s.id AND a.reviewer_id = ?
            )
        )
    """, (submission_id, user["id"], user["id"], user["id"])).fetchone()
    return bool(row)


def _next_number(conn):
    now = datetime.now()
    prefix = f"DSS-{now:%Y%m}-"
    row = conn.execute("""
        SELECT MAX(CAST(SUBSTR(ss_number, ?) AS INTEGER)) AS last_no
        FROM ss_submissions
        WHERE ss_number LIKE ?
    """, (len(prefix) + 1, prefix + "%")).fetchone()
    return prefix + f"{(row['last_no'] or 0) + 1:04d}"


def _submission_dict(conn, row):
    contributors = conn.execute("""
        SELECT u.id, u.employee_id, u.full_name, c.contribution_note,
               CASE WHEN u.id = ? THEN 1 ELSE 0 END AS is_submitter
        FROM ss_contributors c
        JOIN users u ON u.id = c.user_id
        WHERE c.submission_id = ?
        ORDER BY is_submitter DESC, u.full_name COLLATE NOCASE
    """, (row["submitted_by"], row["id"])).fetchall()
    category = conn.execute(
        "SELECT name FROM suggestion_categories WHERE id = ?",
        (row["category_id"],)
    ).fetchone() if row["category_id"] else None
    return {
        "id": row["id"],
        "ss_number": row["ss_number"],
        "title": row["title"],
        "status": row["status"],
        "category_id": row["category_id"],
        "category_name": category["name"] if category else None,
        "department_id": row["department_id"],
        "department_name": row["department_name"],
        "submitted_by": row["submitted_by"],
        "submitter_name": row["submitter_name"],
        "condition_before": row["condition_before"],
        "improvement_action": row["improvement_action"],
        "result_after": row["result_after"],
        "expected_benefit": row["expected_benefit"],
        "actual_benefit": row["actual_benefit"],
        "created_at": row["created_at"],
        "submitted_at": row["submitted_at"],
        "contributors": [dict(x) for x in contributors],
    }


@suggestions_bp.get("/meta")
@require_user
def suggestion_meta(user):
    with get_db_connection() as conn:
        categories = conn.execute("""
            SELECT id, name, description
            FROM suggestion_categories
            WHERE is_active = 1
            ORDER BY sort_order, name
        """).fetchall()
        employees = conn.execute("""
            SELECT id, employee_id, full_name, department_id
            FROM users
            WHERE is_active = 1 AND department_id = ?
            ORDER BY full_name COLLATE NOCASE
        """, (user["department_id"],)).fetchall()
    return jsonify(success=True, data={
        "categories": [dict(x) for x in categories],
        "employees": [dict(x) for x in employees],
        "current_user_id": user["id"],
    }), 200


@suggestions_bp.get("")
@require_user
def list_suggestions(user):
    status = (request.args.get("status") or "").strip().upper()
    search = (request.args.get("search") or "").strip()
    params = []
    sql = """
        SELECT DISTINCT s.*, d.name AS department_name,
               u.full_name AS submitter_name
        FROM ss_submissions s
        LEFT JOIN departments d ON d.id = s.department_id
        LEFT JOIN users u ON u.id = s.submitted_by
        LEFT JOIN ss_contributors c ON c.submission_id = s.id
        WHERE 1=1
    """
    if user["role"] != "ADMIN":
        sql += " AND (s.submitted_by = ? OR c.user_id = ?)"
        params.extend([user["id"], user["id"]])
    if status:
        sql += " AND UPPER(s.status) = ?"
        params.append(status)
    if search:
        like = f"%{search}%"
        sql += " AND (s.ss_number LIKE ? OR s.title LIKE ?)"
        params.extend([like, like])
    sql += " ORDER BY s.id DESC LIMIT 200"

    with get_db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        data = [_submission_dict(conn, row) for row in rows]
    return jsonify(success=True, data=data), 200


@suggestions_bp.post("")
@require_user
def create_suggestion(user):
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    before = str(data.get("condition_before") or "").strip()
    action = str(data.get("improvement_action") or "").strip()
    after = str(data.get("result_after") or "").strip()
    expected = str(data.get("expected_benefit") or "").strip()
    actual = str(data.get("actual_benefit") or "").strip()
    category_id = data.get("category_id")
    submit = data.get("submit", False)

    if not title or len(title) > 180:
        return jsonify(success=False, message="Judul wajib diisi (maksimal 180 karakter)."), 400
    if not before or not action or not after:
        return jsonify(success=False, message="Kondisi sebelum, tindakan improvement, dan hasil sesudah wajib diisi."), 400
    if not isinstance(submit, bool):
        return jsonify(success=False, message="Nilai submit tidak valid."), 400
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        return jsonify(success=False, message="Kategori wajib dipilih."), 400

    raw_contributors = data.get("contributors") or []
    if not isinstance(raw_contributors, list):
        return jsonify(success=False, message="Daftar kontributor tidak valid."), 400
    contributor_ids = {user["id"]}
    try:
        contributor_ids.update(int(x) for x in raw_contributors)
    except (TypeError, ValueError):
        return jsonify(success=False, message="Daftar kontributor tidak valid."), 400
    if len(contributor_ids) > MAX_CONTRIBUTORS:
        return jsonify(success=False, message=f"Maksimal {MAX_CONTRIBUTORS} anggota termasuk pengusul utama."), 400

    with get_db_connection() as conn:
        # Serialize number allocation and INSERT to avoid duplicate DSS numbers.
        conn.execute("BEGIN IMMEDIATE")
        category = conn.execute(
            "SELECT id FROM suggestion_categories WHERE id = ? AND is_active = 1",
            (category_id,)
        ).fetchone()
        if not category:
            return jsonify(success=False, message="Kategori tidak ditemukan atau tidak aktif."), 400

        placeholders = ",".join("?" for _ in contributor_ids)
        employees = conn.execute(f"""
            SELECT id FROM users
            WHERE id IN ({placeholders})
              AND is_active = 1 AND department_id = ?
        """, (*contributor_ids, user["department_id"])).fetchall()
        if len(employees) != len(contributor_ids):
            return jsonify(success=False, message="Semua kontributor harus merupakan karyawan aktif dari departemen yang sama."), 400

        ss_number = _next_number(conn)
        status = "SUBMITTED" if submit else "DRAFT"
        cur = conn.execute("""
            INSERT INTO ss_submissions (
                ss_number, title, submitted_by, department_id, status,
                condition_before, result_after, category_id,
                improvement_action, expected_benefit, actual_benefit,
                created_by, updated_at, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
                      CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE NULL END)
        """, (
            ss_number, title, user["id"], user["department_id"], status,
            before, after, category_id, action, expected, actual,
            user["id"], 1 if submit else 0
        ))
        submission_id = cur.lastrowid
        for contributor_id in sorted(contributor_ids):
            conn.execute("""
                INSERT INTO ss_contributors (submission_id, user_id, contribution_note)
                VALUES (?, ?, ?)
            """, (submission_id, contributor_id, "Pengusul utama" if contributor_id == user["id"] else None))
        conn.execute("""
            INSERT INTO ss_activity_logs (submission_id, actor_id, action, description)
            VALUES (?, ?, ?, ?)
        """, (submission_id, user["id"], "CREATE", "Suggestion dibuat sebagai " + status))
    return jsonify(success=True, message="Suggestion berhasil disimpan.", data={
        "id": submission_id, "ss_number": ss_number, "status": status
    }), 201


@suggestions_bp.get("/<int:submission_id>")
@require_user
def get_suggestion(user, submission_id):
    with get_db_connection() as conn:
        if not _can_view(conn, user, submission_id):
            return jsonify(success=False, message="Suggestion tidak ditemukan atau Anda tidak memiliki akses."), 404
        row = conn.execute("""
            SELECT s.*, d.name AS department_name, u.full_name AS submitter_name
            FROM ss_submissions s
            LEFT JOIN departments d ON d.id = s.department_id
            LEFT JOIN users u ON u.id = s.submitted_by
            WHERE s.id = ?
        """, (submission_id,)).fetchone()
        data = _submission_dict(conn, row)
    return jsonify(success=True, data=data), 200


@suggestions_bp.put("/<int:submission_id>")
@require_user
def update_suggestion(user, submission_id):
    data = request.get_json(silent=True) or {}
    fields = {
        "title": "title",
        "condition_before": "condition_before",
        "improvement_action": "improvement_action",
        "result_after": "result_after",
        "expected_benefit": "expected_benefit",
        "actual_benefit": "actual_benefit",
        "category_id": "category_id",
    }
    updates = {}
    for key, column in fields.items():
        if key in data:
            updates[column] = data[key]
    if not updates:
        return jsonify(success=False, message="Tidak ada data yang diperbarui."), 400

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ss_submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        if not row:
            return jsonify(success=False, message="Suggestion tidak ditemukan."), 404
        if row["submitted_by"] != user["id"] and user["role"] != "ADMIN":
            return jsonify(success=False, message="Hanya pengusul utama atau Admin yang dapat mengubah data."), 403
        if row["status"] not in ("DRAFT", "REVISION_REQUESTED"):
            return jsonify(success=False, message="Suggestion hanya dapat diedit saat Draft atau setelah diminta revisi."), 409

        if "title" in updates:
            updates["title"] = str(updates["title"] or "").strip()
            if not updates["title"] or len(updates["title"]) > 180:
                return jsonify(success=False, message="Judul wajib diisi (maksimal 180 karakter)."), 400
        for key in ("condition_before", "improvement_action", "result_after"):
            if key in updates:
                updates[key] = str(updates[key] or "").strip()
                if not updates[key]:
                    return jsonify(success=False, message="Kondisi sebelum, tindakan, dan hasil sesudah wajib diisi."), 400
        if "category_id" in updates:
            try:
                updates["category_id"] = int(updates["category_id"])
            except (TypeError, ValueError):
                return jsonify(success=False, message="Kategori tidak valid."), 400
            cat = conn.execute("SELECT id FROM suggestion_categories WHERE id=? AND is_active=1", (updates["category_id"],)).fetchone()
            if not cat:
                return jsonify(success=False, message="Kategori tidak ditemukan."), 400
        updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
        sql = "UPDATE ss_submissions SET " + ", ".join(f"{k} = ?" for k in updates) + " WHERE id = ?"
        conn.execute(sql, (*updates.values(), submission_id))
        conn.execute("""
            INSERT INTO ss_activity_logs (submission_id, actor_id, action, description)
            VALUES (?, ?, 'UPDATE_DRAFT', 'Draft suggestion diperbarui')
        """, (submission_id, user["id"]))
    return jsonify(success=True, message="Draft berhasil diperbarui."), 200


@suggestions_bp.post("/<int:submission_id>/submit")
@require_user
def submit_suggestion(user, submission_id):
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM ss_submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        if not row:
            return jsonify(success=False, message="Suggestion tidak ditemukan."), 404
        if row["submitted_by"] != user["id"] and user["role"] != "ADMIN":
            return jsonify(success=False, message="Hanya pengusul utama atau Admin yang dapat submit."), 403
        if row["status"] not in ("DRAFT", "REVISION_REQUESTED"):
            return jsonify(success=False, message="Hanya Draft atau suggestion yang diminta revisi yang dapat disubmit."), 409
        if not row["category_id"] or not row["condition_before"] or not row["improvement_action"] or not row["result_after"]:
            return jsonify(success=False, message="Lengkapi kategori, kondisi sebelum, tindakan improvement, dan hasil sesudah."), 400
        assignments = conn.execute("""
            SELECT approval_level,user_id FROM department_approvers
            WHERE department_id=? AND is_active=1
        """, (row["department_id"],)).fetchall()
        assigned = {item["approval_level"]: item["user_id"] for item in assignments}
        missing = [level for level in ("LEADER","SUPERVISOR","MANAGER") if level not in assigned]
        if missing:
            labels = {"LEADER":"KaSie","SUPERVISOR":"Supervisor","MANAGER":"Manager"}
            return jsonify(success=False, message="Admin belum menetapkan approver: " + ", ".join(labels[x] for x in missing)), 409
        existing_approvals = conn.execute("SELECT COUNT(*) AS n FROM ss_approvals WHERE submission_id=?", (submission_id,)).fetchone()["n"]
        if existing_approvals:
            for level in ("LEADER","SUPERVISOR","MANAGER"):
                conn.execute("""
                    UPDATE ss_approvals SET reviewer_id=?,decision='PENDING',note=NULL,decided_at=NULL
                    WHERE submission_id=? AND approval_level=?
                """, (assigned[level], submission_id, level))
                conn.execute("""
                    INSERT INTO ss_approval_history(submission_id,approval_level,reviewer_id,decision,note)
                    VALUES(?,?,?,'PENDING','Suggestion dikirim ulang setelah revisi')
                """, (submission_id, level, assigned[level]))
        else:
            for level in ("LEADER","SUPERVISOR","MANAGER"):
                conn.execute("""
                    INSERT INTO ss_approvals(submission_id,approval_level,reviewer_id,decision)
                    VALUES(?,?,?,'PENDING')
                """, (submission_id, level, assigned[level]))
                conn.execute("""
                    INSERT INTO ss_approval_history(submission_id,approval_level,reviewer_id,decision,note)
                    VALUES(?,?,?,'PENDING','Menunggu keputusan')
                """, (submission_id, level, assigned[level]))
        conn.execute("""
            UPDATE ss_submissions
            SET status='WAITING_KASIE', submitted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (submission_id,))
        conn.execute("""
            INSERT INTO ss_activity_logs (submission_id, actor_id, action, description)
            VALUES (?, ?, 'SUBMIT', 'Suggestion disubmit untuk approval KaSie')
        """, (submission_id, user["id"]))
    return jsonify(success=True, message="Suggestion berhasil disubmit."), 200
