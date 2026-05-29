"""Persist farmer data to Supabase (server-side service role only)."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from .auth_supabase import SESSION_ACTIVE_FARM_ID, hash_sensitive, normalize_user_id
from .supabase_client import get_service_client, supabase_configured

logger = logging.getLogger(__name__)


def _json_safe(obj: Any) -> Any:
    """Recursively convert values to JSON-serializable types (NaN/Inf → null)."""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer, int)) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(obj, Decimal):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, pd.Timestamp):
        if pd.isna(obj):
            return None
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return str(obj)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _service_table(name: str):
    """All data access uses the service-role client (never anon) for server-side writes/reads."""
    client = get_service_client()
    if client is None:
        logger.error("Supabase service client unavailable — check SUPABASE_URL and SUPABASE_SERVICE_KEY")
        return None
    return client.table(name)


def _rows_from_response(resp) -> list:
    data = getattr(resp, "data", None)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _insert(table: str, row: dict) -> dict | None:
    if not supabase_configured():
        logger.warning("Supabase not configured; skip insert into %s", table)
        return None
    uid = normalize_user_id(row.get("user_id"))
    if table != "profiles" and not uid:
        logger.error("Refusing insert into %s without user_id", table)
        return None
    try:
        tbl = _service_table(table)
        if tbl is None:
            return None
        resp = tbl.insert(row).select("*").execute()
        rows = _rows_from_response(resp)
        if rows:
            return rows[0]
        logger.warning("Supabase insert into %s returned no rows; payload keys=%s", table, list(row.keys()))
        return None
    except Exception as exc:
        logger.error("Supabase insert into %s failed: %s", table, exc, exc_info=True)
        return None


def _select(table: str, user_id: str, *, limit: int = 20, order_col: str = "created_at") -> list:
    uid = normalize_user_id(user_id)
    if not uid or not supabase_configured():
        return []
    try:
        tbl = _service_table(table)
        if tbl is None:
            return []
        resp = (
            tbl.select("*")
            .eq("user_id", uid)
            .order(order_col, desc=True)
            .limit(limit)
            .execute()
        )
        return _rows_from_response(resp)
    except Exception as exc:
        logger.error("Supabase select from %s failed for user %s: %s", table, uid, exc, exc_info=True)
        return []


def upsert_profile(*, user_id: str, email: str, full_name: str | None = None) -> None:
    """Create or update profile. Omit full_name on login so existing names are kept."""
    uid = normalize_user_id(user_id)
    if not uid or not supabase_configured():
        return
    try:
        tbl = _service_table("profiles")
        if tbl is None:
            return
        row: dict[str, Any] = {"id": uid, "email": email}
        if full_name is not None:
            row["full_name"] = (full_name or "").strip()
        tbl.upsert(row, on_conflict="id").execute()
    except Exception as exc:
        logger.error("Supabase profile upsert failed: %s", exc, exc_info=True)


def get_profile(user_id: str) -> dict | None:
    uid = normalize_user_id(user_id)
    if not uid or not supabase_configured():
        return None
    try:
        tbl = _service_table("profiles")
        if tbl is None:
            return None
        resp = tbl.select("*").eq("id", uid).limit(1).execute()
        rows = _rows_from_response(resp)
        return rows[0] if rows else None
    except Exception as exc:
        logger.error("Supabase get profile failed: %s", exc, exc_info=True)
        return None


def get_run_for_user(table: str, user_id: str, run_id: str) -> dict | None:
    uid = normalize_user_id(user_id)
    rid = str(run_id).strip() if run_id else ""
    if not uid or not rid or not supabase_configured():
        return None
    try:
        tbl = _service_table(table)
        if tbl is None:
            return None
        resp = tbl.select("*").eq("id", rid).eq("user_id", uid).limit(1).execute()
        rows = _rows_from_response(resp)
        return rows[0] if rows else None
    except Exception as exc:
        logger.error("Supabase get %s failed: %s", table, exc, exc_info=True)
        return None


def get_et_calculation_by_id(user_id: str, run_id: str) -> dict | None:
    return get_run_for_user("et_calculations", user_id, run_id)


def get_aquacrop_run_by_id(user_id: str, run_id: str) -> dict | None:
    return get_run_for_user("aquacrop_runs", user_id, run_id)


def get_forecast_run_by_id(user_id: str, run_id: str) -> dict | None:
    return get_run_for_user("forecast_runs", user_id, run_id)


def get_farm_for_user(user_id: str, farm_id: str | None = None) -> dict | None:
    uid = normalize_user_id(user_id)
    if not uid or not supabase_configured():
        return None
    try:
        tbl = _service_table("farms")
        if tbl is None:
            return None
        q = tbl.select("*").eq("user_id", uid)
        if farm_id:
            q = q.eq("id", str(farm_id))
        resp = q.order("created_at", desc=True).limit(1).execute()
        rows = _rows_from_response(resp)
        return rows[0] if rows else None
    except Exception as exc:
        logger.error("Supabase get farm failed: %s", exc, exc_info=True)
        return None


def save_farm(
    *,
    user_id: str,
    farm_name: str,
    province: str,
    city: str,
    area_hectares: float | None,
    crop_type: str,
    irrigation_type: str,
    farm_id: str | None = None,
) -> dict | None:
    uid = normalize_user_id(user_id)
    if not uid or not supabase_configured():
        logger.error("save_farm: missing user_id or Supabase not configured")
        return None

    payload = {
        "user_id": uid,
        "farm_name": farm_name,
        "province": province,
        "city": city,
        "area_hectares": area_hectares,
        "crop_type": crop_type or "",
        "irrigation_type": irrigation_type or "",
    }

    try:
        tbl = _service_table("farms")
        if tbl is None:
            return None

        if farm_id:
            fid = str(farm_id)
            resp = (
                tbl.update(payload)
                .eq("id", fid)
                .eq("user_id", uid)
                .select("*")
                .execute()
            )
            rows = _rows_from_response(resp)
            if rows:
                return rows[0]
            return get_farm_for_user(uid, fid)

        resp = tbl.insert(payload).select("*").execute()
        rows = _rows_from_response(resp)
        if rows:
            return rows[0]
        return get_farm_for_user(uid)
    except Exception as exc:
        logger.error("Supabase save farm failed for user %s: %s", uid, exc, exc_info=True)
        return None


def resolve_farm_id(request, user_id: str) -> str | None:
    uid = normalize_user_id(user_id)
    if not uid:
        return None
    fid = request.session.get(SESSION_ACTIVE_FARM_ID)
    if fid:
        return str(fid)
    farm = get_farm_for_user(uid)
    if farm and farm.get("id"):
        request.session[SESSION_ACTIVE_FARM_ID] = str(farm["id"])
        request.session.modified = True
        return str(farm["id"])
    return None


def _date_only(value) -> str | None:
    """Normalize to YYYY-MM-DD for Supabase date columns."""
    if value is None or value == "":
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return None


def _et_calculation_row(
    *,
    user_id: str,
    farm_id: str | None,
    inputs: dict,
    result_data: dict,
) -> dict:
    """
    Map app payloads to the Supabase et_calculations table:
    user_id, farm_id, et_method, province, city, date_range_start, date_range_end, result_data
    """
    uid = normalize_user_id(user_id)
    location = inputs.get("location") if isinstance(inputs.get("location"), dict) else {}
    methods = inputs.get("methods") or result_data.get("methods") or []
    if isinstance(methods, list):
        et_method = ", ".join(str(m) for m in methods if m)
    else:
        et_method = str(methods) if methods else "ET comparison"

    province = (
        inputs.get("province")
        or location.get("province")
        or result_data.get("province")
    )
    city = (
        inputs.get("city")
        or location.get("description")
        or location.get("station")
        or result_data.get("city")
    )
    date_start = (
        inputs.get("date_range_start")
        or location.get("start_date")
        or result_data.get("date_min")
    )
    date_end = (
        inputs.get("date_range_end")
        or location.get("end_date")
        or result_data.get("date_max")
    )

    return {
        "user_id": uid,
        "farm_id": str(farm_id) if farm_id else None,
        "et_method": (et_method[:500] if et_method else None),
        "province": str(province)[:120] if province else None,
        "city": str(city)[:200] if city else None,
        "date_range_start": _date_only(date_start),
        "date_range_end": _date_only(date_end),
        "result_data": _json_safe({"inputs": inputs, "results": result_data}),
    }


def save_et_calculation(*, user_id: str, farm_id: str | None, inputs: dict, result_data: dict) -> dict | None:
    uid = normalize_user_id(user_id)
    if not uid:
        return None
    row = _et_calculation_row(
        user_id=uid,
        farm_id=farm_id,
        inputs=inputs or {},
        result_data=result_data or {},
    )
    return _insert("et_calculations", row)


def compact_aquacrop_result_data(results: dict, extra: dict | None = None) -> dict:
    """
    Store summary + chart payloads in result_data (omit huge daily DataFrames).
    """
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
    payload: dict[str, Any] = {k: results.get(k) for k in scalar_keys if k in results}
    if results.get("growth_chart"):
        payload["growth_chart"] = results["growth_chart"]
    if results.get("water_balance_chart"):
        payload["water_balance_chart"] = results["water_balance_chart"]
    if extra:
        payload["context"] = extra
    return _json_safe(payload)


def save_aquacrop_run(
    *,
    user_id: str,
    farm_id: str | None,
    mode: str,
    crop_type: str,
    start_date: str,
    end_date: str,
    result_data: dict,
) -> dict | None:
    uid = normalize_user_id(user_id)
    if not uid:
        return None
    return _insert(
        "aquacrop_runs",
        {
            "user_id": uid,
            "farm_id": str(farm_id) if farm_id else None,
            "mode": (mode or None),
            "crop_type": (crop_type or None),
            "start_date": _date_only(start_date),
            "end_date": _date_only(end_date),
            "result_data": result_data if isinstance(result_data, dict) else _json_safe(result_data),
        },
    )


def save_forecast_run(
    *,
    user_id: str,
    farm_id: str | None,
    province: str,
    city: str,
    forecast_days: int,
    et_method: str,
    result_data: dict,
) -> dict | None:
    uid = normalize_user_id(user_id)
    if not uid:
        return None
    return _insert(
        "forecast_runs",
        {
            "user_id": uid,
            "farm_id": str(farm_id) if farm_id else None,
            "province": province,
            "city": city,
            "forecast_days": int(forecast_days),
            "et_method": et_method,
            "result_data": _json_safe(result_data),
        },
    )


def log_usage(
    *,
    user_id: str,
    feature: str,
    action: str,
    metadata: dict | None = None,
    hash_email: str | None = None,
) -> None:
    uid = normalize_user_id(user_id)
    if not uid:
        return
    meta = _json_safe(metadata or {})
    if hash_email:
        meta["email_hash"] = hash_sensitive(hash_email)
    _insert(
        "usage_logs",
        {
            "user_id": uid,
            "feature": feature,
            "action": action,
            "metadata": meta,
        },
    )


def list_recent_et_calculations(user_id: str, limit: int = 10):
    return _select("et_calculations", user_id, limit=limit)


def list_recent_aquacrop_runs(user_id: str, limit: int = 10):
    return _select("aquacrop_runs", user_id, limit=limit)


def list_recent_forecast_runs(user_id: str, limit: int = 10):
    return _select("forecast_runs", user_id, limit=limit)


def sanitize_aquacrop_results(results: dict) -> dict:
    """Strip DataFrames from AquaCrop output before JSON storage."""
    out = {}
    for key, val in results.items():
        if isinstance(val, pd.DataFrame):
            if val.empty:
                out[key] = []
            else:
                slim = val.head(400).copy()
                out[key] = _json_safe(slim.to_dict(orient="records"))
        else:
            out[key] = _json_safe(val)
    return out
