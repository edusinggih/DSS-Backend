import sqlite3
from contextlib import contextmanager
from flask import current_app

@contextmanager
def get_db_connection():
    db_path = current_app.config["DATABASE_PATH"]

    conn = sqlite3.connect(
        db_path,
        timeout=10
    )

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
