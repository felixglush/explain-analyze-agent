-- tests/fixtures/sample_schema.sql
-- Sample schema for local end-to-end testing of sql_reviewer.
-- Load with: psql $DATABASE_URL -f tests/fixtures/sample_schema.sql

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email    ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_active   ON users (is_active) WHERE is_active = true;

CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending',
    total_cents INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders (user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders (status);

CREATE TABLE IF NOT EXISTS order_items (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 1,
    unit_cents INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id);
