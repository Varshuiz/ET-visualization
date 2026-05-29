"""Build ET comparison page context from a saved Supabase et_calculations row."""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd

from .et_units import get_unit_info


def parse_run_result_data(row: dict) -> tuple[dict, dict]:
    raw = row.get("result_data") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        return {}, {}
    inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
    results = raw.get("results") if isinstance(raw.get("results"), dict) else raw
    return inputs, results


def normalize_et_data_records(records: list[dict] | None) -> list[dict]:
    if not records:
        return []
    out = []
    for row in records:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        date_val = item.get("Date")
        if date_val is not None:
            try:
                item["Date"] = pd.to_datetime(date_val).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                item["Date"] = str(date_val)[:10]
        out.append(item)
    return out


def et_data_from_csv(csv_text: str, unit: str, unit_info: dict) -> list[dict]:
    if not csv_text:
        return []
    df = pd.read_csv(io.StringIO(csv_text))
    if "Date" not in df.columns:
        return []
    df["Date"] = pd.to_datetime(df["Date"])
    et_cols = [c for c in df.columns if c.startswith("ET_")]
    table_df = df[["Date"] + et_cols].copy()
    for col in et_cols:
        table_df[col] = pd.to_numeric(table_df[col], errors="coerce").fillna(0.0)
        if unit == "inches":
            from .et_units import convert_units

            table_df[col] = table_df[col].apply(
                lambda v: convert_units(v, "mm", "inches") if v else 0.0
            )
        table_df[col] = table_df[col].apply(
            lambda v: round(float(v), unit_info["decimal_places"]) if v else 0
        )
    return normalize_et_data_records(table_df.to_dict("records"))


def build_acis_location(row: dict, inputs: dict, results: dict) -> dict:
    loc = inputs.get("location") if isinstance(inputs.get("location"), dict) else {}
    city = row.get("city") or loc.get("city") or ""
    province = row.get("province") or loc.get("province") or ""
    desc = loc.get("description") or ", ".join(p for p in (city, province) if p)
    return {
        "description": desc or "Saved calculation",
        "city": city,
        "province": province,
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "start_date": row.get("date_range_start") or results.get("date_min") or loc.get("start_date"),
        "end_date": row.get("date_range_end") or results.get("date_max") or loc.get("end_date"),
    }


_METHOD_LABELS = {
    "PT": "Priestley-Taylor",
    "PM": "Penman-Monteith",
    "Maule": "Maulé",
    "Hargreaves": "Hargreaves-Samani",
}


def build_comparison_stats_diffs(comparison_stats: dict | None) -> list[tuple[str, float]]:
    if not comparison_stats:
        return []
    rows: list[tuple[str, float]] = []
    for key, value in comparison_stats.items():
        if key == "correlations" or value is None:
            continue
        if not str(key).endswith("_diff"):
            continue
        base = str(key)[:-5]
        parts = base.split("_")
        if len(parts) == 2:
            m1, m2 = parts
            label = f"{_METHOD_LABELS.get(m1, m1)} vs {_METHOD_LABELS.get(m2, m2)} (mean |Δ|)"
        else:
            label = base.replace("_", " ")
        rows.append((label, value))
    return rows


def comparison_context_from_saved_row(row: dict) -> dict[str, Any]:
    inputs, results = parse_run_result_data(row)
    unit = results.get("unit") or inputs.get("unit") or "mm"
    if unit not in ("mm", "inches"):
        unit = "mm"
    unit_info = get_unit_info(unit)

    available_methods = results.get("methods") or inputs.get("methods") or []
    if isinstance(available_methods, str):
        available_methods = [m.strip() for m in available_methods.split(",") if m.strip()]

    et_stats = results.get("et_stats") or {}
    comparison_stats = results.get("comparison_stats") or {}
    growing_season_stats = results.get("growing_season_stats") or {}

    et_data = normalize_et_data_records(results.get("et_data"))
    if not et_data:
        csv_text = results.get("csv") or results.get("et_data_csv") or ""
        et_data = et_data_from_csv(csv_text, unit, unit_info)

    csv_export = results.get("csv") or results.get("et_data_csv") or ""

    return {
        "et_data": et_data,
        "et_stats": et_stats,
        "comparison_stats": comparison_stats,
        "comparison_stats_diffs": build_comparison_stats_diffs(comparison_stats),
        "growing_season_stats": growing_season_stats,
        "plot_url": None,
        "plot_warning": None if et_data else "Daily series were not stored for this run; statistics only.",
        "growing_season_plots": {},
        "selected_unit": unit,
        "unit_info": unit_info,
        "acis_location": build_acis_location(row, inputs, results),
        "available_methods": available_methods,
        "is_saved_run": True,
        "csv_export": csv_export,
        "has_charts": bool(et_data),
    }
