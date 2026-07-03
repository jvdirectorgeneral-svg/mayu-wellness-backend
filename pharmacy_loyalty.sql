CREATE TABLE IF NOT EXISTS public.pharmacy_customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR NOT NULL,
    email VARCHAR NOT NULL UNIQUE,
    password VARCHAR NOT NULL,
    phone VARCHAR NOT NULL,
    cedula VARCHAR UNIQUE,
    city VARCHAR,
    address VARCHAR,
    reference VARCHAR,
    delivery_notes TEXT,
    accepted_terms BOOLEAN NOT NULL DEFAULT FALSE,
    accepted_privacy_policy BOOLEAN NOT NULL DEFAULT FALSE,
    accepted_digital_policy BOOLEAN NOT NULL DEFAULT FALSE,
    accepted_terms_at TIMESTAMP WITHOUT TIME ZONE,
    accepted_privacy_policy_at TIMESTAMP WITHOUT TIME ZONE,
    accepted_digital_policy_at TIMESTAMP WITHOUT TIME ZONE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.pharmacy_loyalty_cards (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES public.users(id) ON DELETE CASCADE,
    pharmacy_customer_id INTEGER UNIQUE
        REFERENCES public.pharmacy_customers(id) ON DELETE CASCADE,
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
