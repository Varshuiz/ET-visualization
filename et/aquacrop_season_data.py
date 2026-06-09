"""AIMM-style weekly season data for AquaCrop (user actuals vs optimal)."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

# Top-layer field capacity (%) by soil label — used in AIMM runoff (SM/FC).
SOIL_FIELD_CAPACITY_PCT: dict[str, float] = {
    "Sandy Loam": 22.0,
    "Loam": 28.0,
    "Clay Loam": 32.0,
    "Sandy Clay Loam": 30.0,
    "Silty Clay": 36.0,
    "Clay": 38.0,
}

DEFAULT_APPLICATION_EFFICIENCY_PCT = 81.0
DEFAULT_PLANTING_MONTH_DAY = "05/01"


def soil_field_capacity_pct(soil_type: str) -> float:
    return float(SOIL_FIELD_CAPACITY_PCT.get(soil_type or "Loam", SOIL_FIELD_CAPACITY_PCT["Loam"]))


def default_allowable_mad_pct(crop: str, irrigation: str) -> float:
    """
    AIMM-style defaults:
    - 30% for potatoes or centre-pivot-style full irrigation
    - 50% for grain/oilseed with surface / wheel-move (rainfed, deficit)
    """
    crop_l = (crop or "").strip().lower()
    if crop_l == "potato":
        return 30.0
    if irrigation in ("full",):
        return 30.0
    return 50.0


def weekly_period_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Week rows anchored on start date, 7-day steps through end (inclusive start)."""
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    if end < start:
        return []
    starts: list[pd.Timestamp] = []
    cur = start
    while cur <= end:
        starts.append(cur)
        cur += pd.Timedelta(days=7)
    return starts


def aimm_weekly_runoff_mm(
    precipitation_mm: float,
    soil_moisture_pct: float,
    field_capacity_pct: float,
) -> float:
    """
    AIMM weekly runoff:
    - R < 25 mm → 0
    - R ≥ 25 mm → Runoff = R − I, where
      I = 0.9177 + 1.811·ln(R) − 0.0097·ln(R)·(SM/FC)·100
    """
    r = float(precipitation_mm or 0)
    if r < 25.0:
        return 0.0
    sm = float(soil_moisture_pct or 0)
    fc = max(float(field_capacity_pct or 28.0), 1.0)
    ln_r = math.log(r)
    infiltration = 0.9177 + 1.811 * ln_r - 0.0097 * ln_r * (sm / fc) * 100.0
    infiltration = max(0.0, infiltration)
    return round(max(0.0, r - infiltration), 2)


def effective_irrigation_mm(gross_mm: float, efficiency_pct: float) -> float:
    eff = max(0.0, min(float(efficiency_pct or 0), 100.0))
    return round(max(0.0, float(gross_mm or 0)) * eff / 100.0, 2)


def _aggregate_daily_to_weekly(daily: pd.DataFrame, week_starts: list[pd.Timestamp]) -> list[dict[str, Any]]:
    if daily is None or daily.empty or not week_starts:
        return []
    df = daily.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["Date"])
    rows: list[dict[str, Any]] = []
    for i, ws in enumerate(week_starts):
        we = week_starts[i + 1] - pd.Timedelta(days=1) if i + 1 < len(week_starts) else df["Date"].max()
        if pd.isna(we):
            we = ws + pd.Timedelta(days=6)
        mask = (df["Date"] >= ws) & (df["Date"] <= we)
        chunk = df.loc[mask]
        if chunk.empty:
            rows.append(
                {
                    "week_start": ws.strftime("%Y-%m-%d"),
                    "tmax": "",
                    "tmin": "",
                    "precipitation": "",
                    "reference_et": "",
                }
            )
            continue
        tmax = pd.to_numeric(chunk.get("MaxTemp", chunk.get("Tmax")), errors="coerce").max()
        tmin = pd.to_numeric(chunk.get("MinTemp", chunk.get("Tmin")), errors="coerce").min()
        precip = pd.to_numeric(chunk.get("Precipitation"), errors="coerce").sum()
        ref_et = pd.to_numeric(chunk.get("ReferenceET"), errors="coerce").sum()
        rows.append(
            {
                "week_start": ws.strftime("%Y-%m-%d"),
                "tmax": round(float(tmax), 1) if pd.notna(tmax) else "",
                "tmin": round(float(tmin), 1) if pd.notna(tmin) else "",
                "precipitation": round(float(precip), 1) if pd.notna(precip) else "",
                "reference_et": round(float(ref_et), 2) if pd.notna(ref_et) else "",
            }
        )
    return rows


