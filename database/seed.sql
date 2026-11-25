INSERT INTO departments (name, code)
VALUES ('Maintenance', 'MTC')
ON CONFLICT(code) DO NOTHING;
