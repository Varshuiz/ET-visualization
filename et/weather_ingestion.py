import os

import numpy as np
import pandas as pd
import requests


def _enrich_observed_rn() -> bool:
    """Hourly ECCC net-radiation bulk fetch is slow; ET comparison estimates Rn from Rs."""
    return os.environ.get("ECCC_ENRICH_RN", "false").lower() in ("1", "true", "yes")

from .eccc_weather import add_eccc_rh_to_dataframe, add_eccc_rn_to_dataframe
from .eccc_weather import build_aquacrop_weather_from_eccc
from .et_methods import (
    calculate_extraterrestrial_radiation,
    net_radiation_estimate,
    priestley_taylor_ET,
)


def kmh_max_wind_to_u2_ms(wind_kmh_max):
    """
    Convert a daily maximum 10 m wind speed (km/h) to 2 m wind speed u2 (m/s),
    matching the Open-Meteo historical pipeline in this project.
    """
    if wind_kmh_max is None or pd.isna(wind_kmh_max):
        return None
    try:
        kmh = float(wind_kmh_max)
    except (TypeError, ValueError):
        return None
    if kmh < 0:
        return None
    u10_ms = kmh / 3.6
    return float(u10_ms * 0.748)


def fetch_openmeteo_historical_data(latitude, longitude, start_date, end_date):
    """
    Fetch historical daily weather with ECCC as primary source and Open-Meteo fallback.

    Returns a normalized dataframe with at least:
    Date, Tmax, Tmin, Precipitation, RH, u2/Wind_Speed (where available/defaulted).
    """
    from .weather_cache import (
        dataframe_from_cache_payload,
        dataframe_to_cache_payload,
        get_cached,
        set_cached,
        weather_cache_key,
    )

    cache_key = weather_cache_key(
        "historical_weather",
        lat=round(float(latitude), 4),
        lon=round(float(longitude), 4),
        start=str(start_date),
        end=str(end_date),
    )
    cached = get_cached(cache_key)
    if cached is not None:
        return dataframe_from_cache_payload(cached)

    df = _fetch_openmeteo_historical_data_uncached(latitude, longitude, start_date, end_date)
    if df is not None and not df.empty:
        set_cached(cache_key, dataframe_to_cache_payload(df))
    return df


def _fetch_openmeteo_historical_data_uncached(latitude, longitude, start_date, end_date):
    """Uncached implementation for historical weather (ECCC primary, Open-Meteo fallback)."""
    # Primary path: ECCC daily climate observations (+ built-in Open-Meteo temp gap-fill).
    try:
        eccc_df = build_aquacrop_weather_from_eccc(
            latitude=float(latitude),
            longitude=float(longitude),
            start_date=start_date,
            end_date=end_date,
        )
        df = pd.DataFrame(
            {
                "Date": pd.to_datetime(eccc_df["Date"], errors="coerce"),
                "Tmax": pd.to_numeric(eccc_df["MaxTemp"], errors="coerce"),
                "Tmin": pd.to_numeric(eccc_df["MinTemp"], errors="coerce"),
                "Precipitation": pd.to_numeric(eccc_df["Precipitation"], errors="coerce"),
            }
        )
        df = df.dropna(subset=["Date", "Tmax", "Tmin"]).reset_index(drop=True)
        if not df.empty:
            # RH and wind are not guaranteed in this ECCC-normalized path; fill via existing enrichment/defaults.
            df = add_eccc_rh_to_dataframe(df, latitude=latitude, longitude=longitude, prefer_eccc=True)
            if _enrich_observed_rn():
                df = add_eccc_rn_to_dataframe(df, latitude=latitude, longitude=longitude, prefer_eccc=True)
            df["RH"] = pd.to_numeric(df.get("RH"), errors="coerce").fillna(65.0)
            df["u2"] = 2.0
            df["Wind_Speed"] = df["u2"]
            df.attrs["historical_source"] = "ECCC primary (Open-Meteo temperature gap-fill when needed)"
            return df
    except Exception:
        # Fall through to Open-Meteo archive fallback.
        pass

    # Fallback path: Open-Meteo archive API.
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "shortwave_radiation_sum",
                "wind_speed_10m_max",
                "relative_humidity_2m_mean",
            ]
        ),
        "timezone": "auto",
    }

    response = requests.get(url, params=params, timeout=45)
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily", {})
    required = ["time", "temperature_2m_max", "temperature_2m_min"]
    missing = [k for k in required if k not in daily]
    if missing:
        raise ValueError(f"Open-Meteo response missing fields: {missing}")

    df = pd.DataFrame(
        {
            "Date": pd.to_datetime(daily.get("time", []), errors="coerce"),
            "Tmax": pd.to_numeric(daily.get("temperature_2m_max", []), errors="coerce"),
            "Tmin": pd.to_numeric(daily.get("temperature_2m_min", []), errors="coerce"),
            "Precipitation": pd.to_numeric(daily.get("precipitation_sum", []), errors="coerce"),
            "Solar_Radiation": pd.to_numeric(daily.get("shortwave_radiation_sum", []), errors="coerce"),
            "Wind_Speed_kmh": pd.to_numeric(daily.get("wind_speed_10m_max", []), errors="coerce"),
            "RH": pd.to_numeric(daily.get("relative_humidity_2m_mean", []), errors="coerce"),
        }
    )

    df = df.dropna(subset=["Date", "Tmax", "Tmin"]).reset_index(drop=True)
    if df.empty:
        raise ValueError("No usable daily records returned from Open-Meteo")

    df["u2"] = pd.to_numeric(
        df["Wind_Speed_kmh"].map(kmh_max_wind_to_u2_ms), errors="coerce"
    ).fillna(0.0)
    df["Wind_Speed"] = df["u2"]
    # Prefer ECCC RH where available for matching dates/stations.
    df = add_eccc_rh_to_dataframe(df, latitude=latitude, longitude=longitude, prefer_eccc=True)
    df = add_eccc_rn_to_dataframe(df, latitude=latitude, longitude=longitude, prefer_eccc=True)
    df["RH"] = df["RH"].fillna(65.0)
    df.attrs["historical_source"] = "Open-Meteo fallback"
    return df


