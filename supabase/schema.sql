-- Run in Supabase SQL Editor to create tables and RLS policies for AqualET.
-- Service role key bypasses RLS for server-side Django; policies protect direct API access.

-- Profiles (extends auth.users)
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    full_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Farm details per user
CREATE TABLE IF NOT EXISTS public.farms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    farm_name TEXT NOT NULL,
    province TEXT NOT NULL,
    city TEXT NOT NULL,
    area_hectares NUMERIC(12, 4),
    crop_type TEXT,
    irrigation_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS farms_user_id_idx ON public.farms(user_id);

-- ET calculation runs (matches Supabase table editor layout)
CREATE TABLE IF NOT EXISTS public.et_calculations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    farm_id UUID REFERENCES public.farms(id) ON DELETE SET NULL,
    et_method TEXT,
    province TEXT,
    city TEXT,
    date_range_start DATE,
    date_range_end DATE,
    result_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS et_calculations_user_id_idx ON public.et_calculations(user_id);

-- AquaCrop simulation runs (matches Supabase table editor layout)
CREATE TABLE IF NOT EXISTS public.aquacrop_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.profiles(id) ON DELETE CASCADE,
    farm_id UUID REFERENCES public.farms(id) ON DELETE SET NULL,
    mode TEXT,
    crop_type TEXT,
    start_date DATE,
    end_date DATE,
    result_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS aquacrop_runs_user_id_idx ON public.aquacrop_runs(user_id);

-- Forecast runs
CREATE TABLE IF NOT EXISTS public.forecast_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    farm_id UUID REFERENCES public.farms(id) ON DELETE SET NULL,
    province TEXT,
    city TEXT,
    forecast_days INTEGER,
    et_method TEXT,
    result_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS forecast_runs_user_id_idx ON public.forecast_runs(user_id);

-- Feature usage audit log
CREATE TABLE IF NOT EXISTS public.usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    feature TEXT NOT NULL,
    action TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS usage_logs_user_id_idx ON public.usage_logs(user_id);

-- Row Level Security
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.farms ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.et_calculations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.aquacrop_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forecast_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_logs ENABLE ROW LEVEL SECURITY;

-- Profiles: users read/update own row
CREATE POLICY profiles_select_own ON public.profiles FOR SELECT
    USING (auth.uid() = id);
CREATE POLICY profiles_insert_own ON public.profiles FOR INSERT
    WITH CHECK (auth.uid() = id);
CREATE POLICY profiles_update_own ON public.profiles FOR UPDATE
    USING (auth.uid() = id);

-- Farms
CREATE POLICY farms_select_own ON public.farms FOR SELECT
    USING (auth.uid() = user_id);
CREATE POLICY farms_insert_own ON public.farms FOR INSERT
    WITH CHECK (auth.uid() = user_id);
CREATE POLICY farms_update_own ON public.farms FOR UPDATE
    USING (auth.uid() = user_id);
CREATE POLICY farms_delete_own ON public.farms FOR DELETE
    USING (auth.uid() = user_id);

-- ET calculations
CREATE POLICY et_calculations_select_own ON public.et_calculations FOR SELECT
    USING (auth.uid() = user_id);
CREATE POLICY et_calculations_insert_own ON public.et_calculations FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- AquaCrop runs
CREATE POLICY aquacrop_runs_select_own ON public.aquacrop_runs FOR SELECT
    USING (auth.uid() = user_id);
CREATE POLICY aquacrop_runs_insert_own ON public.aquacrop_runs FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- Forecast runs
CREATE POLICY forecast_runs_select_own ON public.forecast_runs FOR SELECT
    USING (auth.uid() = user_id);
CREATE POLICY forecast_runs_insert_own ON public.forecast_runs FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- Usage logs
CREATE POLICY usage_logs_select_own ON public.usage_logs FOR SELECT
    USING (auth.uid() = user_id);
CREATE POLICY usage_logs_insert_own ON public.usage_logs FOR INSERT
    WITH CHECK (auth.uid() = user_id);
