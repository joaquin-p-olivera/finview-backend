-- Login with Google: the Google account id of each user, and accounts created
-- with Google have no password. Run once by hand on Postgres (Render) BEFORE
-- deploying the backend that uses it. Additive: the previous backend keeps
-- working with it (every existing user still has a password).

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id ON users (google_id);
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;

COMMIT;
