BEGIN;

ALTER TABLE public.doctor_commission_transactions
ADD COLUMN IF NOT EXISTS payout_status VARCHAR NOT NULL DEFAULT 'pending',
ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP WITHOUT TIME ZONE,
ADD COLUMN IF NOT EXISTS paid_by INTEGER REFERENCES public.users(id),
ADD COLUMN IF NOT EXISTS payout_note TEXT,
ADD COLUMN IF NOT EXISTS gross_commission_cents INTEGER,
ADD COLUMN IF NOT EXISTS deduction_bps INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS deduction_cents INTEGER NOT NULL DEFAULT 0;

UPDATE public.doctor_commission_transactions
SET gross_commission_cents = commission_cents
WHERE gross_commission_cents IS NULL;

CREATE INDEX IF NOT EXISTS ix_doctor_commission_transactions_payout_status
ON public.doctor_commission_transactions(payout_status);

COMMIT;