def build_management_rows(
    week_starts: list[pd.Timestamp],
    *,
    application_efficiency_pct: float,
    field_capacity_pct: float,
    weather_rows: list[dict[str, Any]] | None = None,
    gross_irrigation: list[str] | None = None,
) -> list[dict[str, Any]]:
    weather_by_week = {_normalize_week_start(r["week_start"]): r for r in (weather_rows or [])}
    rows: list[dict[str, Any]] = []
    for i, ws in enumerate(week_starts):
        key = ws.strftime("%Y-%m-%d")
        wk = weather_by_week.get(key, weather_by_week.get(_normalize_week_start(key), {}))
        precip = _float_or_zero(wk.get("precipitation"))
        gross = _float_or_zero((gross_irrigation or [None] * len(week_starts))[i] if gross_irrigation else 0)
        sm_for_runoff = field_capacity_pct * 0.6
        eff = effective_irrigation_mm(gross, application_efficiency_pct)
        runoff = aimm_weekly_runoff_mm(precip, sm_for_runoff, field_capacity_pct)
        rows.append(
            {
                "week_start": key,
                "gross_irrigation": _fmt_num(gross) if gross else "",
                "effective_irrigation": eff,
                "runoff": runoff,
            }
        )
    return rows


def build_season_tables(
    *,
    start_date: str,
    end_date: str,
    city_name: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    soil_type: str = "Loam",
    crop: str = "Wheat",
    irrigation: str = "full",
    fetch_eccc: bool = True,
    planting_date: str | None = None,
    allowable_mad_pct: float | None = None,
    application_efficiency_pct: float | None = None,
) -> dict[str, Any]:
    start_ts = pd.to_datetime(start_date.replace("/", "-"), errors="coerce")
    end_ts = pd.to_datetime(end_date.replace("/", "-"), errors="coerce")
    if pd.isna(start_ts) or pd.isna(end_ts):
        start_ts, end_ts = pd.Timestamp.now().normalize(), pd.Timestamp.now().normalize()
    year = int(start_ts.year)
    plant = planting_date or f"{year}/{DEFAULT_PLANTING_MONTH_DAY}"
    fc = soil_field_capacity_pct(soil_type)
    eff_pct = (
        float(application_efficiency_pct)
        if application_efficiency_pct is not None
        else DEFAULT_APPLICATION_EFFICIENCY_PCT
    )
    mad = (
        float(allowable_mad_pct)
        if allowable_mad_pct is not None
        else default_allowable_mad_pct(crop, irrigation)
    )

    plant_ts = pd.to_datetime(str(plant).replace("/", "-"), errors="coerce")
    period_start = start_ts
    if pd.notna(plant_ts) and start_ts <= plant_ts.normalize() <= end_ts:
        period_start = plant_ts.normalize()
    week_starts = weekly_period_starts(period_start, end_ts)
    weather_rows: list[dict[str, Any]] = []
    eccc_prefilled = False

    if fetch_eccc and latitude is not None and longitude is not None:
        try:
            from .eccc_weather import build_aquacrop_weather_from_eccc

            daily = build_aquacrop_weather_from_eccc(
                latitude=float(latitude),
                longitude=float(longitude),
                start_date=start_ts.strftime("%Y-%m-%d"),
                end_date=end_ts.strftime("%Y-%m-%d"),
            )
            weather_rows = _aggregate_daily_to_weekly(daily, week_starts)
            eccc_prefilled = bool(weather_rows)
        except Exception:
            weather_rows = []

    if not weather_rows:
        weather_rows = [
            {
                "week_start": ws.strftime("%Y-%m-%d"),
                "tmax": "",
                "tmin": "",
                "precipitation": "",
                "reference_et": "",
            }
            for ws in week_starts
        ]

    management_rows = build_management_rows(
        week_starts,
        application_efficiency_pct=eff_pct,
        field_capacity_pct=fc,
        weather_rows=weather_rows,
    )

    return {
        "planting_date": plant.replace("-", "/") if plant else f"{year}/05/01",
        "allowable_mad_pct": mad,
        "application_efficiency_pct": eff_pct,
        "soil_field_capacity_pct": fc,
        "weather_rows": weather_rows,
        "management_rows": management_rows,
        "season_data_collapsed": False,
        "eccc_weather_prefilled": eccc_prefilled,
        "season_city": city_name or "",
    }


