from __future__ import annotations

import io
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

from .et_methods import (
    calculate_extraterrestrial_radiation,
    net_radiation_estimate,
    priestley_taylor_ET,
)


ECCC_CLIMATE_DAILY_URL = "https://api.weather.gc.ca/collections/climate-daily/items"
ECCC_CLIMATE_STATIONS_URL = "https://api.weather.gc.ca/collections/climate-stations/items"
ECCC_CLIMATE_HOURLY_BULK_URL = "https://climate.weather.gc.ca/climate_data/bulk_data_e.html"
ECCC_TIMEOUT_SECONDS = 6
ECCC_MAX_RANGE_DAYS = 180
ECCC_RN_STATION_LIMIT = 50
ECCC_RN_CANDIDATE_LIMIT = 8
ECCC_RN_SEARCH_RADIUS_DEG = 1.0


def _build_bbox(longitude: float, latitude: float, radius_deg: float = 0.3) -> str:
    return f"{longitude - radius_deg},{latitude - radius_deg},{longitude + radius_deg},{latitude + radius_deg}"


def _normalize_station_coordinate(raw_value) -> float | None:
    value = pd.to_numeric(raw_value, errors="coerce")
    if pd.isna(value):
        return None
    value = float(value)
    if abs(value) > 1000:
        value /= 1e7
    return value


def _distance_km(latitude: float, longitude: float, station_lat: float, station_lon: float) -> float:
    # Good-enough local approximation for debugging output.
    mean_lat = np.radians((latitude + station_lat) / 2.0)
    dlat_km = (station_lat - latitude) * 111.32
    dlon_km = (station_lon - longitude) * (111.32 * np.cos(mean_lat))
    return float(np.sqrt(dlat_km**2 + dlon_km**2))


