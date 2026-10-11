-- Passwords of password-protected statement PDFs, per user and bank (Santander
-- uses the holder's ID number), stored encrypted with Fernet by the backend.
-- The email import tries them when a protected PDF arrives.
-- Run once by hand on Postgres (Render) BEFORE deploying the backend that uses it.
-- Additive: the previous backend keeps working with it.

BEGIN;

CREATE TABLE IF NOT EXISTS bank_pdf_passwords (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bank_name VARCHAR(100) NOT NULL,
    bank_key VARCHAR(100) NOT NULL,
    password_encrypted TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_bank_pdf_password_user_bank UNIQUE (user_id, bank_key)
);
CREATE INDEX IF NOT EXISTS ix_bank_pdf_passwords_user_id ON bank_pdf_passwords (user_id);

COMMIT;
