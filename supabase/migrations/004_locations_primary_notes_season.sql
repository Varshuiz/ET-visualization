-- Multiple locations (farms table), primary flag, season table persistence, run notes.

ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS season_table_data JSONB NOT NULL DEFAULT '{}';

ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS crop_condition TEXT;

CREATE INDEX IF NOT EXISTS farms_user_primary_idx ON public.farms(user_id, is_primary);

ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS note TEXT;

ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS note TEXT;

ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS note TEXT;
