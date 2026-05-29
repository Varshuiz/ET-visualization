-- Optional: run if your farms table was created without updated_at
ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
