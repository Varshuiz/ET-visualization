"""Rebuild forecast / AquaCrop page context from saved Supabase run rows."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .aquacrop_aggregation import compute_yield_tha
from .forecast_recommendations import CROP_GDD_PROFILES, SOIL_IRRIGATION_FACTORS


def _parse_result_data(row: dict) -> dict:
    raw = row.get("result_data") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _normalize_forecast_records(records: list | None) -> list[dict]:
    if not records:
        return []
    out: list[dict] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        date_val = row.get("Date")
        if date_val is not None:
            try:
                row["Date"] = pd.to_datetime(date_val)
            except (TypeError, ValueError):
                pass
        out.append(row)
    return out


def _crop_label(crop_type: str) -> str:
    return crop_type.replace("_", " ").title() if crop_type else "Wheat"


def _soil_label(soil_type: str) -> str:
    return soil_type.replace("_", " ").title() if soil_type else "Loam"


def forecast_context_from_saved_row(row: dict) -> dict[str, Any]:
    data = _parse_result_data(row)
    df_forecast = _normalize_forecast_records(data.get("df_forecast"))
    crop_type = (data.get("crop_type") or "wheat").strip().lower()
    soil_type = (data.get("soil_type") or "loam").strip().lower()
    if crop_type not in CROP_GDD_PROFILES:
        crop_type = "wheat"
    if soil_type not in SOIL_IRRIGATION_FACTORS:
        soil_type = "loam"

    province = row.get("province") or ""
    city = row.get("city") or ""
    forecast_days = int(row.get("forecast_days") or len(df_forecast) or 7)

    crop_label = data.get("crop_label") or _crop_label(crop_type)
    soil_label = data.get("soil_label") or _soil_label(soil_type)
    soil_factor = data.get("soil_factor")
    if soil_factor is None:
        soil_factor = SOIL_IRRIGATION_FACTORS.get(soil_type, 1.0)

    return {
        "error_message": None,
        "city_name": city,
        "selected_province": province,
        "province_options": [{"value": province, "label": province}] if province else [],
        "cities_by_province": {province: [city]} if province and city else {},
        "default_city_by_province": {province: city} if province and city else {},
        "selected_days": forecast_days,
        "show_extended_horizon_caveat": bool(
            data.get("show_extended_horizon_caveat", forecast_days > 7)
        ),
        "chart_available": bool(data.get("rec_chart_url")),
        "crop_type": crop_type,
        "soil_type": soil_type,
        "crop_label": crop_label,
        "soil_label": soil_label,
        "soil_factor": soil_factor,
        "crop_options": [
            {"value": k, "label": k.replace("_", " ").title()}
            for k in sorted(CROP_GDD_PROFILES.keys())
        ],
        "soil_options": [
            {"value": k, "label": k.replace("_", " ").title()}
            for k in sorted(SOIL_IRRIGATION_FACTORS.keys())
        ],
        "available_cities": [city] if city else [],
        "cities_by_region": {},
        "farm_prefill_note": False,
        "df_forecast": df_forecast,
        "total_precip": data.get("total_precip"),
        "estimated_et_total": data.get("estimated_et_total"),
        "net_water_balance": data.get("net_water_balance"),
        "irrigation_needed": data.get("irrigation_needed"),
        "recommendation_level": data.get("recommendation_level") or "moderate",
        "gdd_total": data.get("gdd_total") if data.get("gdd_total") is not None else 0.0,
        "gdd_stage": data.get("gdd_stage") or "—",
        "historical_confidence": data.get("historical_confidence"),
        "rec_chart_url": data.get("rec_chart_url"),
        "weekly_forecast": data.get("weekly_forecast"),
        "available_forecast_days": data.get("available_forecast_days"),
        "is_saved_run": True,
        "saved_run": row,
        "sidebar_active": "history",
    }


def aquacrop_context_from_saved_row(row: dict) -> dict[str, Any]:
    data = _parse_result_data(row)
    extra = data.get("context") if isinstance(data.get("context"), dict) else {}

    scalar_keys = (
        "yield_fresh",
        "yield_dry",
        "biomass",
        "total_et",
        "total_irrigation",
        "total_rainfall",
        "transpiration",
        "evaporation",
        "water_productivity",
        "irrigation_efficiency",
        "canopy_cover_max",
        "growing_degree_days",
        "reached_maturity",
        "partial_results",
        "result_note",
    )
    results: dict[str, Any] = {k: data[k] for k in scalar_keys if k in data}
    if data.get("growth_chart"):
        results["growth_chart"] = data["growth_chart"]
    if data.get("water_balance_chart"):
        results["water_balance_chart"] = data["water_balance_chart"]

    crop = row.get("crop_type") or extra.get("crop") or "Wheat"
    dry = results.get("yield_dry")
    yield_tha = None
    if dry is not None:
        try:
            yield_tha = compute_yield_tha(float(dry), str(crop))
        except (TypeError, ValueError):
            yield_tha = None

    has_charts = bool(results.get("growth_chart") and results.get("water_balance_chart"))

    return {
        "crops": [crop] if crop else [],
        "soil_types": [extra.get("soil")] if extra.get("soil") else [],
        "available_cities": [extra.get("city")] if extra.get("city") else [],
        "selected_city": extra.get("city") or "",
        "selected_crop": crop,
        "selected_soil": extra.get("soil") or "",
        "selected_irrigation": extra.get("irrigation") or "",
        "timestep": extra.get("timestep") or "",
        "start_date": row.get("start_date") or "",
        "end_date": row.get("end_date") or "",
        "sim_mode": row.get("mode") or "",
        "historical_range_type": "",
        "simulation_year": "",
        "hist_year_start": "",
        "hist_year_end": "",
        "historical_results_rows": extra.get("historical_results_rows") or [],
        "weekly_yield_projection": extra.get("weekly_yield_projection") or [],
        "forecast_mode_caveat": extra.get("forecast_mode_caveat"),
        "multi_year_mode": bool(extra.get("multi_year_mode")),
        "irrigation_methods": [],
        "farm_prefill_note": False,
        "error_message": None,
        "warning_messages": [],
        "temperature_source_summary": None,
        "has_results": has_charts or bool(results.get("yield_fresh") is not None),
        "results": results,
        "yield_tha": yield_tha,
        "resampled_chart": extra.get("resampled_chart"),
        "resampled_data": extra.get("resampled_data") or [],
        "is_saved_run": True,
        "saved_run": row,
        "sidebar_active": "history",
    }
