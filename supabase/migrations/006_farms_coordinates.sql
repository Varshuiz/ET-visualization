-- Optional coordinate-based locations on saved regions.

ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS latitude NUMERIC(10, 7);

ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS longitude NUMERIC(10, 7);

ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS location_input_mode TEXT NOT NULL DEFAULT 'city';
