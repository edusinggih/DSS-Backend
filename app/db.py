import psycopg2
from flask import current_app

def get_db_connection():
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(
        database_url,
        connect_timeout=10,
        application_name="digital-suggestion-system"
    )