def parse_season_data_from_post(
    post,
    *,
    soil_type: str,
    crop: str,
    irrigation: str,
) -> dict[str, Any]:
    """Parse optional season tables from POST; enrich runoff and effective irrigation."""
    planting_date = (post.get("planting_date") or "").strip()
    try:
        mad = float(post.get("allowable_mad_pct") or default_allowable_mad_pct(crop, irrigation))
    except (TypeError, ValueError):
        mad = default_allowable_mad_pct(crop, irrigation)
    try:
        eff_pct = float(post.get("application_efficiency_pct") or DEFAULT_APPLICATION_EFFICIENCY_PCT)
    except (TypeError, ValueError):
        eff_pct = DEFAULT_APPLICATION_EFFICIENCY_PCT

    week_starts = post.getlist("weather_week_start")
    if not week_starts:
        return _empty_season_context(soil_type, crop, irrigation)

    tmax_list = post.getlist("weather_tmax")
    tmin_list = post.getlist("weather_tmin")
    precip_list = post.getlist("weather_precip")
    ref_et_list = post.getlist("weather_ref_et")
    gross_list = post.getlist("mgmt_gross_irr")

    fc = soil_field_capacity_pct(soil_type)
    weather_rows: list[dict[str, Any]] = []
    for i, ws in enumerate(week_starts):
        weather_rows.append(
            {
                "week_start": ws,
                "tmax": (tmax_list[i] if i < len(tmax_list) else "").strip(),
                "tmin": (tmin_list[i] if i < len(tmin_list) else "").strip(),
                "precipitation": (precip_list[i] if i < len(precip_list) else "").strip(),
                "reference_et": (ref_et_list[i] if i < len(ref_et_list) else "").strip(),
            }
        )

    week_ts = [pd.to_datetime(ws, errors="coerce") for ws in week_starts]
    week_ts = [t for t in week_ts if pd.notna(t)]
    management_rows = build_management_rows(
        week_ts,
        application_efficiency_pct=eff_pct,
        field_capacity_pct=fc,
        weather_rows=weather_rows,
        gross_irrigation=gross_list,
    )

    has_farmer_data = (
        sum(_float_or_zero(r.get("gross_irrigation")) for r in management_rows) > 0
        or sum(_float_or_zero(r.get("precipitation")) for r in weather_rows) > 0
        or sum(_float_or_zero(r.get("reference_et")) for r in weather_rows) > 0
    )

    return {
        "planting_date": planting_date or f"{_aquacrop_year()}/{DEFAULT_PLANTING_MONTH_DAY}",
        "allowable_mad_pct": mad,
        "application_efficiency_pct": eff_pct,
        "soil_field_capacity_pct": fc,
        "weather_rows": weather_rows,
        "management_rows": management_rows,
        "season_data_collapsed": post.get("season_data_collapsed") == "1",
        "has_season_data": has_farmer_data,
        "total_gross_irrigation_mm": sum(_float_or_zero(r.get("gross_irrigation")) for r in management_rows),
        "total_effective_irrigation_mm": sum(float(r.get("effective_irrigation") or 0) for r in management_rows),
        "total_runoff_mm": sum(float(r.get("runoff") or 0) for r in management_rows),
    }


