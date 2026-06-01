import base64
from datetime import date, timedelta
from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

from .weather_ingestion import kmh_max_wind_to_u2_ms
from .weather_ingestion import fetch_openmeteo_historical_data


CROP_GDD_PROFILES = {
    "wheat": [
        (180, 0.65, "Early establishment"),
        (550, 0.90, "Vegetative growth"),
        (950, 1.08, "Mid-season growth"),
        (1300, 1.18, "Peak water demand"),
        (99999, 0.85, "Late season / maturity"),
    ],
    "canola": [
        (160, 0.62, "Early establishment"),
        (520, 0.92, "Vegetative growth"),
        (900, 1.10, "Flowering and pod set"),
        (1200, 1.16, "Peak water demand"),
        (99999, 0.86, "Late season / maturity"),
    ],
    "corn": [
        (200, 0.55, "Emergence and early growth"),
        (650, 0.92, "Vegetative growth"),
        (1100, 1.16, "Tasseling and silking"),
        (1600, 1.22, "Peak water demand"),
        (99999, 0.90, "Late season / maturity"),
    ],
    "barley": [
        (170, 0.62, "Early establishment"),
        (520, 0.88, "Vegetative growth"),
        (900, 1.05, "Heading and grain fill"),
        (1200, 1.12, "Peak water demand"),
        (99999, 0.84, "Late season / maturity"),
    ],
    "oats": [
        (170, 0.63, "Early establishment"),
        (540, 0.89, "Vegetative growth"),
        (930, 1.06, "Panicle and grain fill"),
        (1220, 1.13, "Peak water demand"),
        (99999, 0.85, "Late season / maturity"),
    ],
    "soybean": [
        (190, 0.60, "Early establishment"),
        (620, 0.90, "Vegetative growth"),
        (1020, 1.12, "Flowering and pod fill"),
        (1400, 1.18, "Peak water demand"),
        (99999, 0.88, "Late season / maturity"),
    ],
    "potato": [
        (180, 0.68, "Emergence"),
        (520, 0.96, "Canopy development"),
        (900, 1.18, "Tuber initiation and bulking"),
        (1300, 1.24, "Peak water demand"),
        (99999, 0.92, "Maturation"),
    ],
    "dry_bean": [
        (180, 0.60, "Early establishment"),
        (580, 0.92, "Vegetative growth"),
        (980, 1.10, "Flowering and pod fill"),
        (1320, 1.16, "Peak water demand"),
        (99999, 0.86, "Late season / maturity"),
    ],
    "alfalfa": [
        (150, 0.78, "Early regrowth"),
        (450, 1.00, "Canopy build"),
        (850, 1.12, "Active growth"),
        (1200, 1.18, "Peak water demand"),
        (99999, 0.98, "Late growth"),
    ],
    "sugar_beet": [
        (170, 0.65, "Emergence"),
        (520, 0.95, "Canopy development"),
        (950, 1.16, "Root bulking"),
        (1400, 1.22, "Peak water demand"),
        (99999, 0.90, "Late season / maturity"),
    ],
}

SOIL_IRRIGATION_FACTORS = {
    "loam": 1.00,
    "sandy": 1.12,
    "clay": 0.90,
    "sandy_loam": 1.08,
    "silt_loam": 0.98,
    "clay_loam": 0.93,
    "silty_clay": 0.89,
    "sandy_clay_loam": 0.96,
    "peat": 0.94,
    "gravelly": 1.15,
}


def safe_temp_convert(value):
    if value is None or pd.isna(value):
        return None
    try:
        value_float = float(value)
        if np.isnan(value_float):
            return None
        return value_float
    except (ValueError, TypeError):
        return None


def calculate_daily_gdd(tmax, tmin, base_temp=5.0):
    tmean = (tmax + tmin) / 2.0
    return max(tmean - base_temp, 0.0)


def gdd_stage_factor(cumulative_gdd, crop_type):
    profile = CROP_GDD_PROFILES.get(crop_type, CROP_GDD_PROFILES["wheat"])
    for threshold, factor, label in profile:
        if cumulative_gdd < threshold:
            return factor, label
    return profile[-1][1], profile[-1][2]


def _daily_series_aligned(daily, key, n):
    """Return a length-*n* list for a daily field (pad / trim) so DataFrame construction never fails."""
    raw = daily.get(key)
    if raw is None:
        return [np.nan] * n
    if not isinstance(raw, (list, tuple)):
        return [np.nan] * n
    out = list(raw)
    if len(out) < n:
        out = out + [np.nan] * (n - len(out))
    elif len(out) > n:
        out = out[:n]
    return out


