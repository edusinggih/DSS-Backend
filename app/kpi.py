"""DSS KPI reporting API. Reads the existing ss_kpi_points ledger only."""
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

from app.auth import get_current_user
from app.db import get_db_connection

kpi_bp = Blueprint("kpi", __name__, url_prefix="/api/kpi")


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify(success=False, message="Sesi tidak valid atau sudah berakhir."), 401
        return fn(user, *args, **kwargs)
    return wrapper


def _period_params():
    today = date.today()
    period = (request.args.get("period") or "month").strip().lower()
    if period not in ("all", "month", "year"):
        raise ValueError("Periode harus all, month, atau year.")
    # All-time ranking does not depend on year/month filters.
    if period == "all":
        return period, today.year, today.month
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        raise ValueError("Tahun atau bulan tidak valid.")
    if year < 2000 or year > 2100:
        raise ValueError("Tahun harus antara 2000 sampai 2100.")
    if month < 1 or month > 12:
        raise ValueError("Bulan harus antara 1 sampai 12.")
    return period, year, month


@kpi_bp.get("/summary")
@require_user
def summary(user):
    try:
        period, year, month = _period_params()
    except ValueError as exc:
        return jsonify(success=False, message=str(exc)), 400

    # Dashboard leaderboard is intentionally global for every authenticated user.
    # This endpoint is read-only; management/approval permissions remain unchanged.
    department_id = None

    if period == "all":
        date_clause = "1=1"
        date_params = []
    else:
        date_clause = "strftime('%Y', k.awarded_at) = ?"
        date_params = [f"{year:04d}"]
        if period == "month":
            date_clause += " AND strftime('%m', k.awarded_at) = ?"
            date_params.append(f"{month:02d}")

    department_clause = ""
    department_params = []
    if department_id is not None:
        department_clause = " AND u.department_id = ?"
        department_params.append(department_id)

    with get_db_connection() as conn:
        summary_row = conn.execute(f"""
            SELECT
                COALESCE(SUM(k.points), 0) AS total_points,
                COUNT(DISTINCT k.submission_id) AS approved_suggestions,
                COUNT(DISTINCT k.user_id) AS participating_employees
            FROM ss_kpi_points k
            JOIN users u ON u.id = k.user_id
            WHERE {date_clause} {department_clause}
        """, date_params + department_params).fetchone()

        leaderboard = conn.execute(f"""
            SELECT
                u.id AS user_id,
                u.employee_id,
                u.full_name,
                u.role,
                u.department_id,
                u.profile_photo,
                d.name AS department_name,
                COALESCE(SUM(k.points), 0) AS total_points,
                COUNT(DISTINCT k.submission_id) AS approved_suggestions
            FROM ss_kpi_points k
            JOIN users u ON u.id = k.user_id
            LEFT JOIN departments d ON d.id = u.department_id
            WHERE {date_clause} {department_clause}
            GROUP BY u.id, u.employee_id, u.full_name, u.role, u.department_id, u.profile_photo, d.name
            ORDER BY total_points DESC, approved_suggestions DESC, u.full_name COLLATE NOCASE ASC
        """, date_params + department_params).fetchall()

        if period == "all":
            trend = conn.execute(f"""
                SELECT
                    strftime('%m', k.awarded_at) AS month_number,
                    strftime('%Y', k.awarded_at) AS year_number,
                    strftime('%m/%Y', k.awarded_at) AS period_label,
                    COALESCE(SUM(k.points), 0) AS total_points,
                    COUNT(DISTINCT k.submission_id) AS approved_suggestions
                FROM ss_kpi_points k
                JOIN users u ON u.id = k.user_id
                WHERE date(k.awarded_at) >= date('now', '-11 months', 'start of month')
                  {department_clause}
                GROUP BY strftime('%Y-%m', k.awarded_at)
                ORDER BY strftime('%Y-%m', k.awarded_at)
            """, department_params).fetchall()
        else:
            trend = conn.execute(f"""
                SELECT
                    strftime('%m', k.awarded_at) AS month_number,
                    strftime('%Y', k.awarded_at) AS year_number,
                    strftime('%m', k.awarded_at) AS period_label,
                    COALESCE(SUM(k.points), 0) AS total_points,
                    COUNT(DISTINCT k.submission_id) AS approved_suggestions
                FROM ss_kpi_points k
                JOIN users u ON u.id = k.user_id
                WHERE strftime('%Y', k.awarded_at) = ? {department_clause}
                GROUP BY strftime('%m', k.awarded_at)
                ORDER BY month_number
            """, [f"{year:04d}"] + department_params).fetchall()

        my_row = conn.execute(f"""
            SELECT
                COALESCE(SUM(k.points), 0) AS total_points,
                COUNT(DISTINCT k.submission_id) AS approved_suggestions
            FROM ss_kpi_points k
            WHERE k.user_id = ? AND {date_clause}
        """, [user["id"]] + date_params).fetchone()

    return jsonify(
        success=True,
        data={
            "period": period,
            "year": year if period != "all" else None,
            "month": month if period == "month" else None,
            "scope": "all",
            "department_id": department_id,
            "summary": dict(summary_row),
            "my_kpi": dict(my_row),
            "leaderboard": [{**dict(row), "profile_photo_url": ("/profile/photo/" + str(row["user_id"]) + "?v=" + row["profile_photo"] if row["profile_photo"] else None)} for row in leaderboard],
            "monthly_trend": [dict(row) for row in trend],
        },
    ), 200
