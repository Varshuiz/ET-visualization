"""Build prior-run comparison payloads for AquaCrop results UI."""

from __future__ import annotations

import json
from typing import Any

from .crop_catalog import crop_label


def _parse_result_data(row: dict) -> dict:
    raw = row.get("result_data") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _weekly_rows_from_run(result_data: dict) -> list[dict]:
    extra = result_data.get("context") or {}
    rows = extra.get("weekly_yield_comparison") or extra.get("weekly_yield_projection") or []
    return rows if isinstance(rows, list) else []


def _run_display_label(row: dict, extra: dict) -> str:
    crop = crop_label(row.get("crop_type") or extra.get("crop") or "Crop")
    city = (extra.get("city") or "").strip() or "—"
    start = (row.get("start_date") or "").strip()
    end = (row.get("end_date") or "").strip()
    dates = f"{start} – {end}" if start and end else (start or end or "—")
    return f"{crop} · {city} · {dates}"


def _weekly_payload_from_rows(rows: list[dict]) -> dict[str, Any]:
    yields_by_week: dict[str, float | None] = {}
    biomass_by_week: dict[str, float | None] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        wk = row.get("week_after_planting")
        if wk is None:
            continue
        key = str(wk)
        prev_yield = row.get("your_yield_tha")
        if prev_yield is None:
            prev_yield = row.get("optimal_yield_tha")
        if prev_yield is None:
            prev_yield = row.get("yield_tha")
        if prev_yield is not None:
            try:
                yields_by_week[key] = round(float(prev_yield), 3)
            except (TypeError, ValueError):
                yields_by_week[key] = None
        bio = row.get("biomass_peak_actual_tha")
        if bio is None:
            bio = row.get("biomass_peak_tha")
        if bio is None:
            bio = row.get("biomass_peak_optimal_tha")
        if bio is not None:
            try:
                biomass_by_week[key] = round(float(bio), 3)
            except (TypeError, ValueError):
                biomass_by_week[key] = None
    return {"yields_by_week": yields_by_week, "biomass_by_week": biomass_by_week}


def build_aquacrop_previous_runs_payload(
    rows: list[dict],
    *,
    exclude_run_id: str | None = None,
) -> list[dict]:
    """Compact prior-run data for client-side weekly yield / biomass comparison."""
    out: list[dict] = []
    exclude = str(exclude_run_id).strip() if exclude_run_id else ""
    for row in rows or []:
        rid = str(row.get("id") or "").strip()
        if not rid or (exclude and rid == exclude):
            continue
        result_data = _parse_result_data(row)
        extra = result_data.get("context") or {}
        weekly_rows = _weekly_rows_from_run(result_data)
        if not weekly_rows and not result_data.get("growth_chart"):
            continue
        weekly_payload = _weekly_payload_from_rows(weekly_rows)
        growth = result_data.get("growth_chart") or {}
        if not isinstance(growth, dict):
            growth = {}
        out.append(
            {
                "id": rid,
                "label": _run_display_label(row, extra),
                "weekly_yields_by_week": weekly_payload["yields_by_week"],
                "weekly_biomass_by_week": weekly_payload["biomass_by_week"],
                "biomass_series": {
                    "dates": list(growth.get("dates") or []),
                    "biomass": list(growth.get("biomass") or []),
                },
            }
        )
    return out
