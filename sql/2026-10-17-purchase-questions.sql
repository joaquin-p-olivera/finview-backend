-- Questions about purchases answered by Claude (POST /api/v1/purchase/ask):
-- history shown on the analysis page and the per-user daily limit.
-- Run once by hand on Postgres (Render) BEFORE deploying the backend that uses it.
-- Additive: the previous backend keeps working with it.

BEGIN;

CREATE TABLE IF NOT EXISTS purchase_questions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    answer_table JSON,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_purchase_questions_user_created
    ON purchase_questions (user_id, created_at DESC);

COMMIT;
