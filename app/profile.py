from pathlib import Path
from uuid import uuid4
from flask import Blueprint, jsonify, request, current_app, send_file, abort
from werkzeug.utils import secure_filename
from app.auth import get_current_user
from app.db import get_db_connection

profile_bp = Blueprint("profile", __name__, url_prefix="/api/profile")
ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

def _profile_dir():
    path = Path(current_app.instance_path) / "profile_photos"
    path.mkdir(parents=True, exist_ok=True)
    return path

@profile_bp.post("/photo")
def upload_photo():
    user = get_current_user()
    if not user: return jsonify(success=False, message="Sesi tidak valid."), 401
    photo = request.files.get("photo")
    if not photo or not photo.filename: return jsonify(success=False, message="Pilih foto terlebih dahulu."), 400
    if photo.mimetype not in ALLOWED: return jsonify(success=False, message="Format foto harus JPG, PNG, atau WebP."), 400
    raw = photo.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024: return jsonify(success=False, message="Ukuran foto maksimal 2 MB."), 413
    # Verify image signature instead of trusting the browser-provided MIME type.
    valid = (photo.mimetype == "image/jpeg" and raw.startswith(b"\xff\xd8\xff")) or (photo.mimetype == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n")) or (photo.mimetype == "image/webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
    if not valid: return jsonify(success=False, message="Isi file bukan gambar yang valid."), 400
    filename = f"user_{user['id']}_{uuid4().hex}{ALLOWED[photo.mimetype]}"
    target = _profile_dir() / secure_filename(filename)
    target.write_bytes(raw)
    with get_db_connection() as conn:
        old = conn.execute("SELECT profile_photo FROM users WHERE id=?", (user["id"],)).fetchone()["profile_photo"]
        conn.execute("UPDATE users SET profile_photo=? WHERE id=?", (filename, user["id"]))
    if old:
        try: (_profile_dir() / old).unlink(missing_ok=True)
        except OSError: pass
    return jsonify(success=True, profile_photo_url=f"/profile/photo/{user['id']}"), 200

@profile_bp.get("/photo/<int:user_id>")
def get_photo(user_id):
    with get_db_connection() as conn:
        row = conn.execute("SELECT profile_photo FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone()
    if not row or not row["profile_photo"]: abort(404)
    path = _profile_dir() / row["profile_photo"]
    if not path.is_file(): abort(404)
    return send_file(path, conditional=True, max_age=300)
