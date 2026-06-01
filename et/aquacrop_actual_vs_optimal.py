"""Actual vs optimal comparison charts for AquaCrop + farmer season data."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .aquacrop_aggregation import compute_yield_tha
from .aquacrop_season_data import effective_irrigation_mm
from .aquacrop_simulator import run_aquacrop_simulation


def _float(val, default=0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def build_irrigation_schedule(
    management_rows: list[dict],
    application_efficiency_pct: float = 81.0,
) -> pd.DataFrame:
    """Weekly effective irrigation applied on each week-start date (mm)."""
    rows = []
    for m in management_rows or []:
        eff = _float(m.get("effective_irrigation"))
        if eff <= 0:
            eff = effective_irrigation_mm(
                _float(m.get("gross_irrigation")),
                application_efficiency_pct,
            )
        if eff > 0:
            rows.append(
                {
                    "Date": pd.to_datetime(m.get("week_start"), errors="coerce"),
                    "Depth": eff,
                }
            )
    if not rows:
        return pd.DataFrame(columns=["Date", "Depth"])
    df = pd.DataFrame(rows).dropna(subset=["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def expand_season_to_daily(
    weather_rows: list[dict],
    management_rows: list[dict],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Daily farmer-reported series (precip, ref ET, irrigation, soil moisture %)."""
    start = pd.to_datetime(start_date.replace("/", "-"), errors="coerce").normalize()
    end = pd.to_datetime(end_date.replace("/", "-"), errors="coerce").normalize()
    if pd.isna(start) or pd.isna(end) or end < start:
        return pd.DataFrame()

    dates = pd.date_range(start, end, freq="D")
    w_by_week = {str(r.get("week_start")): r for r in (weather_rows or [])}
    m_by_week = {str(r.get("week_start")): r for r in (management_rows or [])}

    week_starts = sorted(
        {
            pd.to_datetime(k, errors="coerce")
            for k in set(list(w_by_week.keys()) + list(m_by_week.keys()))
            if k
        }
    )
    week_starts = [t for t in week_starts if pd.notna(t)]

    def week_key_for(d: pd.Timestamp) -> str | None:
        if not week_starts:
            return None
        eligible = [ws for ws in week_starts if ws.normalize() <= d.normalize()]
        if not eligible:
            return week_starts[0].strftime("%Y-%m-%d")
        return eligible[-1].strftime("%Y-%m-%d")

    records = []
    for d in dates:
        wk = week_key_for(d)
        w = w_by_week.get(wk, {}) if wk else {}
        m = m_by_week.get(wk, {}) if wk else {}
        precip_w = _float(w.get("precipitation"))
        ref_et_w = _float(w.get("reference_et"))
        eff_irr_w = _float(m.get("effective_irrigation"))
        if eff_irr_w <= 0:
            eff_irr_w = effective_irrigation_mm(_float(m.get("gross_irrigation")), 100.0)
        sm = m.get("soil_moisture")
        sm_val = _float(sm) if sm not in (None, "") else np.nan
        records.append(
            {
                "Date": d,
                "precipitation_mm": precip_w / 7.0 if precip_w else 0.0,
                "reference_et_mm": ref_et_w / 7.0 if ref_et_w else 0.0,
                "irrigation_mm": eff_irr_w / 7.0 if eff_irr_w else 0.0,
                "soil_moisture_pct": sm_val,
                "week_start": wk,
            }
        )

    df = pd.DataFrame(records)
    if df["soil_moisture_pct"].isna().all():
        df["soil_moisture_pct"] = df["soil_moisture_pct"].fillna(0.0)
    else:
        df["soil_moisture_pct"] = df["soil_moisture_pct"].interpolate(limit_direction="both").fillna(0.0)
    return df


