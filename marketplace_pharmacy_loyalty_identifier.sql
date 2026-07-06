BEGIN;

ALTER TABLE public.marketplace_orders
ADD COLUMN IF NOT EXISTS pharmacy_loyalty_identifier VARCHAR;

CREATE INDEX IF NOT EXISTS ix_marketplace_orders_pharmacy_loyalty_identifier
ON public.marketplace_orders(pharmacy_loyalty_identifier);

COMMIT;