def normalize_uploaded_weather_dataframe(df):
    """Normalize uploaded CSV weather data into expected ET columns."""
    if df is None or df.empty:
        raise ValueError("Uploaded file has no rows")

    rename_map = {
        "date": "Date",
        "day": "Date",
        "tmax": "Tmax",
        "temperature_max": "Tmax",
        "temperature_2m_max": "Tmax",
        "tmin": "Tmin",
        "temperature_min": "Tmin",
        "temperature_2m_min": "Tmin",
        "precip": "Precipitation",
        "precipitation": "Precipitation",
        "precipitation_sum": "Precipitation",
        "rh": "RH",
        "relative_humidity": "RH",
        "relative_humidity_avg": "RH",
        "wind_speed": "Wind_Speed",
        "u2": "u2",
        "solar_radiation": "Solar_Radiation",
        "rs": "Solar_Radiation",
        "shortwave_radiation_sum": "Solar_Radiation",
        "net_radiation": "Rn",
        "rn": "Rn",
        "rf4_net_radiation": "Rn",
    }

    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    df = df.rename(columns=rename_map)

    required = ["Date", "Tmax", "Tmin"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Uploaded CSV missing required columns: {missing}")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Tmax"] = pd.to_numeric(df["Tmax"], errors="coerce")
    df["Tmin"] = pd.to_numeric(df["Tmin"], errors="coerce")

    if "Precipitation" in df.columns:
        df["Precipitation"] = pd.to_numeric(df["Precipitation"], errors="coerce")
    else:
        df["Precipitation"] = 0.0

    if "RH" in df.columns:
        df["RH"] = pd.to_numeric(df["RH"], errors="coerce").fillna(65.0)
    else:
        df["RH"] = 65.0

    if "u2" in df.columns:
        df["u2"] = pd.to_numeric(df["u2"], errors="coerce")
        df["Wind_Speed"] = df["u2"].fillna(2.0)
    elif "Wind_Speed" in df.columns:
        df["Wind_Speed"] = pd.to_numeric(df["Wind_Speed"], errors="coerce").fillna(2.0)
        df["u2"] = df["Wind_Speed"]
    else:
        df["Wind_Speed"] = 2.0
        df["u2"] = 2.0

    if "Solar_Radiation" in df.columns:
        df["Solar_Radiation"] = pd.to_numeric(df["Solar_Radiation"], errors="coerce")

    if "Rn" in df.columns:
        df["Rn"] = pd.to_numeric(df["Rn"], errors="coerce")
        df["Rn_source"] = df["Rn"].where(df["Rn"].notna())
        df["Rn_source"] = df["Rn_source"].map(lambda _: "uploaded" if pd.notna(_) else None)

    df = df.dropna(subset=["Date", "Tmax", "Tmin"]).sort_values("Date").reset_index(drop=True)
    if df.empty:
        raise ValueError("Uploaded CSV has no valid Date/Tmax/Tmin records after cleaning")

    return df