def fetch_openmeteo_archive_daily(latitude, longitude, start_date, end_date):
    """
    Historical daily data for forecast confidence:
    prefer ECCC-backed historical weather, fallback to Open-Meteo handled internally.
    """
    base = fetch_openmeteo_historical_data(latitude, longitude, start_date, end_date)
    if base is None or base.empty:
        return pd.DataFrame(
            columns=["Date", "Tmax", "Tmin", "Precipitation", "Wind_kmh_max", "RH_hist", "Rs_mjm2", "u2"]
        )

    df = base.copy()
    df["Date"] = pd.to_datetime(df.get("Date"), errors="coerce")
    df["Tmax"] = pd.to_numeric(df.get("Tmax"), errors="coerce")
    df["Tmin"] = pd.to_numeric(df.get("Tmin"), errors="coerce")
    df["Precipitation"] = pd.to_numeric(df.get("Precipitation"), errors="coerce").fillna(0.0)
    df["u2"] = pd.to_numeric(df.get("u2"), errors="coerce")
    if "Wind_kmh_max" not in df.columns:
        # Best-effort reverse transform for display/compatibility when only u2 exists.
        df["Wind_kmh_max"] = df["u2"] * (3.6 / 0.748)
    df["RH_hist"] = pd.to_numeric(df.get("RH"), errors="coerce")
    # Keep existing radiation when available; if absent, downstream logic estimates ET from temperature.
    df["Rs_mjm2"] = pd.to_numeric(df.get("Rs_mjm2", df.get("Solar_Radiation")), errors="coerce")
    return df.dropna(subset=["Date", "Tmax", "Tmin"]).reset_index(drop=True)


