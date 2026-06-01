"""AIMM-style weekly season data for AquaCrop (farmer actuals vs optimal)."""

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
    soil_moisture: list[str] | None = None,
) -> list[dict[str, Any]]:
    weather_by_week = {r["week_start"]: r for r in (weather_rows or [])}
    rows: list[dict[str, Any]] = []
    for i, ws in enumerate(week_starts):
        key = ws.strftime("%Y-%m-%d")
        wk = weather_by_week.get(key, {})
        precip = _float_or_zero(wk.get("precipitation"))
        gross = _float_or_zero((gross_irrigation or [None] * len(week_starts))[i] if gross_irrigation else 0)
        sm_raw = (soil_moisture or [None] * len(week_starts))[i] if soil_moisture else None
        sm = _float_or_zero(sm_raw) if sm_raw not in (None, "") else field_capacity_pct * 0.6
        eff = effective_irrigation_mm(gross, application_efficiency_pct)
        runoff = aimm_weekly_runoff_mm(precip, sm, field_capacity_pct)
        rows.append(
            {
                "week_start": key,
                "gross_irrigation": _fmt_num(gross) if gross else "",
                "effective_irrigation": eff,
                "soil_moisture": _fmt_num(sm) if sm_raw not in (None, "") else "",
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
    sm_list = post.getlist("mgmt_soil_moisture")

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
        soil_moisture=sm_list,
    )

    has_farmer_data = (
        sum(_float_or_zero(r.get("gross_irrigation")) for r in management_rows) > 0
        or any((str(r.get("soil_moisture") or "")).strip() for r in management_rows)
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
