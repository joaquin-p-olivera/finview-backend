-- Purchase stores: per-user list of supermarkets, linked from purchase_carts.
-- Run once by hand on Postgres (Render) BEFORE deploying the backend that uses it.
-- Additive: the previous backend keeps working with it.

BEGIN;

CREATE TABLE IF NOT EXISTS purchase_stores (
    id         VARCHAR(36)  PRIMARY KEY,
    user_id    VARCHAR(36)  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_purchase_stores_user_id ON purchase_stores (user_id);
-- One name per user, ignoring case and surrounding spaces.
CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_store_name
    ON purchase_stores (user_id, lower(btrim(name)));

ALTER TABLE purchase_carts
    ADD COLUMN IF NOT EXISTS store_id VARCHAR(36)
        REFERENCES purchase_stores(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_purchase_carts_store_id ON purchase_carts (store_id);

COMMIT;
