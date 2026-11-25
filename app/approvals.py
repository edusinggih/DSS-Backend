"""Sequential DSS approval workflow: KaSie (legacy LEADER), Supervisor, Manager."""
from functools import wraps
import json
from flask import Blueprint, jsonify, request
from app.auth import get_current_user
from app.db import get_db_connection

approvals_bp = Blueprint("approvals", __name__, url_prefix="/api/approvals")
LEVELS = ("LEADER", "SUPERVISOR", "MANAGER")
LEVEL_LABELS = {"LEADER": "KaSie", "SUPERVISOR": "Supervisor", "MANAGER": "Manager"}
NEXT_STATUS = {"LEADER": "WAITING_SUPERVISOR", "SUPERVISOR": "WAITING_MANAGER", "MANAGER": "APPROVED_FINAL"}
EXPECTED_STATUS = {"LEADER": "WAITING_KASIE", "SUPERVISOR": "WAITING_SUPERVISOR", "MANAGER": "WAITING_MANAGER"}

def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify(success=False, message="Sesi tidak valid atau sudah berakhir."), 401
        return fn(user, *args, **kwargs)
    return wrapper

def _is_admin(user):
    return user["role"] == "ADMIN"

@approvals_bp.get("")
@require_user
def list_approvals(user):
    status = (request.args.get("status") or "").upper()
    with get_db_connection() as conn:
        params = []
        sql = """
          SELECT a.id AS approval_id, a.submission_id, a.approval_level, a.reviewer_id,
                 a.decision, a.note, a.decided_at, s.ss_number, s.title, s.status AS submission_status,
                 s.department_id, d.name AS department_name, u.full_name AS submitter_name
          FROM ss_approvals a
          JOIN ss_submissions s ON s.id=a.submission_id
          LEFT JOIN departments d ON d.id=s.department_id
          LEFT JOIN users u ON u.id=s.submitted_by
          WHERE 1=1
        """
        if not _is_admin(user):
            sql += " AND a.reviewer_id=?"
            params.append(user["id"])
        # Fetch all stages before calculating can_decide; filtering here can
        # hide earlier APPROVED stages and incorrectly unlock a later stage.
        sql += " ORDER BY s.submitted_at DESC, s.id DESC, a.id"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    # Calculate workflow state using the complete stage set.
    for row in rows:
        row["approval_label"] = LEVEL_LABELS.get(row["approval_level"], row["approval_level"])
        order = {"LEADER": 1, "SUPERVISOR": 2, "MANAGER": 3}
        earlier = [x for x in rows if x["submission_id"] == row["submission_id"]
                   and order.get(x["approval_level"], 99) < order.get(row["approval_level"], 99)]
        row["can_decide"] = (
            row["decision"] == "PENDING"
            and row["reviewer_id"] == user["id"]
            and all(x["decision"] == "APPROVED" for x in earlier)
            and row["submission_status"] == EXPECTED_STATUS.get(row["approval_level"])
        )
    if status:
        rows = [row for row in rows if str(row["decision"]).upper() == status]
    return jsonify(success=True, data=rows), 200

@approvals_bp.get("/assignments")
@require_user
def get_assignments(user):
    if not _is_admin(user):
        return jsonify(success=False, message="Hanya Admin yang dapat mengatur approver."), 403
    with get_db_connection() as conn:
        departments = [dict(r) for r in conn.execute(
            "SELECT id,name FROM departments WHERE is_active=1 ORDER BY name").fetchall()]
        employees = [dict(r) for r in conn.execute(
            "SELECT id,full_name,employee_id,role,department_id FROM users WHERE is_active=1 ORDER BY full_name").fetchall()]
        assignments = [dict(r) for r in conn.execute("""
            SELECT da.id,da.department_id,d.name AS department_name,da.approval_level,
                   da.user_id,u.full_name,u.employee_id
            FROM department_approvers da
            JOIN departments d ON d.id=da.department_id
            JOIN users u ON u.id=da.user_id
            WHERE da.is_active=1 ORDER BY d.name,da.approval_level
        """).fetchall()]
    for row in assignments:
        row["approval_label"] = LEVEL_LABELS.get(row["approval_level"], row["approval_level"])
    return jsonify(success=True, data={"departments":departments,"employees":employees,"assignments":assignments}), 200

