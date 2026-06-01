"""Django cache helpers for ECCC / Open-Meteo weather API responses."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
from django.core.cache import cache

WEATHER_CACHE_TTL = int(os.environ.get("WEATHER_CACHE_TTL_SECONDS", "3600"))


def weather_cache_key(prefix: str, **parts: Any) -> str:
    normalized = "|".join(f"{k}={parts[k]}" for k in sorted(parts))
    return f"weather:{prefix}:{normalized}"


def get_cached(key: str):
    return cache.get(key)


def set_cached(key: str, value, ttl: int | None = None) -> None:
    cache.set(key, value, ttl if ttl is not None else WEATHER_CACHE_TTL)


def dataframe_to_cache_payload(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"records": [], "attrs": {}}
    out = df.copy()
    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return {"records": out.to_dict(orient="records"), "attrs": dict(getattr(df, "attrs", {}))}


def dataframe_from_cache_payload(payload: dict | None) -> pd.DataFrame:
    if not payload or not payload.get("records"):
        return pd.DataFrame()
    df = pd.DataFrame(payload["records"])
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for key, value in (payload.get("attrs") or {}).items():
        df.attrs[key] = value
    return df
