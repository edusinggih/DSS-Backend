"""Non-destructive, idempotent SQLite schema upgrades for DSS."""


def _columns(conn, table_name):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _add_column_if_missing(conn, table_name, column_name, definition):
    if column_name not in _columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def apply_dss_migrations(conn):
    """Extend the existing live schema without dropping or rewriting user data."""
    conn.execute("PRAGMA foreign_keys = ON")

    # Optional self-service profile image path. Existing user records are preserved.
    _add_column_if_missing(conn, "users", "profile_photo", "TEXT")

    # Extend existing suggestion records in place; all new fields are nullable/defaulted.
    submission_columns = {
        "category_id": "INTEGER REFERENCES suggestion_categories(id)",
        "improvement_action": "TEXT",
        "expected_benefit": "TEXT",
        "actual_benefit": "TEXT",
        "updated_at": "TEXT",
        "submitted_at": "TEXT",
        "final_approved_at": "TEXT",
        "created_by": "INTEGER REFERENCES users(id)",
    }
    # Create referenced category table before adding category_id.
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS suggestion_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS department_approvers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department_id INTEGER NOT NULL REFERENCES departments(id),
        approval_level TEXT NOT NULL CHECK(approval_level IN ('LEADER','SUPERVISOR','MANAGER')),
        user_id INTEGER NOT NULL REFERENCES users(id),
        is_active INTEGER NOT NULL DEFAULT 1,
        effective_from TEXT,
        effective_until TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(department_id, approval_level, user_id)
    );

    CREATE TABLE IF NOT EXISTS ss_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES ss_submissions(id) ON DELETE CASCADE,
        uploaded_by INTEGER REFERENCES users(id),
        original_name TEXT NOT NULL,
        stored_name TEXT NOT NULL UNIQUE,
        relative_path TEXT NOT NULL,
        mime_type TEXT,
        file_size INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ss_revision_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES ss_submissions(id) ON DELETE CASCADE,
        revision_no INTEGER NOT NULL,
        changed_by INTEGER REFERENCES users(id),
        change_note TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(submission_id, revision_no)
    );

    CREATE TABLE IF NOT EXISTS ss_approval_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES ss_submissions(id) ON DELETE CASCADE,
        approval_level TEXT NOT NULL CHECK(approval_level IN ('LEADER','SUPERVISOR','MANAGER')),
        reviewer_id INTEGER REFERENCES users(id),
        decision TEXT NOT NULL CHECK(decision IN ('PENDING','APPROVED','REJECTED','REVISION')),
        note TEXT,
        decided_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ss_activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER REFERENCES ss_submissions(id) ON DELETE SET NULL,
        actor_id INTEGER REFERENCES users(id),
        action TEXT NOT NULL,
        description TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS ss_kpi_points (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES ss_submissions(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id),
        points INTEGER NOT NULL DEFAULT 1 CHECK(points > 0),
        reason TEXT NOT NULL DEFAULT 'FINAL_APPROVAL',
        awarded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(submission_id, user_id, reason)
    );

    CREATE INDEX IF NOT EXISTS idx_submissions_department_status
        ON ss_submissions(department_id, status);
    CREATE INDEX IF NOT EXISTS idx_approvers_department_level_active
        ON department_approvers(department_id, approval_level, is_active);
    CREATE INDEX IF NOT EXISTS idx_attachments_submission
        ON ss_attachments(submission_id);
    CREATE INDEX IF NOT EXISTS idx_revision_submission
        ON ss_revision_history(submission_id, revision_no);
    CREATE INDEX IF NOT EXISTS idx_approval_history_submission
        ON ss_approval_history(submission_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_activity_submission
        ON ss_activity_logs(submission_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_kpi_user_awarded
        ON ss_kpi_points(user_id, awarded_at);
    """)

    for column_name, definition in submission_columns.items():
        _add_column_if_missing(conn, "ss_submissions", column_name, definition)


    # Milestone 2A: complete digital suggestion form fields and structured results.
    form_columns = {
        "location_machine": "TEXT",
        "risk_before": "TEXT",
        "risk_reduced": "TEXT",
        "work_process_before": "TEXT",
        "work_process_after": "TEXT",
    }
    for column_name, definition in form_columns.items():
        _add_column_if_missing(conn, "ss_submissions", column_name, definition)

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ss_submission_aspects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES ss_submissions(id) ON DELETE CASCADE,
        aspect TEXT NOT NULL CHECK(aspect IN (
            'QUALITY','COST','DELIVERY','SAFETY','MORAL','PRODUCTIVITY','ENVIRONMENT'
        )),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(submission_id, aspect)
    );

    CREATE TABLE IF NOT EXISTS ss_result_indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES ss_submissions(id) ON DELETE CASCADE,
        indicator_name TEXT NOT NULL,
        unit TEXT,
        before_value TEXT,
        after_value TEXT,
        saving_value TEXT,
        pic TEXT,
        measured_at TEXT,
        notes TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_submission_aspects_submission
        ON ss_submission_aspects(submission_id);
    CREATE INDEX IF NOT EXISTS idx_result_indicators_submission
        ON ss_result_indicators(submission_id, sort_order, id);
    """)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, CURRENT_TIMESTAMP)",
        ("002_suggestion_form_detail",)
    )

    # Seed standard categories only when absent; preserve administrator edits.
    categories = [
        ("Safety", "Keselamatan kerja", 10),
        ("Quality", "Peningkatan kualitas", 20),
        ("Cost Reduction", "Pengurangan biaya", 30),
        ("Productivity", "Peningkatan produktivitas", 40),
        ("Delivery", "Peningkatan ketepatan dan kelancaran delivery", 50),
        ("5S / Environment", "Kerapian, kebersihan, dan lingkungan kerja", 60),
        ("Ergonomic", "Perbaikan ergonomi kerja", 70),
        ("Machine Improvement", "Peningkatan mesin dan peralatan", 80),
        ("Energy Saving", "Penghematan energi", 90),
    ]
    conn.executemany("""
        INSERT INTO suggestion_categories (name, description, sort_order)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO NOTHING
    """, categories)

    conn.execute("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, CURRENT_TIMESTAMP)", ("001_dss_foundation",))


    # Milestone 2D: approval workflow tables (idempotent; preserves existing rows).
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ss_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL REFERENCES ss_submissions(id) ON DELETE CASCADE,
        approval_level TEXT NOT NULL CHECK(approval_level IN ('LEADER','SUPERVISOR','MANAGER')),
        reviewer_id INTEGER REFERENCES users(id),
        decision TEXT NOT NULL DEFAULT 'PENDING' CHECK(decision IN ('PENDING','APPROVED','REJECTED','REVISION')),
        note TEXT,
        decided_at TEXT,
        UNIQUE(submission_id, approval_level)
    );
    CREATE INDEX IF NOT EXISTS idx_ss_approvals_reviewer_decision
        ON ss_approvals(reviewer_id, decision);
    """)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, CURRENT_TIMESTAMP)",
        ("003_sequential_approval_kasie",)
    )
