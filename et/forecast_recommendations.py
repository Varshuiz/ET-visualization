import base64
from datetime import date, timedelta
from io import BytesIO

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests


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


def fetch_openmeteo_archive_daily(latitude, longitude, start_date, end_date):
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "auto",
    }
    response = requests.get(url, params=params, timeout=45)
    response.raise_for_status()
    daily = response.json().get("daily", {})
    df = pd.DataFrame(
        {
            "Date": pd.to_datetime(daily.get("time", []), errors="coerce"),
            "Tmax": pd.to_numeric(daily.get("temperature_2m_max", []), errors="coerce"),
            "Tmin": pd.to_numeric(daily.get("temperature_2m_min", []), errors="coerce"),
            "Precipitation": pd.to_numeric(daily.get("precipitation_sum", []), errors="coerce").fillna(0.0),
        }
    )
    return df.dropna(subset=["Date", "Tmax", "Tmin"]).reset_index(drop=True)


def safe_year_shift_date(base_date, year):
    try:
        return base_date.replace(year=year)
    except ValueError:
        return (base_date - timedelta(days=1)).replace(year=year)


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
                    et0 = daily_et_func(float(row["Tmax"]), float(row["Tmin"]), p_lat, day_of_year)
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

    p10 = np.percentile(matrix, 10, axis=0)
    p50 = np.percentile(matrix, 50, axis=0)
    p90 = np.percentile(matrix, 90, axis=0)
    return {
        "days": list(range(1, max_len + 1)),
        "p10": p10.tolist(),
        "p50": p50.tolist(),
        "p90": p90.tolist(),
        "scenario_count": int(matrix.shape[0]),
        "total_p10": float(p10[-1]),
        "total_p50": float(p50[-1]),
        "total_p90": float(p90[-1]),
    }


def build_irrigation_confidence_plot(hist_conf, forecast_curve):
    if not hist_conf or not forecast_curve:
        return None

    n = min(len(hist_conf["days"]), len(forecast_curve))
    days = hist_conf["days"][:n]
    p10 = hist_conf["p10"][:n]
    p50 = hist_conf["p50"][:n]
    p90 = hist_conf["p90"][:n]
    fcurve = forecast_curve[:n]

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.fill_between(days, p10, p90, color="#c9ced1", alpha=0.65, label="Historical confidence range")
    ax.plot(days, p50, color="#666666", linestyle="--", linewidth=1.8, label="Historical median")
    ax.plot(days, fcurve, color="#0b5f66", linewidth=2.2, label="Current forecast recommendation")
    ax.set_xlabel("Forecast day")
    ax.set_ylabel("Cumulative irrigation recommendation (mm)")
    ax.set_title("Irrigation recommendation with 5-year confidence band")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", fontsize=8)
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