def merge_openmeteo_forecast_drivers(df, latitude, longitude, timeout=30):
    """
    Enrich forecast rows using Open-Meteo *forecast* API at the city coordinates:

    - wind_speed_10m_max → u₂ (after km/h → m/s conversion) when MSC wind is missing
    - relative_humidity_2m_mean → RH_percent when ECCC humidity is missing
    - shortwave_radiation_sum → Rs_mjm2 (MJ/m²/day) for Penman–Monteith (measured Rs)
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    for col in ("Wind_kmh_max", "u2_ms", "RH_percent", "Rs_mjm2"):
        if col not in out.columns:
            out[col] = np.nan

    mask_u2 = out["u2_ms"].isna() & out["Wind_kmh_max"].notna()
    out.loc[mask_u2, "u2_ms"] = out.loc[mask_u2, "Wind_kmh_max"].map(kmh_max_wind_to_u2_ms)

    need_u2 = out["u2_ms"].isna().any()
    need_rh = out["RH_percent"].isna().any()
    need_rs = out["Rs_mjm2"].isna().any()
    if not (need_u2 or need_rh or need_rs):
        return out

    dates = pd.to_datetime(out["Date"], errors="coerce")
    if dates.isna().all():
        return out
    start = dates.min().strftime("%Y-%m-%d")
    end = dates.max().strftime("%Y-%m-%d")
    daily = _fetch_openmeteo_forecast_daily(latitude, longitude, start, end, timeout=timeout)
    tlist = daily.get("time", [])
    if not tlist:
        return out
    wlist = daily.get("wind_speed_10m_max") or []
    rhlist = daily.get("relative_humidity_2m_mean") or []
    rslist = daily.get("shortwave_radiation_sum") or []

    n = len(tlist)

    def _take(series, i):
        if i < len(series):
            return series[i]
        return None

    wind_by_date = {}
    rh_by_date = {}
    rs_by_date = {}
    for i, t in enumerate(tlist):
        d = pd.to_datetime(t, errors="coerce")
        if pd.isna(d):
            continue
        key = d.normalize()
        wv = _take(wlist, i)
        if wv is not None:
            try:
                wind_by_date[key] = float(wv)
            except (TypeError, ValueError):
                pass
        rhv = _take(rhlist, i)
        if rhv is not None:
            try:
                rh_by_date[key] = float(rhv)
            except (TypeError, ValueError):
                pass
        rsv = _take(rslist, i)
        if rsv is not None:
            try:
                rs_by_date[key] = float(rsv)
            except (TypeError, ValueError):
                pass

    dnorm = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    fill_kmh = dnorm.map(wind_by_date)
    fill_u2 = fill_kmh.map(kmh_max_wind_to_u2_ms)
    still_u2 = out["u2_ms"].isna()
    out.loc[still_u2, "u2_ms"] = fill_u2.loc[still_u2]
    still_w = still_u2 & out["Wind_kmh_max"].isna()
    out.loc[still_w, "Wind_kmh_max"] = fill_kmh.loc[still_w]

    fill_rh = dnorm.map(rh_by_date)
    still_rh = out["RH_percent"].isna()
    out.loc[still_rh, "RH_percent"] = fill_rh.loc[still_rh]

    fill_rs = dnorm.map(rs_by_date)
    still_rs = out["Rs_mjm2"].isna()
    out.loc[still_rs, "Rs_mjm2"] = fill_rs.loc[still_rs]
    return out


def _fetch_openmeteo_forecast_daily(latitude, longitude, start, end, timeout=30):
    """Fetch Open-Meteo daily forecast drivers with a 1-hour Django cache."""
    from .weather_cache import get_cached, set_cached, weather_cache_key

    cache_key = weather_cache_key(
        "openmeteo_forecast_daily",
        lat=round(float(latitude), 4),
        lon=round(float(longitude), 4),
        start=start,
        end=end,
    )
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "start_date": start,
        "end_date": end,
        "daily": "wind_speed_10m_max,relative_humidity_2m_mean,shortwave_radiation_sum",
        "timezone": "auto",
    }
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        daily = r.json().get("daily", {}) or {}
    except Exception:
        daily = {}

    if daily.get("time"):
        set_cached(cache_key, daily)
    return daily


def merge_openmeteo_forecast_wind(df, latitude, longitude, timeout=25):
    """Backward-compatible alias for merge_openmeteo_forecast_drivers."""
    return merge_openmeteo_forecast_drivers(df, latitude, longitude, timeout=timeout)


def safe_year_shift_date(base_date, year):
    try:
        return base_date.replace(year=year)
    except ValueError:
        return (base_date - timedelta(days=1)).replace(year=year)


def _widen_irrigation_confidence_envelope(p10, p90, min_tail_growth=1.28):
    """
    Widen the P10–P90 spread along the horizon so the shaded band visibly grows
    toward later days when raw percentiles are too flat (display clarity).

    Cumulative irrigation: lower edge ≈ wetter historical analogues, upper ≈ drier.
    """
    lo = np.minimum(np.asarray(p10, dtype=float), np.asarray(p90, dtype=float))
    hi = np.maximum(np.asarray(p10, dtype=float), np.asarray(p90, dtype=float))
    spread = hi - lo
    n = int(spread.size)
    if n <= 1:
        return lo.tolist(), hi.tolist()
    target_end = max(float(spread[-1]), float(spread[0]) * min_tail_growth)
    ramp = float(spread[0]) + (target_end - float(spread[0])) * np.linspace(0.0, 1.0, n)
    new_spread = np.maximum(spread, ramp)
    mid = (lo + hi) / 2.0
    out_lo = mid - new_spread / 2.0
    out_hi = mid + new_spread / 2.0
    out_lo = np.maximum(out_lo, 0.0)
    out_hi = np.maximum(out_hi, out_lo + 1e-9)
    return out_lo.tolist(), out_hi.tolist()


def build_historical_confidence(city_name, forecast_days, crop_type, resolve_city_lat_lon, daily_et_func):
    lat, lon = resolve_city_lat_lon(city_name)
    sample_points = [
        (lat, lon),
        (lat + 0.25, lon),
        (lat - 0.25, lon),
        (lat, lon + 0.25),
        (lat, lon - 0.25),
    ]

    today = date.today()
    scenario_curves = []

    for years_back in range(1, 6):
        hist_year = today.year - years_back
        hist_start = safe_year_shift_date(today, hist_year)
        hist_end = hist_start + timedelta(days=forecast_days - 1)

        for p_lat, p_lon in sample_points:
            try:
                hdf = fetch_openmeteo_archive_daily(
                    p_lat,
                    p_lon,
                    hist_start.strftime("%Y-%m-%d"),
                    hist_end.strftime("%Y-%m-%d"),
                )
                if len(hdf) < max(3, forecast_days // 2):
                    continue

                running_irrig = 0.0
                cumulative_gdd = 0.0
                cumulative_irrig = []
                for _, row in hdf.head(forecast_days).iterrows():
                    day_of_year = pd.to_datetime(row["Date"]).dayofyear
                    rh_val = row.get("RH_hist")
                    if rh_val is None or pd.isna(rh_val):
                        rh_val = 65.0
                    else:
                        rh_val = float(rh_val)
                    u2_val = row.get("u2")
                    if u2_val is None or pd.isna(u2_val):
                        u2_val = None
                    else:
                        u2_val = float(u2_val)
                    rs_val = row.get("Rs_mjm2")
                    if rs_val is None or pd.isna(rs_val):
                        rs_val = None
                    else:
                        rs_val = float(rs_val)
                    et0 = daily_et_func(
                        float(row["Tmax"]),
                        float(row["Tmin"]),
                        p_lat,
                        day_of_year,
                        rh=rh_val,
                        u2=u2_val,
                        rs=rs_val,
                    )
                    cumulative_gdd += calculate_daily_gdd(float(row["Tmax"]), float(row["Tmin"]))
                    stage_factor, _ = gdd_stage_factor(cumulative_gdd, crop_type)
                    adjusted_et = et0 * stage_factor
                    irrig_req = max(adjusted_et - max(float(row["Precipitation"]), 0.0), 0.0)
                    running_irrig += irrig_req
                    cumulative_irrig.append(running_irrig)

                if cumulative_irrig:
                    scenario_curves.append(cumulative_irrig)
            except Exception:
                continue

    if not scenario_curves:
        return None

    max_len = min(forecast_days, max(len(c) for c in scenario_curves))
    matrix = np.array([c[:max_len] for c in scenario_curves if len(c) >= max_len], dtype=float)
    if matrix.size == 0:
        return None

    p10_raw = np.percentile(matrix, 10, axis=0)
    p50 = np.percentile(matrix, 50, axis=0)
    p90_raw = np.percentile(matrix, 90, axis=0)
    p10, p90 = _widen_irrigation_confidence_envelope(p10_raw, p90_raw)
    return {
        "days": list(range(1, max_len + 1)),
        "p10": p10,
        "p50": p50.tolist(),
        "p90": p90,
        "scenario_count": int(matrix.shape[0]),
        "total_p10": float(p10[-1]),
        "total_p50": float(p50[-1]),
        "total_p90": float(p90[-1]),
    }


def build_irrigation_confidence_plot(hist_conf, forecast_curve):
    """
    Return base64-encoded PNG. If historical confidence is missing, still plot the
    forecast cumulative curve alone so the page is never left without a figure.
    """
    try:
        fc = list(forecast_curve or [])
    except TypeError:
        fc = []
    if len(fc) == 0:
        return None

    legend_kw = {"loc": "upper left", "fontsize": 8, "shadow": False, "fancybox": False}
    buffer = BytesIO()
    try:
        hist_ok = (
            hist_conf
            and isinstance(hist_conf, dict)
            and hist_conf.get("days")
            and len(hist_conf["days"]) > 0
        )
        if hist_ok:
            n = min(len(hist_conf["days"]), len(fc))
            if n <= 0:
                return None
            days = hist_conf["days"][:n]
            p10 = hist_conf["p10"][:n]
            p50 = hist_conf["p50"][:n]
            p90 = hist_conf["p90"][:n]
            fcurve = fc[:n]
            fig, ax = plt.subplots(figsize=(8, 4.2))
            ax.fill_between(
                days,
                p10,
                p90,
                color="#c9ced1",
                alpha=0.65,
                label="Historical P10–P90 (cumulative irrigation)",
            )
            ax.plot(
                days,
                p50,
                color="#666666",
                linestyle="--",
                linewidth=1.8,
                label="Historical median (P50)",
            )
            ax.plot(
                days,
                fcurve,
                color="#0b5f66",
                linewidth=2.4,
                label="This forecast (cumulative)",
            )
            ax.set_xlabel("Forecast day")
            ax.set_ylabel("Cumulative irrigation recommendation (mm)")
            ax.set_title(
                "Cumulative irrigation vs historical analogues\n"
                "(shaded band: wetter lower edge → drier upper edge; width grows with horizon)"
            )
            ax.grid(True, alpha=0.25)
            ax.legend(**legend_kw)
        else:
            days = list(range(1, len(fc) + 1))
            fig, ax = plt.subplots(figsize=(8, 4.2))
            ax.plot(days, fc, color="#0b5f66", linewidth=2.4, label="This forecast (cumulative)")
            ax.set_xlabel("Forecast day")
            ax.set_ylabel("Cumulative irrigation recommendation (mm)")
            ax.set_title(
                "Cumulative irrigation (this forecast only)\n"
                "(historical P10–P90 band unavailable for this run)"
            )
            ax.grid(True, alpha=0.25)
            ax.legend(**legend_kw)

        fig.tight_layout()
        fig.savefig(buffer, format="png", dpi=130, bbox_inches="tight")
        buffer.seek(0)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception:
        return None
    finally:
        plt.close("all")
