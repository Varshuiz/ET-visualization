-- Soil type on saved locations (AquaCrop-aligned labels).

ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS soil_type TEXT;