def prepare_historical_weather_dataframe(
    df,
    latitude=None,
    longitude=None,
    elevation=766.0,
    prefer_eccc_rn=True,
):
    """Prepare a historical weather dataframe for ET calculations with optional ECCC Rn enrichment."""
    if df is None or df.empty:
        raise ValueError("Weather dataframe is empty")

    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out["Tmax"] = pd.to_numeric(out["Tmax"], errors="coerce")
    out["Tmin"] = pd.to_numeric(out["Tmin"], errors="coerce")
    out = out.dropna(subset=["Date", "Tmax", "Tmin"]).sort_values("Date").reset_index(drop=True)
    if out.empty:
        raise ValueError("Weather dataframe has no valid Date/Tmax/Tmin rows")

    if "Tavg" not in out.columns:
        out["Tavg"] = (out["Tmax"] + out["Tmin"]) / 2.0
    else:
        out["Tavg"] = pd.to_numeric(out["Tavg"], errors="coerce").fillna((out["Tmax"] + out["Tmin"]) / 2.0)

    out["day_of_year"] = out["Date"].dt.dayofyear
    lat_for_ra = float(latitude) if latitude is not None else 49.7
    out["Ra"] = out["day_of_year"].apply(lambda doy: calculate_extraterrestrial_radiation(lat_for_ra, int(doy)))

    if "Solar_Radiation" not in out.columns:
        temp_range = (out["Tmax"] - out["Tmin"]).clip(lower=1.0)
        out["Solar_Radiation"] = (0.16 * temp_range.pow(0.5) * out["Ra"]).clip(3, 40)
    else:
        out["Solar_Radiation"] = pd.to_numeric(out["Solar_Radiation"], errors="coerce")

    out["Rs"] = pd.to_numeric(out.get("Rs", out["Solar_Radiation"]), errors="coerce")
    out["Rs"] = out["Rs"].fillna(out["Solar_Radiation"])

    if "RH" not in out.columns:
        out["RH"] = 65.0
    out["RH"] = pd.to_numeric(out["RH"], errors="coerce").fillna(65.0)

    if "u2" in out.columns:
        out["u2"] = pd.to_numeric(out["u2"], errors="coerce")
    elif "Wind_Speed" in out.columns:
        out["u2"] = pd.to_numeric(out["Wind_Speed"], errors="coerce")
    else:
        out["u2"] = 2.0
    out["u2"] = out["u2"].fillna(2.0)
    out["Wind_Speed"] = out["u2"]

    if "Rn" in out.columns:
        out["Rn"] = pd.to_numeric(out["Rn"], errors="coerce")
    if "Rn_source" not in out.columns:
        out["Rn_source"] = None

    if latitude is not None and longitude is not None and prefer_eccc_rn:
        out = add_eccc_rn_to_dataframe(out, latitude=float(latitude), longitude=float(longitude), prefer_eccc=True)

    out["Rn_estimated"] = out.apply(
        lambda row: net_radiation_estimate(row["Rs"], row["Tmax"], row["Tmin"], row["Ra"], row["RH"], elevation=elevation),
        axis=1,
    )
    if "Rn" not in out.columns:
        out["Rn"] = out["Rn_estimated"]
    else:
        out["Rn"] = pd.to_numeric(out["Rn"], errors="coerce").combine_first(out["Rn_estimated"])
    out.loc[out["Rn_source"].isna() & out["Rn"].notna(), "Rn_source"] = "estimated"
    return out


