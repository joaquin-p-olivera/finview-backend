-- Purchase products: cart items point to a product ("Agua 6L"), and each
-- product has a category. Aliases map the normalized names the user types
-- ("limones") to a product.
-- Run once by hand on Postgres (Render) BEFORE deploying the backend that uses it.
-- Additive: the previous backend keeps working with it.

BEGIN;

CREATE TABLE IF NOT EXISTS purchase_products (
    id              VARCHAR(36)   PRIMARY KEY,
    user_id         VARCHAR(36)   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(255)  NOT NULL,
    category_id     VARCHAR(36)   REFERENCES purchase_categories(id) ON DELETE SET NULL,
    category_source VARCHAR(10),
    size_value      NUMERIC(10,3),
    size_unit       VARCHAR(10),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_purchase_products_user_id ON purchase_products (user_id);
CREATE INDEX IF NOT EXISTS ix_purchase_products_category_id ON purchase_products (category_id);
-- One name per user, ignoring case and surrounding spaces.
CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_product_name
    ON purchase_products (user_id, lower(btrim(name)));

CREATE TABLE IF NOT EXISTS purchase_product_aliases (
    id         VARCHAR(36)  PRIMARY KEY,
    user_id    VARCHAR(36)  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id VARCHAR(36)  NOT NULL REFERENCES purchase_products(id) ON DELETE CASCADE,
    alias_key  VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_purchase_product_alias UNIQUE (user_id, alias_key)
);

CREATE INDEX IF NOT EXISTS ix_purchase_product_aliases_product_id
    ON purchase_product_aliases (product_id);

ALTER TABLE purchase_cart_items
    ADD COLUMN IF NOT EXISTS product_id VARCHAR(36)
        REFERENCES purchase_products(id) ON DELETE SET NULL;
ALTER TABLE purchase_cart_items
    ADD COLUMN IF NOT EXISTS categorized_by VARCHAR(10);

CREATE INDEX IF NOT EXISTS ix_purchase_cart_items_product_id ON purchase_cart_items (product_id);

ALTER TABLE purchase_categories
    ADD COLUMN IF NOT EXISTS created_by_ai BOOLEAN NOT NULL DEFAULT false;

COMMIT;
