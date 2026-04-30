import pandas as pd
import requests

from .eccc_weather import add_eccc_rh_to_dataframe, add_eccc_rn_to_dataframe
from .et_methods import calculate_extraterrestrial_radiation, net_radiation_estimate


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
    """Fetch historical daily weather data from Open-Meteo archive API."""
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
