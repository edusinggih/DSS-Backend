from functools import wraps
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, jsonify, request, current_app
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash

from app.auth import get_current_user
from app.db import get_db_connection

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

ALLOWED_ROLES = {"ADMIN", "EMPLOYEE", "LEADER", "SUPERVISOR", "MANAGER"}


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify(success=False, message="Sesi tidak valid atau sudah berakhir."), 401
        if user["role"] != "ADMIN":
            return jsonify(success=False, message="Akses khusus Administrator."), 403
        return fn(*args, **kwargs)
    return wrapper


def _employee_payload(row):
    return {
        "id": row["id"],
        "employee_id": row["employee_id"],
        "full_name": row["full_name"],
        "username": row["username"],
        "email": row["email"],
        "department_id": row["department_id"],
        "department_name": row["department_name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "profile_photo_url": f"/profile/photo/{row['id']}?v={row['profile_photo']}" if row["profile_photo"] else None,
    }


@admin_bp.get("/dashboard")
@admin_required
def dashboard():
    """Dashboard summary based on the actual DSS workflow statuses."""
    with get_db_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM ss_submissions"
        ).fetchone()["n"]

        pending = conn.execute("""
            SELECT COUNT(*) AS n
            FROM ss_submissions
            WHERE UPPER(COALESCE(status, '')) IN (
                'WAITING_KASIE',
                'WAITING_SUPERVISOR',
                'WAITING_MANAGER'
            )
        """).fetchone()["n"]

        waiting_kasie = conn.execute(
            "SELECT COUNT(*) AS n FROM ss_submissions WHERE status='WAITING_KASIE'"
        ).fetchone()["n"]
        waiting_supervisor = conn.execute(
            "SELECT COUNT(*) AS n FROM ss_submissions WHERE status='WAITING_SUPERVISOR'"
        ).fetchone()["n"]
        waiting_manager = conn.execute(
            "SELECT COUNT(*) AS n FROM ss_submissions WHERE status='WAITING_MANAGER'"
        ).fetchone()["n"]

        approved = conn.execute("""
            SELECT COUNT(*) AS n
            FROM ss_submissions
            WHERE UPPER(COALESCE(status, '')) = 'APPROVED_FINAL'
        """).fetchone()["n"]

        rejected = conn.execute("""
            SELECT COUNT(*) AS n
            FROM ss_submissions
            WHERE UPPER(COALESCE(status, '')) = 'REJECTED'
        """).fetchone()["n"]

        employees = conn.execute("""
            SELECT COUNT(*) AS n FROM users WHERE is_active = 1
        """).fetchone()["n"]

        total_kpi_points = conn.execute("""
            SELECT COALESCE(SUM(points), 0) AS n FROM ss_kpi_points
        """).fetchone()["n"]

        contributing_employees = conn.execute("""
            SELECT COUNT(DISTINCT user_id) AS n
            FROM ss_kpi_points
        """).fetchone()["n"]

    return jsonify(
        success=True,
        data={
            "total_suggestion": total,
            "waiting_approval": pending,
            "waiting_kasie": waiting_kasie,
            "waiting_supervisor": waiting_supervisor,
            "waiting_manager": waiting_manager,
            "approved": approved,
            "rejected": rejected,
            "active_employees": employees,
            "total_kpi_points": total_kpi_points,
            "contributing_employees": contributing_employees,
        },
    ), 200

@admin_bp.get("/employees")
@admin_required
def list_employees():
    search = (request.args.get("search") or "").strip()
    role = (request.args.get("role") or "").strip().upper()
    status = (request.args.get("status") or "").strip().lower()

    sql = """
        SELECT u.id, u.employee_id, u.full_name, u.username, u.email,
               u.department_id, d.name AS department_name,
               u.role, u.is_active, u.created_at, u.profile_photo
        FROM users u
        LEFT JOIN departments d ON d.id = u.department_id
        WHERE 1 = 1
    """
    params = []

    if search:
        pattern = f"%{search}%"
        sql += """ AND (
            u.employee_id LIKE ? OR u.full_name LIKE ?
            OR u.username LIKE ? OR COALESCE(u.email, '') LIKE ?
        )"""
        params.extend([pattern, pattern, pattern, pattern])

    if role:
        if role not in ALLOWED_ROLES:
            return jsonify(success=False, message="Filter role tidak dikenal."), 400
        sql += " AND u.role = ?"
        params.append(role)

    if status in {"active", "inactive"}:
        sql += " AND u.is_active = ?"
        params.append(1 if status == "active" else 0)

    sql += " ORDER BY u.full_name COLLATE NOCASE ASC"

    with get_db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    return jsonify(success=True, data=[_employee_payload(row) for row in rows]), 200