@lru_cache(maxsize=64)
def _fetch_eccc_daily_features(bbox: str, start_date: str, end_date: str) -> list[dict]:
    params = {
        "bbox": bbox,
        "datetime": f"{start_date}/{end_date}",
        "f": "json",
        "limit": 2500,
    }
    response = requests.get(ECCC_CLIMATE_DAILY_URL, params=params, timeout=ECCC_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    return payload.get("features", [])


@lru_cache(maxsize=64)
def _fetch_eccc_station_features(bbox: str) -> list[dict]:
    params = {
        "bbox": bbox,
        "f": "json",
        "HAS_HOURLY_DATA": "Y",
        "limit": ECCC_RN_STATION_LIMIT,
    }
    response = requests.get(ECCC_CLIMATE_STATIONS_URL, params=params, timeout=ECCC_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    return payload.get("features", [])


@lru_cache(maxsize=512)
def _fetch_eccc_bulk_hourly_csv(station_id: int, year: int, month: int) -> str:
    params = {
        "format": "csv",
        "stationID": str(station_id),
        "Year": str(year),
        "Month": str(month),
        "timeframe": "1",
    }
    response = requests.get(ECCC_CLIMATE_HOURLY_BULK_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.text


def _find_rn_column(columns: list[str]) -> str | None:
    for col in columns:
        norm = str(col).strip().lower()
        if ("net" in norm and "radiation" in norm) or "rf4" in norm:
            return col
    return None


def _find_datetime_column(columns: list[str]) -> str | None:
    for col in columns:
        norm = str(col).strip().lower()
        if "date/time" in norm or ("date" in norm and "time" in norm):
            return col
    return None


def _daily_rn_from_bulk_hourly(station_id: int, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    monthly_frames: list[pd.DataFrame] = []
    for month_start in pd.date_range(start_ts.normalize().replace(day=1), end_ts.normalize().replace(day=1), freq="MS"):
        try:
            csv_text = _fetch_eccc_bulk_hourly_csv(int(station_id), int(month_start.year), int(month_start.month))
        except Exception:
            continue
        try:
            monthly_df = pd.read_csv(io.StringIO(csv_text))
        except Exception:
            continue
        if monthly_df.empty:
            continue
        rn_col = _find_rn_column(list(monthly_df.columns))
        dt_col = _find_datetime_column(list(monthly_df.columns))
        if not rn_col or not dt_col:
            continue
        work = monthly_df[[dt_col, rn_col]].copy()
        work["DateTime"] = pd.to_datetime(work[dt_col], errors="coerce")
        work["Rn_raw"] = pd.to_numeric(work[rn_col], errors="coerce")
        work = work.dropna(subset=["DateTime", "Rn_raw"])
        if work.empty:
            continue
        col_norm = rn_col.lower()
        if "w/m2" in col_norm or "w/m²" in col_norm:
            work["Rn_hourly"] = work["Rn_raw"] * 0.0036
        else:
            work["Rn_hourly"] = work["Rn_raw"]
        work["Date"] = work["DateTime"].dt.normalize()
        monthly_frames.append(
            work.groupby("Date", as_index=False)["Rn_hourly"].sum().rename(columns={"Rn_hourly": "Rn_ECCC"})
        )
    if not monthly_frames:
        return pd.DataFrame(columns=["Date", "Rn_ECCC"])
    merged = pd.concat(monthly_frames, ignore_index=True)
    merged = merged.groupby("Date", as_index=False)["Rn_ECCC"].sum()
    mask = (merged["Date"] >= start_ts.normalize()) & (merged["Date"] <= end_ts.normalize())
    return merged.loc[mask].reset_index(drop=True)


def _candidate_rn_stations(latitude: float, longitude: float, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> list[dict]:
    bbox = _build_bbox(float(longitude), float(latitude), radius_deg=ECCC_RN_SEARCH_RADIUS_DEG)
    try:
        features = _fetch_eccc_station_features(bbox)
    except Exception:
        return []
    stations = []
    inspected = []
    for feat in features:
        props = feat.get("properties", {})
        station_name = str(props.get("STATION_NAME", ""))
        station_id_raw = pd.to_numeric(props.get("STN_ID"), errors="coerce")
        has_hourly = str(props.get("HAS_HOURLY_DATA", "")).upper() == "Y"
        hly_first = pd.to_datetime(props.get("HLY_FIRST_DATE"), errors="coerce")
        hly_last = pd.to_datetime(props.get("HLY_LAST_DATE"), errors="coerce")
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [None, None])
        lon = _normalize_station_coordinate(coords[0] if coords else props.get("LONGITUDE"))
        lat = _normalize_station_coordinate(coords[1] if coords else props.get("LATITUDE"))
        if lat is None or lon is None:
            lat = _normalize_station_coordinate(props.get("LATITUDE"))
            lon = _normalize_station_coordinate(props.get("LONGITUDE"))
        if pd.isna(station_id_raw) or lat is None or lon is None:
            inspected.append(
                {
                    "station_id": None if pd.isna(station_id_raw) else int(station_id_raw),
                    "station_name": station_name,
                    "distance_km": None,
                    "rejected_reason": "missing station id or coordinates",
                }
            )
            continue
        station_id = int(station_id_raw)
        distance_sq = (float(lat) - float(latitude)) ** 2 + (float(lon) - float(longitude)) ** 2
        distance_km = _distance_km(float(latitude), float(longitude), float(lat), float(lon))

        rejected_reason = None
        if not has_hourly:
            rejected_reason = "no hourly data"
        elif pd.notna(hly_first) and start_ts < hly_first.normalize():
            rejected_reason = f"start date before station hourly period ({hly_first.date()})"
        elif pd.notna(hly_last) and end_ts > hly_last.normalize():
            rejected_reason = f"end date after station hourly period ({hly_last.date()})"

        inspected.append(
            {
                "station_id": station_id,
                "station_name": station_name,
                "distance_km": distance_km,
                "rejected_reason": rejected_reason or "accepted",
            }
        )
        if rejected_reason:
            continue
        stations.append(
            {
                "station_id": station_id,
                "station_name": station_name,
                "distance_sq": distance_sq,
                "distance_km": distance_km,
            }
        )
    stations_sorted = sorted(stations, key=lambda row: row["distance_sq"])[:ECCC_RN_CANDIDATE_LIMIT]

    if not stations_sorted:
        radius_km_lat = ECCC_RN_SEARCH_RADIUS_DEG * 111.32
        radius_km_lon = ECCC_RN_SEARCH_RADIUS_DEG * 111.32 * np.cos(np.radians(float(latitude)))
        print("[ECCC_RN_DEBUG] No station candidates returned.")
        print(f"[ECCC_RN_DEBUG] Search input lat/lon: {float(latitude):.6f}, {float(longitude):.6f}")
        print(
            f"[ECCC_RN_DEBUG] Search radius: {ECCC_RN_SEARCH_RADIUS_DEG} deg "
            f"(~{radius_km_lat:.1f} km N/S, ~{abs(radius_km_lon):.1f} km E/W)"
        )
        print(f"[ECCC_RN_DEBUG] Station list size fetched before filtering: {len(inspected)}")
        print("[ECCC_RN_DEBUG] Province filtering before distance check: none (bbox-only query)")
        if inspected:
            closest = sorted(
                [row for row in inspected if row["distance_km"] is not None],
                key=lambda row: row["distance_km"],
            )
            if closest:
                nearest = closest[0]
                print(
                    "[ECCC_RN_DEBUG] Closest station (even if rejected): "
                    f"{nearest['station_id']} | {nearest['station_name']} | "
                    f"{nearest['distance_km']:.2f} km | reason={nearest['rejected_reason']}"
                )
            print("[ECCC_RN_DEBUG] Full station list before filtering:")
            for row in inspected:
                d_text = "n/a" if row["distance_km"] is None else f"{row['distance_km']:.2f} km"
                print(
                    f"[ECCC_RN_DEBUG] station={row['station_id']} | name={row['station_name']} | "
                    f"distance={d_text} | status={row['rejected_reason']}"
                )
            print("[ECCC_RN_DEBUG] 5 closest stations by distance (regardless of filter):")
            for row in closest[:5]:
                print(
                    f"[ECCC_RN_DEBUG] closest station={row['station_id']} | name={row['station_name']} | "
                    f"distance={row['distance_km']:.2f} km | status={row['rejected_reason']}"
                )

    return stations_sorted


def add_eccc_rn_to_dataframe(df: pd.DataFrame, latitude: float, longitude: float, prefer_eccc: bool = True) -> pd.DataFrame:
    """
    Enrich daily data with observed ECCC net radiation where a nearby hourly station exposes it.
    """
    if df is None or df.empty or "Date" not in df.columns:
        return df

    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out.dropna(subset=["Date"]).copy()
    if out.empty:
        return out

    start_ts = out["Date"].min()
    end_ts = out["Date"].max()
    if (end_ts - start_ts).days > ECCC_MAX_RANGE_DAYS:
        return out

    existing_rn = pd.to_numeric(out["Rn"], errors="coerce") if "Rn" in out.columns else pd.Series(np.nan, index=out.index)
    if "Rn_source" not in out.columns:
        out["Rn_source"] = np.where(existing_rn.notna(), "existing", None)

    best_daily = pd.DataFrame(columns=["Date", "Rn_ECCC"])
    best_station_name = None
    best_station_id = None
    best_count = 0

    for candidate in _candidate_rn_stations(float(latitude), float(longitude), start_ts, end_ts):
        daily_rn = _daily_rn_from_bulk_hourly(candidate["station_id"], start_ts, end_ts)
        count = int(daily_rn["Rn_ECCC"].notna().sum()) if not daily_rn.empty else 0
        if count > best_count:
            best_daily = daily_rn
            best_station_name = candidate["station_name"]
            best_station_id = candidate["station_id"]
            best_count = count
        if count >= max(3, int(len(out) * 0.5)):
            break

    if best_daily.empty or best_count == 0:
        return out

    out["DateKey"] = out["Date"].dt.normalize()
    best_daily = best_daily.rename(columns={"Date": "DateKey"})
    out = out.merge(best_daily, on="DateKey", how="left")
    eccc_rn = pd.to_numeric(out["Rn_ECCC"], errors="coerce")
    current_rn = pd.to_numeric(out["Rn"], errors="coerce") if "Rn" in out.columns else pd.Series(np.nan, index=out.index)
    if prefer_eccc:
        out["Rn"] = eccc_rn.combine_first(current_rn)
    else:
        out["Rn"] = current_rn.combine_first(eccc_rn)
    out["Rn_station_name"] = best_station_name
    out["Rn_station_id"] = best_station_id
    observed_mask = eccc_rn.notna()
    out.loc[observed_mask, "Rn_source"] = "ECCC_RF4"

    drop_cols = [c for c in ["DateKey", "Rn_ECCC"] if c in out.columns]
    if drop_cols:
        out = out.drop(columns=drop_cols)
    return out


def add_eccc_rh_to_dataframe(df: pd.DataFrame, latitude: float, longitude: float, prefer_eccc: bool = True) -> pd.DataFrame:
    """
    Enrich RH using ECCC climate-daily observations near selected coordinates.
    """
    if df is None or df.empty or "Date" not in df.columns:
        return df

    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out.dropna(subset=["Date"]).copy()
    if out.empty:
        return out

    # Fast path: if RH already exists and mostly populated, skip remote enrichment.
    if "RH" in out.columns:
        existing_rh = pd.to_numeric(out["RH"], errors="coerce")
        if existing_rh.notna().mean() >= 0.9:
            out["RH"] = existing_rh
            return out

    start_date = out["Date"].min().strftime("%Y-%m-%d")
    end_date = out["Date"].max().strftime("%Y-%m-%d")
    if (out["Date"].max() - out["Date"].min()).days > ECCC_MAX_RANGE_DAYS:
        return out
    bbox = _build_bbox(float(longitude), float(latitude))

    try:
        features = _fetch_eccc_daily_features(bbox, start_date, end_date)
    except Exception:
        return out

    if not features:
        return out

    rows = []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [None, None])
        lon = pd.to_numeric(coords[0], errors="coerce")
        lat = pd.to_numeric(coords[1], errors="coerce")
        date_val = pd.to_datetime(props.get("LOCAL_DATE"), errors="coerce")
        rh_min = pd.to_numeric(props.get("MIN_REL_HUMIDITY"), errors="coerce")
        rh_max = pd.to_numeric(props.get("MAX_REL_HUMIDITY"), errors="coerce")

        if pd.isna(date_val) or pd.isna(lon) or pd.isna(lat):
            continue

        if not pd.isna(rh_min) and not pd.isna(rh_max):
            rh = (rh_min + rh_max) / 2.0
        elif not pd.isna(rh_max):
            rh = rh_max
        elif not pd.isna(rh_min):
            rh = rh_min
        else:
            rh = np.nan

        rows.append(
            {
                "Date": date_val.normalize(),
                "RH_ECCC": rh,
                "distance_sq": (lat - float(latitude)) ** 2 + (lon - float(longitude)) ** 2,
            }
        )

    if not rows:
        return out

    eccc_df = pd.DataFrame(rows).dropna(subset=["RH_ECCC"])
    if eccc_df.empty:
        return out

    # Pick nearest reporting station per day.
    eccc_daily = (
        eccc_df.sort_values(["Date", "distance_sq"])
        .groupby("Date", as_index=False)
        .first()[["Date", "RH_ECCC"]]
    )

    out["DateKey"] = out["Date"].dt.normalize()
    eccc_daily = eccc_daily.rename(columns={"Date": "DateKey"})
    out = out.merge(eccc_daily, on="DateKey", how="left")
    if "RH" not in out.columns:
        out["RH"] = out["RH_ECCC"]
    elif prefer_eccc:
        out["RH"] = out["RH_ECCC"].combine_first(pd.to_numeric(out["RH"], errors="coerce"))
    else:
        out["RH"] = pd.to_numeric(out["RH"], errors="coerce").combine_first(out["RH_ECCC"])

    # Cleanup merge helper columns.
    drop_cols = [c for c in ["DateKey", "RH_ECCC"] if c in out.columns]
    if drop_cols:
        out = out.drop(columns=drop_cols)
    return out


def build_aquacrop_weather_from_eccc(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    elevation: float = 766.0,
) -> pd.DataFrame:
    """
    Build AquaCrop weather dataframe from ECCC climate-daily observations.
    Output columns: MinTemp, MaxTemp, Precipitation, ReferenceET, Date
    """
    start_ts = pd.to_datetime(start_date, errors="coerce")
    end_ts = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(start_ts) or pd.isna(end_ts):
        raise ValueError("Invalid date range for ECCC weather fetch")
    if end_ts < start_ts:
        raise ValueError("End date must be after start date")

    bbox = _build_bbox(float(longitude), float(latitude))
    features = _fetch_eccc_daily_features(
        bbox,
        start_ts.strftime("%Y-%m-%d"),
        end_ts.strftime("%Y-%m-%d"),
    )
    if not features:
        raise ValueError("No ECCC weather observations available for the selected period/location")

    rows = []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [None, None])
        lon = pd.to_numeric(coords[0], errors="coerce")
        lat = pd.to_numeric(coords[1], errors="coerce")
        date_val = pd.to_datetime(props.get("LOCAL_DATE"), errors="coerce")
        tmax = pd.to_numeric(props.get("MAX_TEMPERATURE"), errors="coerce")
        tmin = pd.to_numeric(props.get("MIN_TEMPERATURE"), errors="coerce")
        precip = pd.to_numeric(props.get("TOTAL_PRECIPITATION"), errors="coerce")
        rh_min = pd.to_numeric(props.get("MIN_REL_HUMIDITY"), errors="coerce")
        rh_max = pd.to_numeric(props.get("MAX_REL_HUMIDITY"), errors="coerce")

        if pd.isna(date_val) or pd.isna(lat) or pd.isna(lon) or pd.isna(tmax) or pd.isna(tmin):
            continue

        if not pd.isna(rh_min) and not pd.isna(rh_max):
            rh = (rh_min + rh_max) / 2.0
        elif not pd.isna(rh_max):
            rh = rh_max
        elif not pd.isna(rh_min):
            rh = rh_min
        else:
            rh = np.nan

        rows.append(
            {
                "Date": date_val.normalize(),
                "Tmax": float(tmax),
                "Tmin": float(tmin),
                "Precipitation": max(float(precip), 0.0) if not pd.isna(precip) else 0.0,
                "RH": rh,
                "distance_sq": (float(lat) - float(latitude)) ** 2 + (float(lon) - float(longitude)) ** 2,
            }
        )

    if not rows:
        raise ValueError("ECCC returned no usable daily rows for AquaCrop weather")

    df = pd.DataFrame(rows)
    df = (
        df.sort_values(["Date", "distance_sq"])
        .groupby("Date", as_index=False)
        .first()
        .sort_values("Date")
        .reset_index(drop=True)
    )

    full_dates = pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")
    df = pd.DataFrame({"Date": full_dates}).merge(df, on="Date", how="left")
    if df["Tmax"].isna().any() or df["Tmin"].isna().any():
        raise ValueError("ECCC data has missing daily temperature values in selected range")

    df["RH"] = pd.to_numeric(df["RH"], errors="coerce")
    if df["RH"].isna().any():
        # Fill missing RH from nearby days (still ECCC-derived, no synthetic constants).
        df["RH"] = df["RH"].interpolate(limit_direction="both")
    if df["RH"].isna().any():
        raise ValueError("ECCC data has missing RH values that could not be resolved")

    df["day_of_year"] = df["Date"].dt.dayofyear
    df["Ra"] = df.apply(
        lambda row: calculate_extraterrestrial_radiation(float(latitude), int(row["day_of_year"])),
        axis=1,
    )
    df["Tavg"] = (df["Tmax"] + df["Tmin"]) / 2.0
    df["Rs"] = 0.16 * np.sqrt((df["Tmax"] - df["Tmin"]).clip(lower=0.5)) * df["Ra"]
    df["Rn"] = df.apply(
        lambda row: net_radiation_estimate(
            row["Rs"], row["Tmax"], row["Tmin"], row["Ra"], row["RH"], elevation=elevation
        ),
        axis=1,
    )
    df["ReferenceET"] = df.apply(
        lambda row: priestley_taylor_ET(row["Tavg"], row["Rn"]),
        axis=1,
    ).clip(lower=0)

    weather_df = pd.DataFrame(
        {
            "MinTemp": df["Tmin"],
            "MaxTemp": df["Tmax"],
            "Precipitation": df["Precipitation"].fillna(0.0),
            "ReferenceET": df["ReferenceET"],
            "Date": df["Date"],
        }
    )
    return weather_df
