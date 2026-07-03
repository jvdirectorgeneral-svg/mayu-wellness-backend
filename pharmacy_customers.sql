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

ALTER TABLE public.pharmacy_loyalty_cards
    ADD COLUMN IF NOT EXISTS pharmacy_customer_id INTEGER
    REFERENCES public.pharmacy_customers(id) ON DELETE CASCADE;

ALTER TABLE public.pharmacy_loyalty_cards
    ALTER COLUMN user_id DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pharmacy_loyalty_cards_customer
    ON public.pharmacy_loyalty_cards(pharmacy_customer_id)
    WHERE pharmacy_customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_pharmacy_customers_email
    ON public.pharmacy_customers(email);
