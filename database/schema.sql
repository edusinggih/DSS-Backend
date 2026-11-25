CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    code TEXT UNIQUE NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT UNIQUE NOT NULL,
    full_name TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    department_id INTEGER REFERENCES departments(id),
    role TEXT NOT NULL DEFAULT 'EMPLOYEE'
        CHECK(role IN (
            'ADMIN',
            'EMPLOYEE',
            'LEADER',
            'SUPERVISOR',
            'MANAGER'
        )),
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ss_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ss_number TEXT UNIQUE,
    title TEXT NOT NULL,
    submitted_by INTEGER REFERENCES users(id),
    department_id INTEGER REFERENCES departments(id),
    status TEXT DEFAULT 'DRAFT',
    condition_before TEXT,
    result_after TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ss_contributors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER REFERENCES ss_submissions(id)
        ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id),
    contribution_note TEXT,
    UNIQUE(submission_id, user_id)
);

CREATE TABLE IF NOT EXISTS ss_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER REFERENCES ss_submissions(id)
        ON DELETE CASCADE,
    approval_level TEXT
        CHECK(approval_level IN (
            'LEADER',
            'SUPERVISOR',
            'MANAGER'
        )),
    reviewer_id INTEGER REFERENCES users(id),
    decision TEXT DEFAULT 'PENDING',
    note TEXT,
    decided_at TEXT,
    UNIQUE(submission_id, approval_level)
);