@approvals_bp.put("/assignments")
@require_user
def save_assignment(user):
    if not _is_admin(user):
        return jsonify(success=False, message="Hanya Admin yang dapat mengatur approver."), 403
    data=request.get_json(silent=True) or {}
    try:
        department_id=int(data.get("department_id"))
        user_id=int(data.get("user_id"))
    except (TypeError,ValueError):
        return jsonify(success=False,message="Departemen dan karyawan wajib dipilih."),400
    level=str(data.get("approval_level") or "").upper()
    if level not in LEVELS:
        return jsonify(success=False,message="Level approval tidak valid."),400
    required_role = level
    with get_db_connection() as conn:
        employee=conn.execute("SELECT id,role,department_id,is_active FROM users WHERE id=?",(user_id,)).fetchone()
        if not employee or not employee["is_active"] or employee["department_id"] != department_id:
            return jsonify(success=False,message="Approver harus karyawan aktif dari departemen yang dipilih."),400
        if employee["role"] not in (required_role, "ADMIN"):
            return jsonify(success=False,message=f"Role akun harus {LEVEL_LABELS[level]} ({required_role}) atau ADMIN."),400
        conn.execute("""
          UPDATE department_approvers SET is_active=0
          WHERE department_id=? AND approval_level=? AND is_active=1
        """,(department_id,level))
        conn.execute("""
          INSERT INTO department_approvers(department_id,approval_level,user_id,is_active,created_by)
          VALUES(?,?,?,1,?)
          ON CONFLICT(department_id,approval_level,user_id)
          DO UPDATE SET is_active=1,created_by=excluded.created_by
        """,(department_id,level,user_id,user["id"]))
        # Reassign undecided approval tasks only. Completed decisions remain
        # attributed to the reviewer who actually made them.
        conn.execute("""
          UPDATE ss_approvals
          SET reviewer_id=?
          WHERE approval_level=? AND decision='PENDING'
            AND submission_id IN (
              SELECT id FROM ss_submissions WHERE department_id=?
            )
        """, (user_id, level, department_id))
        current = conn.execute("""
          SELECT approval_level,user_id FROM department_approvers
          WHERE department_id=? AND is_active=1
        """,(department_id,)).fetchall()
        assigned = {r["approval_level"]:r["user_id"] for r in current}
        if all(x in assigned for x in LEVELS):
            waiting = conn.execute("""
              SELECT id FROM ss_submissions
              WHERE department_id=? AND status='SUBMITTED'
            """,(department_id,)).fetchall()
            for submission in waiting:
                exists = conn.execute("SELECT 1 FROM ss_approvals WHERE submission_id=? LIMIT 1",(submission["id"],)).fetchone()
                if exists: continue
                for stage in LEVELS:
                    conn.execute("""
                      INSERT INTO ss_approvals(submission_id,approval_level,reviewer_id,decision)
                      VALUES(?,?,?,'PENDING')
                    """,(submission["id"],stage,assigned[stage]))
                    conn.execute("""
                      INSERT INTO ss_approval_history(submission_id,approval_level,reviewer_id,decision,note)
                      VALUES(?,?,?,'PENDING','Dialihkan ke workflow approval KaSie')
                    """,(submission["id"],stage,assigned[stage]))
                conn.execute("UPDATE ss_submissions SET status='WAITING_KASIE',updated_at=CURRENT_TIMESTAMP WHERE id=?",(submission["id"],))
        conn.execute("""
          INSERT INTO ss_activity_logs(actor_id,action,description,metadata_json)
          VALUES(?,?,?,?)
        """,(user["id"],"SET_APPROVER",f"Approver {LEVEL_LABELS[level]} diperbarui",json.dumps({"department_id":department_id,"user_id":user_id})))
    return jsonify(success=True,message=f"Approver {LEVEL_LABELS[level]} berhasil disimpan."),200


@approvals_bp.get("/<int:submission_id>/history")
@require_user
def approval_history(user, submission_id):
    with get_db_connection() as conn:
        submission=conn.execute("SELECT submitted_by FROM ss_submissions WHERE id=?",(submission_id,)).fetchone()
        if not submission:
            return jsonify(success=False,message="Suggestion tidak ditemukan."),404
        allowed = _is_admin(user) or submission["submitted_by"] == user["id"]
        if not allowed:
            allowed = conn.execute("SELECT 1 FROM ss_contributors WHERE submission_id=? AND user_id=?",(submission_id,user["id"])).fetchone() is not None
        if not allowed:
            allowed = conn.execute("SELECT 1 FROM ss_approvals WHERE submission_id=? AND reviewer_id=?",(submission_id,user["id"])).fetchone() is not None
        if not allowed:
            return jsonify(success=False,message="Anda tidak memiliki akses ke riwayat approval."),403
        rows=conn.execute("""
          SELECT h.id,h.approval_level,h.reviewer_id,u.full_name AS reviewer_name,
                 h.decision,h.note,h.decided_at,h.created_at
          FROM ss_approval_history h LEFT JOIN users u ON u.id=h.reviewer_id
          WHERE h.submission_id=? ORDER BY h.id
        """,(submission_id,)).fetchall()
    result=[]
    for row in rows:
        item=dict(row); item["approval_label"]=LEVEL_LABELS.get(item["approval_level"],item["approval_level"]); result.append(item)
    return jsonify(success=True,data=result),200

