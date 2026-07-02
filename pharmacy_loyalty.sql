CREATE TABLE IF NOT EXISTS public.pharmacy_loyalty_cards (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES public.users(id) ON DELETE CASCADE,
    card_code VARCHAR NOT NULL UNIQUE,
    qr_token VARCHAR NOT NULL UNIQUE,
    points_balance INTEGER NOT NULL DEFAULT 0,
    accumulated_cents INTEGER NOT NULL DEFAULT 0,
    lifetime_points INTEGER NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.pharmacy_points_transactions (
    id SERIAL PRIMARY KEY,
    card_id INTEGER NOT NULL
        REFERENCES public.pharmacy_loyalty_cards(id) ON DELETE CASCADE,
    marketplace_order_id INTEGER UNIQUE
        REFERENCES public.marketplace_orders(id) ON DELETE SET NULL,
    purchase_amount_cents INTEGER NOT NULL,
    points_delta INTEGER NOT NULL,
    remainder_after_cents INTEGER NOT NULL,
    source VARCHAR NOT NULL,
    reference VARCHAR UNIQUE,
    note TEXT,
    created_by INTEGER REFERENCES public.users(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_pharmacy_points_transactions_card_id
    ON public.pharmacy_points_transactions (card_id);