@admin_bp.post("/employees")
@admin_required
def create_employee():
    data = request.get_json(silent=True) or {}
    employee_id = str(data.get("employee_id") or "").strip()
    full_name = str(data.get("full_name") or "").strip()
    username = str(data.get("username") or "").strip()
    email = str(data.get("email") or "").strip() or None
    password = data.get("password") or ""
    role = str(data.get("role") or "EMPLOYEE").strip().upper()
    department_id = data.get("department_id")

    if not all([employee_id, full_name, username, password, department_id]):
        return jsonify(success=False, message="Employee ID, nama, username, password, dan departemen wajib diisi."), 400
    if len(password) < 6:
        return jsonify(success=False, message="Password minimal 6 karakter."), 400
    if role == "ADMIN":
        return jsonify(success=False, message="Pembuatan akun ADMIN dibatasi dan tidak dapat dilakukan melalui form karyawan."), 403
    if role not in ALLOWED_ROLES:
        return jsonify(success=False, message="Role tidak valid."), 400

    try:
        department_id = int(department_id)
    except (TypeError, ValueError):
        return jsonify(success=False, message="Departemen tidak valid."), 400

    with get_db_connection() as conn:
        department = conn.execute(
            "SELECT id FROM departments WHERE id = ? AND is_active = 1",
            (department_id,),
        ).fetchone()
        if not department:
            return jsonify(success=False, message="Departemen tidak ditemukan atau tidak aktif."), 400

        try:
            cur = conn.execute("""
                INSERT INTO users (
                    employee_id, full_name, username, email,
                    password_hash, department_id, role, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                employee_id, full_name, username, email,
                generate_password_hash(password), department_id, role,
            ))
            new_id = cur.lastrowid
        except Exception as exc:
            # Convert SQLite uniqueness errors to a readable validation response.
            if "UNIQUE constraint failed" in str(exc):
                return jsonify(success=False, message="Employee ID, username, atau email sudah digunakan."), 409
            raise

    return jsonify(success=True, message="Akun karyawan berhasil dibuat.", data={"id": new_id}), 201


@admin_bp.put("/employees/<int:employee_id>")
@admin_required
def update_employee(employee_id):
    data = request.get_json(silent=True) or {}
    allowed = {"employee_id", "full_name", "username", "email", "department_id", "role"}
    if not any(key in data for key in allowed):
        return jsonify(success=False, message="Tidak ada data yang diperbarui."), 400

    with get_db_connection() as conn:
        existing = conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (employee_id,)
        ).fetchone()
        if not existing:
            return jsonify(success=False, message="Karyawan tidak ditemukan."), 404

        requested_role = str(data.get("role") or "").strip().upper()
        if "role" in data:
            current_user = get_current_user()
            if existing["id"] == current_user["id"] and requested_role != "ADMIN":
                return jsonify(
                    success=False,
                    message="Admin tidak dapat menurunkan role akun sendiri."
                ), 400

            # Never allow the last active ADMIN account to be demoted.
            if existing["role"] == "ADMIN" and requested_role != "ADMIN":
                active_admins = conn.execute("""
                    SELECT COUNT(*) AS n FROM users
                    WHERE role = 'ADMIN' AND is_active = 1
                """).fetchone()["n"]
                if active_admins <= 1:
                    return jsonify(
                        success=False,
                        message="Tidak dapat mengubah role Admin aktif terakhir."
                    ), 400

        values = {
            "employee_id": str(data.get("employee_id", "")).strip() if "employee_id" in data else None,
            "full_name": str(data.get("full_name", "")).strip() if "full_name" in data else None,
            "username": str(data.get("username", "")).strip() if "username" in data else None,
            "email": (str(data.get("email") or "").strip() or None) if "email" in data else None,
            "department_id": data.get("department_id") if "department_id" in data else None,
            "role": str(data.get("role", "")).strip().upper() if "role" in data else None,
        }

        for key in ("employee_id", "full_name", "username"):
            if key in data and not values[key]:
                return jsonify(success=False, message=f"{key} tidak boleh kosong."), 400

        if "role" in data and values["role"] not in ALLOWED_ROLES:
            return jsonify(success=False, message="Role tidak valid."), 400
        if "role" in data and values["role"] == "ADMIN" and existing["role"] != "ADMIN":
            return jsonify(success=False, message="Promosi menjadi ADMIN tidak dapat dilakukan melalui edit karyawan."), 403

        if "department_id" in data:
            try:
                values["department_id"] = int(values["department_id"])
            except (TypeError, ValueError):
                return jsonify(success=False, message="Departemen tidak valid."), 400
            department = conn.execute(
                "SELECT id FROM departments WHERE id = ? AND is_active = 1",
                (values["department_id"],),
            ).fetchone()
            if not department:
                return jsonify(success=False, message="Departemen tidak ditemukan atau tidak aktif."), 400

        updates = [(key, value) for key, value in values.items() if key in data]
        sql = "UPDATE users SET " + ", ".join(f"{key} = ?" for key, _ in updates) + " WHERE id = ?"
        params = [value for _, value in updates] + [employee_id]

        try:
            conn.execute(sql, params)
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return jsonify(success=False, message="Employee ID, username, atau email sudah digunakan."), 409
            raise

    return jsonify(success=True, message="Data karyawan berhasil diperbarui."), 200


@admin_bp.post("/employees/<int:employee_id>/photo")
@admin_bp.post("/users/<int:employee_id>/photo")
@admin_required
def upload_employee_photo(employee_id):
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        return jsonify(success=False, message="Pilih foto terlebih dahulu."), 400
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if photo.mimetype not in allowed:
        return jsonify(success=False, message="Format foto harus JPG, PNG, atau WebP."), 400
    raw = photo.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        return jsonify(success=False, message="Ukuran foto maksimal 2 MB."), 413
    valid = (photo.mimetype == "image/jpeg" and raw.startswith(b"\xff\xd8\xff")) or (photo.mimetype == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or (photo.mimetype == "image/webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
    if not valid:
        return jsonify(success=False, message="Isi file bukan gambar yang valid."), 400
    folder = Path(current_app.instance_path) / "profile_photos"
    folder.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(f"user_{employee_id}_{uuid4().hex}{allowed[photo.mimetype]}")
    target = folder / filename
    target.write_bytes(raw)
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT id, profile_photo FROM users WHERE id=?", (employee_id,)).fetchone()
            if not row:
                target.unlink(missing_ok=True)
                return jsonify(success=False, message="Karyawan tidak ditemukan."), 404
            old = row["profile_photo"]
            conn.execute("UPDATE users SET profile_photo=? WHERE id=?", (filename, employee_id))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if old:
        try:
            (Path(current_app.instance_path) / "profile_photos" / old).unlink(missing_ok=True)
        except OSError:
            pass
    return jsonify(success=True, message="Foto karyawan berhasil diperbarui.", profile_photo_url=f"/profile/photo/{employee_id}?v={filename}"), 200


@admin_bp.patch("/employees/<int:employee_id>/status")
@admin_required
def set_employee_status(employee_id):
    data = request.get_json(silent=True) or {}
    if "is_active" not in data or not isinstance(data["is_active"], bool):
        return jsonify(success=False, message="is_active harus bernilai true atau false."), 400

    active = data["is_active"]
    current_user = get_current_user()

    with get_db_connection() as conn:
        target = conn.execute(
            "SELECT id, role, is_active FROM users WHERE id = ?",
            (employee_id,),
        ).fetchone()
        if not target:
            return jsonify(success=False, message="Karyawan tidak ditemukan."), 404

        if not active and current_user["id"] == employee_id:
            return jsonify(success=False, message="Admin tidak dapat menonaktifkan akun sendiri."), 400

        if not active and target["role"] == "ADMIN" and target["is_active"]:
            active_admins = conn.execute("""
                SELECT COUNT(*) AS n FROM users
                WHERE role = 'ADMIN' AND is_active = 1
            """).fetchone()["n"]
            if active_admins <= 1:
                return jsonify(success=False, message="Tidak dapat menonaktifkan satu-satunya akun Admin aktif."), 400

        conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if active else 0, employee_id),
        )

    return jsonify(
        success=True,
        message="Akun berhasil diaktifkan." if active else "Akun berhasil dinonaktifkan.",
    ), 200


def _department_code(value):
    import re
    return re.sub(r"[^A-Z0-9_-]+", "-", str(value or "").strip().upper()).strip("-_")[:20]


@admin_bp.get("/departments")
@admin_required
def list_departments():
    search = (request.args.get("search") or "").strip()
    status = (request.args.get("status") or "all").strip().lower()
    sql = """
        SELECT d.id, d.name, d.code, d.is_active, d.created_at,
               COUNT(DISTINCT CASE WHEN u.is_active = 1 THEN u.id END) AS active_employees,
               COUNT(DISTINCT u.id) AS total_employees
        FROM departments d
        LEFT JOIN users u ON u.department_id = d.id
        WHERE 1=1
    """
    params = []
    if search:
        like = f"%{search}%"
        sql += " AND (d.name LIKE ? OR d.code LIKE ?)"
        params.extend([like, like])
    if status == "active":
        sql += " AND d.is_active = 1"
    elif status == "inactive":
        sql += " AND d.is_active = 0"
    elif status != "all":
        return jsonify(success=False, message="Filter status departemen tidak valid."), 400
    sql += " GROUP BY d.id ORDER BY d.is_active DESC, d.name COLLATE NOCASE"
    with get_db_connection() as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        approvers = conn.execute("""
            SELECT da.department_id, da.approval_level, u.full_name, u.employee_id
            FROM department_approvers da
            JOIN users u ON u.id = da.user_id
            WHERE da.is_active = 1
            ORDER BY da.department_id,
              CASE da.approval_level WHEN 'LEADER' THEN 1 WHEN 'SUPERVISOR' THEN 2 ELSE 3 END
        """).fetchall()
    grouped = {}
    labels = {"LEADER": "KaSie", "SUPERVISOR": "Supervisor", "MANAGER": "Manager"}
    for row in approvers:
        grouped.setdefault(row["department_id"], []).append({
            "level": row["approval_level"],
            "label": labels.get(row["approval_level"], row["approval_level"]),
            "full_name": row["full_name"],
            "employee_id": row["employee_id"],
        })
    for row in rows:
        row["is_active"] = bool(row["is_active"])
        row["approvers"] = grouped.get(row["id"], [])
    return jsonify(success=True, data=rows), 200


@admin_bp.post("/departments")
@admin_required
def create_department():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    code = _department_code(data.get("code") or name)
    if not name or len(name) > 80:
        return jsonify(success=False, message="Nama departemen wajib diisi (maksimal 80 karakter)."), 400
    if not code or len(code) < 2:
        return jsonify(success=False, message="Kode departemen minimal 2 karakter."), 400
    with get_db_connection() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO departments(name, code, is_active) VALUES(?,?,1)",
                (name, code),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return jsonify(success=False, message="Nama atau kode departemen sudah digunakan."), 409
            raise
        department_id = cur.lastrowid
    return jsonify(success=True, message="Departemen berhasil ditambahkan.", data={"id": department_id}), 201


@admin_bp.put("/departments/<int:department_id>")
@admin_required
def update_department(department_id):
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    code = _department_code(data.get("code") or "")
    if not name or len(name) > 80:
        return jsonify(success=False, message="Nama departemen wajib diisi (maksimal 80 karakter)."), 400
    if not code or len(code) < 2:
        return jsonify(success=False, message="Kode departemen minimal 2 karakter."), 400
    with get_db_connection() as conn:
        exists = conn.execute("SELECT id FROM departments WHERE id=?", (department_id,)).fetchone()
        if not exists:
            return jsonify(success=False, message="Departemen tidak ditemukan."), 404
        try:
            conn.execute(
                "UPDATE departments SET name=?, code=? WHERE id=?",
                (name, code, department_id),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return jsonify(success=False, message="Nama atau kode departemen sudah digunakan."), 409
            raise
    return jsonify(success=True, message="Departemen berhasil diperbarui."), 200


@admin_bp.patch("/departments/<int:department_id>/status")
@admin_required
def set_department_status(department_id):
    data = request.get_json(silent=True) or {}
    if "is_active" not in data or not isinstance(data["is_active"], bool):
        return jsonify(success=False, message="is_active harus bernilai true atau false."), 400
    active = data["is_active"]
    with get_db_connection() as conn:
        department = conn.execute(
            "SELECT id, is_active FROM departments WHERE id=?", (department_id,)
        ).fetchone()
        if not department:
            return jsonify(success=False, message="Departemen tidak ditemukan."), 404
        if not active:
            active_users = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE department_id=? AND is_active=1",
                (department_id,),
            ).fetchone()["n"]
            if active_users:
                return jsonify(
                    success=False,
                    message=f"Departemen masih memiliki {active_users} karyawan aktif. Pindahkan/nonaktifkan karyawan terlebih dahulu."
                ), 409
            pending = conn.execute("""
                SELECT COUNT(*) AS n FROM ss_submissions
                WHERE department_id=? AND status NOT IN ('APPROVED_FINAL','REJECTED')
            """, (department_id,)).fetchone()["n"]
            if pending:
                return jsonify(
                    success=False,
                    message=f"Departemen masih memiliki {pending} suggestion dalam proses. Selesaikan prosesnya terlebih dahulu."
                ), 409
            active_departments = conn.execute(
                "SELECT COUNT(*) AS n FROM departments WHERE is_active=1"
            ).fetchone()["n"]
            if department["is_active"] and active_departments <= 1:
                return jsonify(success=False, message="Minimal harus ada satu departemen aktif."), 409
            conn.execute(
                "UPDATE department_approvers SET is_active=0 WHERE department_id=? AND is_active=1",
                (department_id,),
            )
        conn.execute(
            "UPDATE departments SET is_active=? WHERE id=?",
            (1 if active else 0, department_id),
        )
    return jsonify(
        success=True,
        message="Departemen berhasil diaktifkan." if active else "Departemen berhasil dinonaktifkan."
    ), 200

