import sqlite3
from pathlib import Path

from flask import Flask, jsonify
from flask_cors import CORS

from config import Config
from app.db import get_db_connection
from app.auth import auth_bp
from app.admin import admin_bp
from app.migrations import apply_dss_migrations
from app.suggestions import suggestions_bp
from app.suggestion_details import suggestion_details_bp
from app.approvals import approvals_bp
from app.kpi import kpi_bp
from app.profile import profile_bp
from app.reports import reports_bp


def init_database(app):
    db_path = Path(app.config["DATABASE_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_path = Path(app.root_path).parent / "database" / "schema.sql"
    seed_path = Path(app.root_path).parent / "database" / "seed.sql"

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        apply_dss_migrations(conn)
        if seed_path.exists():
            conn.executescript(seed_path.read_text(encoding="utf-8"))


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": app.config["FRONTEND_ORIGINS"]
            }
        },
        supports_credentials=True
    )

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(suggestions_bp)
    app.register_blueprint(suggestion_details_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(kpi_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(reports_bp)

    init_database(app)

    @app.get("/api/health")
    def health():
        try:
            with get_db_connection() as conn:
                conn.execute("SELECT 1").fetchone()

            return jsonify(
                success=True,
                application="DSS API",
                status="online",
                database="connected"
            ), 200

        except Exception:
            app.logger.exception("DSS health check failed")
            return jsonify(
                success=False,
                application="DSS API",
                status="degraded",
                database="disconnected"
            ), 503

    return app
