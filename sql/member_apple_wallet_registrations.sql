CREATE TABLE IF NOT EXISTS public.member_apple_wallet_registrations (
    id SERIAL PRIMARY KEY,
    card_id INTEGER NOT NULL REFERENCES public.member_cards(id) ON DELETE CASCADE,
    device_library_identifier VARCHAR NOT NULL,
    pass_type_identifier VARCHAR NOT NULL,
    serial_number VARCHAR NOT NULL,
    push_token TEXT NOT NULL,
    authentication_token VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_member_apple_wallet_registrations_card_id
ON public.member_apple_wallet_registrations(card_id);

CREATE INDEX IF NOT EXISTS ix_member_apple_wallet_registrations_device_library_identifier
ON public.member_apple_wallet_registrations(device_library_identifier);

CREATE INDEX IF NOT EXISTS ix_member_apple_wallet_registrations_pass_type_identifier
ON public.member_apple_wallet_registrations(pass_type_identifier);

CREATE INDEX IF NOT EXISTS ix_member_apple_wallet_registrations_serial_number
ON public.member_apple_wallet_registrations(serial_number);

CREATE INDEX IF NOT EXISTS ix_member_apple_wallet_registrations_authentication_token
ON public.member_apple_wallet_registrations(authentication_token);
