
from datetime import timedelta

from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.db import get_db_connection

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

TOKEN_MAX_AGE = 8 * 60 * 60


def create_token(user):
    serializer = URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="dss-auth-token"
    )

    return serializer.dumps({
        "user_id": user["id"],
        "employee_id": user["employee_id"],
        "role": user["role"]
    })


def verify_token(token):
    serializer = URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="dss-auth-token"
    )

    try:
        return serializer.loads(
            token,
            max_age=TOKEN_MAX_AGE
        )
    except (BadSignature, SignatureExpired):
        return None


def get_current_user():
    header = request.headers.get("Authorization", "")

    if not header.startswith("Bearer "):
        return None

    payload = verify_token(header[7:])

    if not payload:
        return None

    with get_db_connection() as conn:
        user = conn.execute("""
            SELECT id, employee_id, full_name, username,
                   email, department_id, role, is_active, profile_photo
            FROM users
            WHERE id = ?
        """, (payload["user_id"],)).fetchone()

    if not user or not user["is_active"]:
        return None

    return user


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}

    identity = (
        data.get("identity")
        or data.get("username")
        or ""
    ).strip()

    password = data.get("password") or ""

    if not identity or not password:
        return jsonify(
            success=False,
            message="Username/NIK dan password wajib diisi."
        ), 400

    with get_db_connection() as conn:
        user = conn.execute("""
            SELECT *
            FROM users
            WHERE username = ? OR employee_id = ?
            LIMIT 1
        """, (identity, identity)).fetchone()

    if (
        not user
        or not user["is_active"]
        or not check_password_hash(user["password_hash"], password)
    ):
        return jsonify(
            success=False,
            message="Username atau password salah."
        ), 401

    token = create_token(user)

    return jsonify(
        success=True,
        message="Login berhasil.",
        token=token,
        user={
            "id": user["id"],
            "employee_id": user["employee_id"],
            "full_name": user["full_name"],
            "username": user["username"],
            "role": user["role"],
            "department_id": user["department_id"],
            "profile_photo_url": f"/profile/photo/{user['id']}" if user["profile_photo"] else None
        }
    ), 200


@auth_bp.get("/me")
def me():
    user = get_current_user()

    if not user:
        return jsonify(
            success=False,
            message="Sesi tidak valid atau sudah berakhir."
        ), 401

    return jsonify(
        success=True,
        user={
            "id": user["id"],
            "employee_id": user["employee_id"],
            "full_name": user["full_name"],
            "username": user["username"],
            "role": user["role"],
            "department_id": user["department_id"],
            "profile_photo_url": f"/profile/photo/{user['id']}" if user["profile_photo"] else None
        }
    ), 200
