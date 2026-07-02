CREATE TABLE IF NOT EXISTS marketplace_order_tracking_history (
    id SERIAL PRIMARY KEY,
    marketplace_order_id INTEGER NOT NULL
        REFERENCES marketplace_orders(id) ON DELETE CASCADE,
    status VARCHAR NOT NULL,
    note TEXT,
    carrier VARCHAR,
    tracking_number VARCHAR,
    tracking_url TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_marketplace_order_tracking_history_order_id
    ON marketplace_order_tracking_history (marketplace_order_id);

CREATE INDEX IF NOT EXISTS ix_marketplace_order_tracking_history_status
    ON marketplace_order_tracking_history (status);
