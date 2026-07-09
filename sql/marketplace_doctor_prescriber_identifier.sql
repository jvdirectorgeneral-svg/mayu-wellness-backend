BEGIN;

ALTER TABLE public.marketplace_orders
ADD COLUMN IF NOT EXISTS doctor_prescriber_identifier VARCHAR;

CREATE INDEX IF NOT EXISTS ix_marketplace_orders_doctor_prescriber_identifier
ON public.marketplace_orders(doctor_prescriber_identifier);

COMMIT;