@approvals_bp.post("/<int:submission_id>/decision")
@require_user
def decide(user, submission_id):
    data=request.get_json(silent=True) or {}
    decision=str(data.get("decision") or "").upper()
    note=str(data.get("note") or "").strip()
    if decision not in ("APPROVED","REVISION","REJECTED"):
        return jsonify(success=False,message="Keputusan tidak valid."),400
    if decision in ("REVISION","REJECTED") and not note:
        return jsonify(success=False,message="Catatan wajib diisi untuk revisi atau penolakan."),400
    with get_db_connection() as conn:
        submission=conn.execute("SELECT * FROM ss_submissions WHERE id=?",(submission_id,)).fetchone()
        if not submission:
            return jsonify(success=False,message="Suggestion tidak ditemukan."),404
        approval=conn.execute("""
          SELECT * FROM ss_approvals WHERE submission_id=? AND reviewer_id=? AND decision='PENDING'
          ORDER BY CASE approval_level WHEN 'LEADER' THEN 1 WHEN 'SUPERVISOR' THEN 2 ELSE 3 END LIMIT 1
        """,(submission_id,user["id"])).fetchone()
        if not approval:
            return jsonify(success=False,message="Tidak ada tugas approval aktif untuk akun ini."),403
        level=approval["approval_level"]
        expected_status = EXPECTED_STATUS.get(level)
        current_status = str(submission["status"] or "").upper()
        if current_status != expected_status:
            return jsonify(
                success=False,
                message="Tahap approval ini belum menjadi tahap aktif atau status suggestion sudah berubah."
            ),409

        previous=conn.execute("""
          SELECT approval_level,decision FROM ss_approvals WHERE submission_id=?
          AND CASE approval_level WHEN 'LEADER' THEN 1 WHEN 'SUPERVISOR' THEN 2 ELSE 3 END
              < CASE ? WHEN 'LEADER' THEN 1 WHEN 'SUPERVISOR' THEN 2 ELSE 3 END
        """,(submission_id,level)).fetchall()
        if any(r["decision"]!="APPROVED" for r in previous):
            return jsonify(success=False,message="Tahap sebelumnya belum disetujui."),409
        conn.execute("""
          UPDATE ss_approvals SET decision=?,note=?,decided_at=CURRENT_TIMESTAMP
          WHERE id=? AND decision='PENDING'
        """,(decision,note,approval["id"]))
        conn.execute("""
          INSERT INTO ss_approval_history(submission_id,approval_level,reviewer_id,decision,note,decided_at)
          VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
        """,(submission_id,level,user["id"],decision,note))
        if decision=="REVISION":
            snapshot = {
                "submission": dict(submission),
                "aspects": [dict(x) for x in conn.execute("SELECT aspect FROM ss_submission_aspects WHERE submission_id=?",(submission_id,)).fetchall()],
                "indicators": [dict(x) for x in conn.execute("SELECT * FROM ss_result_indicators WHERE submission_id=?",(submission_id,)).fetchall()],
                "contributors": [dict(x) for x in conn.execute("SELECT user_id,contribution_note FROM ss_contributors WHERE submission_id=?",(submission_id,)).fetchall()]
            }
            revision_no = conn.execute("SELECT COALESCE(MAX(revision_no),0)+1 AS n FROM ss_revision_history WHERE submission_id=?",(submission_id,)).fetchone()["n"]
            conn.execute("""
              INSERT INTO ss_revision_history(submission_id,revision_no,changed_by,change_note,snapshot_json)
              VALUES(?,?,?,?,?)
            """,(submission_id,revision_no,user["id"],note,json.dumps(snapshot,default=str)))
        if decision=="APPROVED":
            if level=="MANAGER":
                new_status="APPROVED_FINAL"
                conn.execute("UPDATE ss_submissions SET status=?,final_approved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,submission_id))
                # The submitter is always eligible, even if legacy contributor data is incomplete.
                members = {int(submission["submitted_by"])}
                members.update(
                    int(member["user_id"])
                    for member in conn.execute(
                        "SELECT user_id FROM ss_contributors WHERE submission_id=?",
                        (submission_id,)
                    ).fetchall()
                )
                for member_id in sorted(members):
                    conn.execute("""
                      INSERT OR IGNORE INTO ss_kpi_points(submission_id,user_id,points,reason)
                      VALUES(?,?,1,'FINAL_APPROVAL')
                    """,(submission_id,member_id))
            else:
                new_status=NEXT_STATUS[level]
                conn.execute("UPDATE ss_submissions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,submission_id))
        elif decision=="REVISION":
            new_status="REVISION_REQUESTED"
            conn.execute("UPDATE ss_submissions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,submission_id))
        else:
            new_status="REJECTED"
            conn.execute("UPDATE ss_submissions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(new_status,submission_id))
        conn.execute("""
          INSERT INTO ss_activity_logs(submission_id,actor_id,action,description,metadata_json)
          VALUES(?,?,?,?,?)
        """,(submission_id,user["id"],"APPROVAL_"+decision,f"{LEVEL_LABELS[level]}: {decision}",'{"level":"'+level+'","note":'+json.dumps(note)+'}'))
    return jsonify(success=True,message=f"Keputusan {LEVEL_LABELS[level]} berhasil disimpan.",data={"status":new_status}),200
