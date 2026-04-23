from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd
import requests


ECCC_CLIMATE_DAILY_URL = "https://api.weather.gc.ca/collections/climate-daily/items"
ECCC_TIMEOUT_SECONDS = 6
ECCC_MAX_RANGE_DAYS = 180


def _build_bbox(longitude: float, latitude: float, radius_deg: float = 0.3) -> str:
    return f"{longitude - radius_deg},{latitude - radius_deg},{longitude + radius_deg},{latitude + radius_deg}"


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
