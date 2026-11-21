from flask import Flask, jsonify
from flask_cors import CORS
from config import Config
from app.db import get_db_connection

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    Config.validate()

    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["FRONTEND_ORIGINS"]}},
        supports_credentials=True
    )

    @app.get("/api/health")
    def health():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return jsonify(
                success=True,
                application="DSS API",
                status="online",
                database="connected"
            ), 200
        except Exception:
            app.logger.exception("DSS health check database connection failed")
            return jsonify(
                success=False,
                application="DSS API",
                status="degraded",
                database="disconnected"
            ), 503

    return app