def build_aquacrop_weather_from_openmeteo_forecast(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    elevation: float = 766.0,
    max_forecast_days: int = 16,
) -> pd.DataFrame:
    """
    Build AquaCrop daily weather (MinTemp, MaxTemp, Precipitation, ReferenceET, Date)
    from Open-Meteo **forecast** API.

    The public forecast endpoint typically returns at most ~16 days ahead from the
    model run time; long growing-season windows are not supported in this mode.
    """
    start_ts = pd.to_datetime(start_date, errors="coerce").normalize()
    end_ts = pd.to_datetime(end_date, errors="coerce").normalize()
    if pd.isna(start_ts) or pd.isna(end_ts):
        raise ValueError("Invalid start or end date for forecast weather")
    if end_ts < start_ts:
        raise ValueError("End date must be on or after start date")

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "UTC",
        "forecast_days": max_forecast_days,
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "shortwave_radiation_sum",
                "relative_humidity_2m_mean",
            ]
        ),
    }
    response = requests.get(url, params=params, timeout=45)
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily", {})
    required = ["time", "temperature_2m_max", "temperature_2m_min"]
    missing = [k for k in required if k not in daily]
    if missing:
        raise ValueError(f"Open-Meteo forecast response missing fields: {missing}")

    df = pd.DataFrame(
        {
            "Date": pd.to_datetime(daily.get("time", []), errors="coerce"),
            "Tmax": pd.to_numeric(daily.get("temperature_2m_max", []), errors="coerce"),
            "Tmin": pd.to_numeric(daily.get("temperature_2m_min", []), errors="coerce"),
            "Precipitation": pd.to_numeric(daily.get("precipitation_sum", []), errors="coerce"),
            "Solar_Radiation": pd.to_numeric(
                daily.get("shortwave_radiation_sum", []), errors="coerce"
            ),
            "RH": pd.to_numeric(daily.get("relative_humidity_2m_mean", []), errors="coerce"),
        }
    )
    df = df.dropna(subset=["Date", "Tmax", "Tmin"]).sort_values("Date").reset_index(drop=True)
    if df.empty:
        raise ValueError("Open-Meteo forecast returned no usable daily rows")

    df["Date"] = df["Date"].dt.normalize()
    df = df.loc[(df["Date"] >= start_ts) & (df["Date"] <= end_ts)].reset_index(drop=True)
    if df.empty:
        raise ValueError(
            "No forecast days overlap your simulation window. "
            "Forecast mode only supports dates within the next ~16 days of the model run."
        )

    expected_days = int((end_ts - start_ts).days) + 1
    if len(df) < expected_days:
        raise ValueError(
            f"Forecast weather covers {len(df)} day(s) but the simulation needs {expected_days} day(s). "
            "Shorten the date range or use historical / single-year mode."
        )

    df["Precipitation"] = df["Precipitation"].fillna(0.0).clip(lower=0.0)
    df["RH"] = df["RH"].fillna(65.0)

    df["day_of_year"] = df["Date"].dt.dayofyear.astype(int)
    df["Ra"] = df["day_of_year"].apply(
        lambda doy: calculate_extraterrestrial_radiation(float(latitude), int(doy))
    )
    df["Tavg"] = (df["Tmax"] + df["Tmin"]) / 2.0
    temp_range = (df["Tmax"] - df["Tmin"]).clip(lower=0.5)
    df["Rs"] = pd.to_numeric(df["Solar_Radiation"], errors="coerce")
    df["Rs"] = df["Rs"].where(
        df["Rs"].notna() & (df["Rs"] > 0),
        0.16 * np.sqrt(temp_range) * df["Ra"],
    )

    df["Rn"] = df.apply(
        lambda row: net_radiation_estimate(
            row["Rs"],
            row["Tmax"],
            row["Tmin"],
            row["Ra"],
            row["RH"],
            elevation=elevation,
        ),
        axis=1,
    )
    df["ReferenceET"] = df.apply(
        lambda row: priestley_taylor_ET(row["Tavg"], row["Rn"]),
        axis=1,
    ).clip(lower=0.01)

    weather_df = pd.DataFrame(
        {
            "MinTemp": df["Tmin"],
            "MaxTemp": df["Tmax"],
            "Precipitation": df["Precipitation"],
            "ReferenceET": df["ReferenceET"],
            "Date": df["Date"],
        }
    )
    warnings = [
        "Forecast mode uses Open-Meteo daily forecast drivers (≤16-day horizon). "
        "Results are indicative only and should not replace operational planning."
    ]
    weather_df.attrs["warnings"] = warnings
    weather_df.attrs["temperature_source_summary"] = {
        "eccc_days": 0,
        "openmeteo_gapfill_days": 0,
        "remaining_missing_days": 0,
        "forecast_mode": True,
    }
    return weather_df
