-- Statement import by email: each user forwards their bank emails to
-- finview.import+TOKEN@gmail.com and an hourly job reads that inbox.
-- Run once by hand on Postgres (Render) BEFORE deploying the backend that uses it.
-- Additive: the previous backend keeps working with it.

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS import_token VARCHAR(32);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_import_token ON users (import_token);

ALTER TABLE statements ADD COLUMN IF NOT EXISTS source VARCHAR(20);

CREATE TABLE IF NOT EXISTS email_imports (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id VARCHAR(255) NOT NULL,
    kind VARCHAR(30) NOT NULL,
    status VARCHAR(20) NOT NULL,
    filename VARCHAR(255) NOT NULL DEFAULT '',
    sender VARCHAR(255),
    subject VARCHAR(500),
    statement_id VARCHAR(36) REFERENCES statements(id) ON DELETE SET NULL,
    error_message TEXT,
    details JSON,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_email_import_attachment UNIQUE (user_id, message_id, filename)
);
CREATE INDEX IF NOT EXISTS ix_email_imports_user_id ON email_imports (user_id);

COMMIT;
