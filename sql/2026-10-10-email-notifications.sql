-- Email notice when the import inbox handles a statement: per-user switch,
-- on by default. Run once by hand on Postgres (Render) BEFORE deploying the
-- backend that uses it. Additive: the previous backend keeps working with it.

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS email_notifications BOOLEAN NOT NULL DEFAULT TRUE;

COMMIT;