def _empty_season_context(soil_type: str, crop: str, irrigation: str) -> dict[str, Any]:
    start, end = _default_season_str()
    base = build_season_tables(
        start_date=start,
        end_date=end,
        soil_type=soil_type,
        crop=crop,
        irrigation=irrigation,
        fetch_eccc=False,
    )
    base["has_season_data"] = False
    return base


def _default_season_str() -> tuple[str, str]:
    y = _aquacrop_year()
    return f"{y}/05/01", f"{y}/09/30"


def _aquacrop_year() -> int:
    return int(pd.Timestamp.now().year)


def _float_or_zero(val) -> float:
    try:
        if val is None or val == "":
            return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _fmt_num(val: float) -> str:
    if val == int(val):
        return str(int(val))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def _normalize_week_start(value: Any) -> str:
    """Canonical YYYY-MM-DD for matching saved vs built week rows."""
    if value is None or value == "":
        return ""
    ts = pd.to_datetime(str(value).replace("/", "-"), errors="coerce")
    if pd.isna(ts):
        return str(value).strip()
    return ts.strftime("%Y-%m-%d")


def _has_display_value(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, str) and val.strip() == "":
        return False
    return True


def merge_saved_season_table(
    tables: dict[str, Any],
    saved: dict[str, Any] | None,
) -> dict[str, Any]:
    """Overlay saved management/weather rows onto freshly built week rows when dates align."""
    if not saved or not isinstance(saved, dict):
        return tables
    saved_mgmt = saved.get("management_rows") or []
    saved_weather = saved.get("weather_rows") or []
    if not saved_mgmt and not saved_weather:
        return tables

    mgmt_by_week = {
        _normalize_week_start(r.get("week_start")): r
        for r in saved_mgmt
        if r.get("week_start")
    }
    weather_by_week = {
        _normalize_week_start(r.get("week_start")): r
        for r in saved_weather
        if r.get("week_start")
    }

    mgmt_rows = tables.get("management_rows") or []
    matched_mgmt = 0
    for row in mgmt_rows:
        key = _normalize_week_start(row.get("week_start", ""))
        prev = mgmt_by_week.get(key)
        if not prev:
            continue
        matched_mgmt += 1
        for field in ("gross_irrigation", "effective_irrigation", "runoff"):
            val = prev.get(field)
            if _has_display_value(val):
                row[field] = val

    weather_rows = tables.get("weather_rows") or []
    matched_weather = 0
    for row in weather_rows:
        key = _normalize_week_start(row.get("week_start", ""))
        prev = weather_by_week.get(key)
        if not prev:
            continue
        matched_weather += 1
        for field in ("tmax", "tmin", "precipitation", "reference_et"):
            val = prev.get(field)
            if _has_display_value(val):
                row[field] = val

    if saved_mgmt and matched_mgmt == 0:
        tables["management_rows"] = [
            {
                "week_start": _normalize_week_start(r.get("week_start")),
                "gross_irrigation": r.get("gross_irrigation", ""),
                "effective_irrigation": r.get("effective_irrigation", ""),
                "runoff": r.get("runoff", ""),
            }
            for r in saved_mgmt
            if r.get("week_start")
        ]
        matched_mgmt = len(tables["management_rows"])

    if saved_weather and matched_weather == 0:
        tables["weather_rows"] = [
            {
                "week_start": _normalize_week_start(r.get("week_start")),
                "tmax": r.get("tmax", ""),
                "tmin": r.get("tmin", ""),
                "precipitation": r.get("precipitation", ""),
                "reference_et": r.get("reference_et", ""),
            }
            for r in saved_weather
            if r.get("week_start")
        ]

    if saved.get("start_date"):
        tables["start_date"] = saved["start_date"]
    if saved.get("end_date"):
        tables["end_date"] = saved["end_date"]
    if saved.get("planting_date"):
        tables["planting_date"] = saved["planting_date"]
    if saved.get("application_efficiency_pct") is not None:
        tables["application_efficiency_pct"] = saved["application_efficiency_pct"]
    if saved.get("crop_condition"):
        tables["selected_crop_condition"] = saved.get("crop_condition")

    tables["season_table_prefill_banner"] = bool(matched_mgmt or matched_weather)
    tables["saved_season_prefill"] = {
        "management_rows": tables.get("management_rows") or [],
        "weather_rows": tables.get("weather_rows") or [],
        "soil_field_capacity_pct": tables.get("soil_field_capacity_pct"),
    }
    return tables


