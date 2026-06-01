"""High-level hooks from Django views into Supabase (fail-safe)."""

from __future__ import annotations

import logging
import threading

from .auth_supabase import get_current_user_id, normalize_user_id
from .supabase_client import supabase_configured
from . import supabase_storage as store

logger = logging.getLogger(__name__)


def _user_and_farm(request):
    user_id = normalize_user_id(get_current_user_id(request))
    if not user_id or not supabase_configured():
        return None, None
    farm_id = store.resolve_farm_id(request, user_id)
    return user_id, farm_id


def log_feature_usage(request, feature: str, action: str, metadata: dict | None = None) -> None:
    """
    Record usage without blocking the HTTP response (Supabase insert in background).
    """
    user_id = normalize_user_id(get_current_user_id(request))
    if not user_id or not supabase_configured():
        return
    email = request.session.get("supabase_user_email")
    meta = dict(metadata or {})

    def _worker() -> None:
        try:
            store.log_usage(
                user_id=user_id,
                feature=feature,
                action=action,
                metadata=meta,
                hash_email=email,
            )
        except Exception as exc:
            logger.debug("log_feature_usage background failed: %s", exc)

    threading.Thread(target=_worker, daemon=True).start()


def persist_et_run(request, *, inputs: dict, result_data: dict) -> None:
    user_id, farm_id = _user_and_farm(request)
    if not user_id:
        return
    try:
        store.save_et_calculation(
            user_id=user_id,
            farm_id=farm_id,
            inputs=inputs,
            result_data=result_data,
        )
        log_feature_usage(request, "et_calculator", "run", {"farm_id": farm_id})
    except Exception as exc:
        logger.warning("persist_et_run failed: %s", exc)


def persist_aquacrop_run(
    request,
    *,
    mode: str,
    crop_type: str,
    start_date: str,
    end_date: str,
    results: dict,
    extra: dict | None = None,
) -> None:
    user_id, farm_id = _user_and_farm(request)
    if not user_id:
        return
    try:
        payload = store.compact_aquacrop_result_data(results, extra=extra)
        saved = store.save_aquacrop_run(
            user_id=user_id,
            farm_id=farm_id,
            mode=mode,
            crop_type=crop_type,
            start_date=start_date,
            end_date=end_date,
            result_data=payload,
        )
        if not saved:
            logger.warning("save_aquacrop_run returned no row for user %s", user_id)
        log_feature_usage(
            request,
            "aquacrop",
            "run",
            {"mode": mode, "crop": crop_type, "farm_id": farm_id},
        )
    except Exception as exc:
        logger.warning("persist_aquacrop_run failed: %s", exc)


def persist_forecast_run(
    request,
    *,
    province: str,
    city: str,
    forecast_days: int,
    et_method: str,
    result_data: dict,
) -> None:
    user_id, farm_id = _user_and_farm(request)
    if not user_id:
        return
    try:
        store.save_forecast_run(
            user_id=user_id,
            farm_id=farm_id,
            province=province,
            city=city,
            forecast_days=forecast_days,
            et_method=et_method,
            result_data=result_data,
        )
        log_feature_usage(
            request,
            "forecast",
            "run",
            {
                "province": province,
                "city": city,
                "days": forecast_days,
                "farm_id": farm_id,
            },
        )
    except Exception as exc:
        logger.warning("persist_forecast_run failed: %s", exc)
