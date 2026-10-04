-- Purchase AI categorization: Claude's suggestions on products that the user
-- confirms or dismisses (same product as another one, or a note such as
-- "prices differ a lot, maybe two products").
-- Run once by hand on Postgres (Render) BEFORE deploying the backend that uses it,
-- after sql/2026-10-04-purchase-products.sql.
-- Additive: the previous backend keeps working with it.

BEGIN;

ALTER TABLE purchase_products
    ADD COLUMN IF NOT EXISTS suggested_merge_into_id VARCHAR(36)
        REFERENCES purchase_products(id) ON DELETE SET NULL;
ALTER TABLE purchase_products
    ADD COLUMN IF NOT EXISTS ai_note VARCHAR(255);

COMMIT;