def _wr_mm_to_top_layer_pct(wr_mm: float, wr_fc_mm: float, fc_pct: float) -> float:
    if wr_fc_mm <= 0 or pd.isna(wr_mm):
        return round(fc_pct * 0.6, 1)
    return round(min(100.0, max(0.0, (float(wr_mm) / wr_fc_mm) * fc_pct)), 1)


def _simulated_sm_by_week_index(
    results: dict,
    start_date: str,
    soil_type: str,
) -> dict[int, float]:
    from .aquacrop_aggregation import build_simulation_results_tables

    _, weekly = build_simulation_results_tables(results, start_date)
    if not weekly:
        return {}

    wf = results.get("water_flux")
    wr_fc_mm = 1.0
    if isinstance(wf, pd.DataFrame) and not wf.empty and "Wr" in wf.columns:
        wr_series = pd.to_numeric(wf["Wr"], errors="coerce").dropna()
        if len(wr_series):
            wr_fc_mm = float(wr_series.quantile(0.95)) or float(wr_series.max()) or 1.0
    fc_pct = soil_field_capacity_pct(soil_type)

    sm_by_week_idx: dict[int, float] = {}
    for i, wrow in enumerate(weekly):
        sm_mm = wrow.get("soil_moisture")
        if sm_mm is not None:
            sm_by_week_idx[i] = _wr_mm_to_top_layer_pct(float(sm_mm), wr_fc_mm, fc_pct)
    return sm_by_week_idx


def build_soil_moisture_comparison_rows(
    results: dict,
    start_date: str,
    soil_type: str,
    week_starts: list[str] | None = None,
    saved_actual_by_week: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Weekly AquaCrop soil moisture (%) vs optional user field measurements.
    Shown on the results page after simulation.
    """
    sm_by_idx = _simulated_sm_by_week_index(results, start_date, soil_type)
    if not sm_by_idx and not week_starts:
        return []

    start_dt = pd.to_datetime(str(start_date).replace("/", "-"), errors="coerce")
    if pd.isna(start_dt):
        start_dt = pd.Timestamp.today().normalize()

    keys = week_starts or []
    if not keys:
        end_dt = start_dt + pd.Timedelta(days=max(0, (len(sm_by_idx) - 1) * 7))
        keys = [
            ws.strftime("%Y-%m-%d")
            for ws in weekly_period_starts(start_dt, end_dt)
        ]

    actual_map = saved_actual_by_week or {}
    rows: list[dict[str, Any]] = []
    for i, raw_ws in enumerate(keys):
        ws_norm = _normalize_week_start(raw_ws)
        ws_ts = pd.to_datetime(ws_norm, errors="coerce")
        if pd.notna(ws_ts) and pd.notna(start_dt):
            wk_idx = int((ws_ts.normalize() - start_dt.normalize()).days // 7)
        else:
            wk_idx = i
        simulated = sm_by_idx.get(wk_idx)
        if simulated is None and sm_by_idx:
            simulated = sm_by_idx.get(min(wk_idx, max(sm_by_idx.keys())))

        actual_raw = actual_map.get(ws_norm)
        actual_val = None
        if actual_raw not in (None, ""):
            try:
                actual_val = round(float(actual_raw), 1)
            except (TypeError, ValueError):
                actual_val = None

        difference = None
        if simulated is not None and actual_val is not None:
            difference = round(actual_val - simulated, 1)

        rows.append(
            {
                "week_start": ws_norm,
                "simulated_pct": simulated,
                "actual_pct": actual_val,
                "difference_pct": difference,
            }
        )
    return rows
