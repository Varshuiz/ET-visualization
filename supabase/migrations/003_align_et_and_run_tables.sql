-- Run in Supabase SQL Editor if the app reports missing columns (PGRST204 / 42703).
-- Then: Project Settings → API → Reload schema (or wait ~1 minute).

-- et_calculations (app expects these columns — skip any you already have)
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS farm_id UUID REFERENCES public.farms(id) ON DELETE SET NULL;
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS et_method TEXT;
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS province TEXT;
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS city TEXT;
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS date_range_start DATE;
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS date_range_end DATE;
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS result_data JSONB NOT NULL DEFAULT '{}';
ALTER TABLE public.et_calculations
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- aquacrop_runs (start_date/end_date are DATE in Supabase)
ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS farm_id UUID REFERENCES public.farms(id) ON DELETE SET NULL;
ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS mode TEXT;
ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS crop_type TEXT;
ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS start_date DATE;
ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS end_date DATE;
ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS result_data JSONB DEFAULT '{}';
ALTER TABLE public.aquacrop_runs
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

-- forecast_runs
ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS farm_id UUID REFERENCES public.farms(id) ON DELETE SET NULL;
ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS province TEXT;
ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS city TEXT;
ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS forecast_days INTEGER;
ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS et_method TEXT;
ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS result_data JSONB NOT NULL DEFAULT '{}';
ALTER TABLE public.forecast_runs
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- usage_logs
ALTER TABLE public.usage_logs
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
ALTER TABLE public.usage_logs
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- farms (if created without updated_at — app no longer requires it)
ALTER TABLE public.farms
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