def _aggregate_daily_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    df = daily.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    start = df["Date"].min().normalize()
    df["week_idx"] = ((df["Date"] - start).dt.days // 7).astype(int)
    agg = (
        df.groupby("week_idx", as_index=False)
        .agg(
            week_start=("Date", "min"),
            precipitation_mm=("precipitation_mm", "sum"),
            reference_et_mm=("reference_et_mm", "sum"),
            irrigation_mm=("irrigation_mm", "sum"),
            soil_moisture_pct=("soil_moisture_pct", "mean"),
        )
        .sort_values("week_idx")
    )
    agg["week_label"] = agg["week_start"].dt.strftime("%b %d")
    return agg


def _weekly_biomass_from_daily(daily_df: pd.DataFrame, start_date: str) -> list[dict]:
    if daily_df is None or daily_df.empty or "biomass" not in daily_df.columns:
        return []
    df = daily_df.copy()
    if "Date" not in df.columns:
        start_dt = pd.to_datetime(start_date.replace("/", "-"), errors="coerce")
        if pd.isna(start_dt):
            return []
        df["Date"] = pd.date_range(start_dt, periods=len(df), freq="D")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    start_dt = df["Date"].min().normalize()
    df["week_idx"] = ((df["Date"] - start_dt).dt.days // 7).astype(int)
    out = []
    for wk, grp in df.groupby("week_idx", sort=True):
        bmax = float(grp["biomass"].max())
        ws = grp["Date"].min()
        out.append(
            {
                "week_idx": int(wk),
                "week_start": ws.strftime("%Y-%m-%d"),
                "week_label": ws.strftime("%b %d"),
                "biomass_tha": round(bmax, 3),
            }
        )
    return out


def _align_biomass_weeks(optimal_weeks: list[dict], actual_weeks: list[dict]) -> dict[str, Any]:
    by_key = {w["week_start"]: w for w in optimal_weeks}
    labels, optimal, actual = [], [], []
    all_keys = sorted(set(by_key.keys()) | {w["week_start"] for w in actual_weeks})
    actual_by = {w["week_start"]: w for w in actual_weeks}
    for key in all_keys:
        o = by_key.get(key, {})
        a = actual_by.get(key, {})
        labels.append(o.get("week_label") or a.get("week_label") or key)
        optimal.append(o.get("biomass_tha", 0))
        actual.append(a.get("biomass_tha", 0))
    return {"labels": labels, "optimal_biomass": optimal, "actual_biomass": actual}


def _series_to_chart(daily: pd.DataFrame, weekly: pd.DataFrame) -> dict[str, Any]:
    daily_labels = [d.strftime("%Y-%m-%d") for d in daily["Date"]]
    weekly_labels = weekly["week_label"].tolist() if not weekly.empty else []
    return {
        "daily": {
            "labels": daily_labels,
            "precipitation": [round(float(v), 2) for v in daily["precipitation_mm"]],
            "reference_et": [round(float(v), 2) for v in daily["reference_et_mm"]],
            "irrigation": [round(float(v), 2) for v in daily["irrigation_mm"]],
            "soil_moisture": [round(float(v), 1) for v in daily["soil_moisture_pct"]],
        },
        "weekly": {
            "labels": weekly_labels,
            "precipitation": [round(float(v), 1) for v in weekly["precipitation_mm"]] if not weekly.empty else [],
            "reference_et": [round(float(v), 1) for v in weekly["reference_et_mm"]] if not weekly.empty else [],
            "irrigation": [round(float(v), 1) for v in weekly["irrigation_mm"]] if not weekly.empty else [],
            "soil_moisture": [round(float(v), 1) for v in weekly["soil_moisture_pct"]] if not weekly.empty else [],
        },
    }


def build_actual_vs_optimal_payload(
    *,
    optimal_results: dict,
    weather_rows: list[dict],
    management_rows: list[dict],
    crop: str,
    soil: str,
    start_date: str,
    end_date: str,
    weather_df: pd.DataFrame | None,
    application_efficiency_pct: float = 81.0,
) -> dict[str, Any]:
    """
    Build JSON-serializable chart payloads comparing AquaCrop optimal vs farmer irrigation.
    """
    daily_farmer = expand_season_to_daily(weather_rows, management_rows, start_date, end_date)
    if daily_farmer.empty:
        raise ValueError("Could not build daily season series from farmer tables.")

    weekly_farmer = _aggregate_daily_to_weekly(daily_farmer)
    flux_chart = _series_to_chart(daily_farmer, weekly_farmer)

    optimal_daily = optimal_results.get("daily_output")
    optimal_weeks = _weekly_biomass_from_daily(optimal_daily, start_date)

    schedule = build_irrigation_schedule(
        management_rows, application_efficiency_pct=application_efficiency_pct
    )
    actual_results = None
    if weather_df is not None and not weather_df.empty:
        actual_results = run_aquacrop_simulation(
            crop=crop,
            soil=soil,
            start_date=start_date,
            end_date=end_date,
            irrigation="rainfed",
            weather_df=weather_df,
            irrigation_schedule=schedule if not schedule.empty else None,
        )

    actual_weeks = (
        _weekly_biomass_from_daily(actual_results.get("daily_output"), start_date)
        if actual_results
        else []
    )
    biomass_chart = _align_biomass_weeks(optimal_weeks, actual_weeks)

    total_precip = sum(_float(r.get("precipitation")) for r in weather_rows)
    total_gross = sum(_float(r.get("gross_irrigation")) for r in management_rows)
    total_effective = sum(
        _float(r.get("effective_irrigation"))
        or effective_irrigation_mm(_float(r.get("gross_irrigation")), application_efficiency_pct)
        for r in management_rows
    )
    total_runoff = sum(_float(r.get("runoff")) for r in management_rows)

    optimal_biomass_final = float(optimal_results.get("biomass") or 0)
    actual_biomass_final = (
        float(actual_results.get("biomass") or 0) if actual_results else 0.0
    )
    optimal_yield = compute_yield_tha(
        float(optimal_results.get("yield_dry") or optimal_biomass_final), crop
    )
    actual_yield = (
        compute_yield_tha(float(actual_results.get("yield_dry") or actual_biomass_final), crop)
        if actual_results
        else 0.0
    )

    from .aquacrop_aggregation import build_weekly_yield_comparison

    weekly_yield_comparison = build_weekly_yield_comparison(
        optimal_results.get("daily_output"),
        crop,
        start_date,
        actual_daily_df=actual_results.get("daily_output") if actual_results else None,
    )

    return {
        "flux_chart": flux_chart,
        "biomass_chart": biomass_chart,
        "weekly_yield_comparison": weekly_yield_comparison,
        "water_balance": {
            "total_precipitation_mm": round(total_precip, 1),
            "total_gross_irrigation_mm": round(total_gross, 1),
            "total_effective_irrigation_mm": round(total_effective, 1),
            "total_runoff_mm": round(total_runoff, 1),
            "total_et_optimal_mm": round(float(optimal_results.get("total_et") or 0), 1),
            "final_biomass_optimal_tha": round(optimal_biomass_final, 2),
            "final_biomass_actual_tha": round(actual_biomass_final, 2),
            "final_yield_optimal_tha": round(optimal_yield, 2),
            "final_yield_actual_tha": round(actual_yield, 2),
        },
        "farmer_simulation_ran": actual_results is not None,
    }
