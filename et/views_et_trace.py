"""Page that shows the inputs and formula behind each ET Results method."""

from __future__ import annotations

import io

import pandas as pd
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from .auth_supabase import get_current_user_id
from .et_calculation_trace import (
    METHOD_LABELS,
    METHOD_ORDER,
    build_method_trace,
    method_spec,
    normalize_method,
    prepare_et_results_frame,
    query_url,
    trace_csv,
    trace_zip,
)
from .et_results_display import build_acis_location, parse_run_result_data
from .et_units import get_unit_info
from .supabase_storage import get_et_calculation_by_id


def _dates_match(left, right) -> bool:
    return str(left or "")[:10] == str(right or "")[:10]


def _session_matches_saved(session_location: dict, saved_location: dict) -> bool:
    if not session_location or not saved_location:
        return False
    if not _dates_match(session_location.get("start_date"), saved_location.get("start_date")):
        return False
    if not _dates_match(session_location.get("end_date"), saved_location.get("end_date")):
        return False
    saved_lat = saved_location.get("latitude")
    session_lat = session_location.get("latitude")
    if saved_lat in (None, "") or session_lat in (None, ""):
        return True
    try:
        return abs(float(saved_lat) - float(session_lat)) < 0.02
    except (TypeError, ValueError):
        return False


def _load_saved_location(request, run_id: str) -> dict | None:
    user_id = get_current_user_id(request)
    if not user_id or not run_id:
        return None
    row = get_et_calculation_by_id(user_id, run_id)
    if not row:
        return None
    inputs, results = parse_run_result_data(row)
    location = build_acis_location(row, inputs, results)
    location["run_id"] = run_id
    return location


def _load_weather_frame(request):
    """Return (frame, location, unit, run_id, missing_weather)."""
    selected_unit = request.GET.get("unit", "mm")
    if selected_unit not in ("mm", "inches"):
        selected_unit = "mm"
    run_id = (request.GET.get("run") or "").strip()
    session_location = request.session.get("acis_location") or {}
    saved_location = _load_saved_location(request, run_id) if run_id else None
    location = saved_location or session_location
    raw = request.session.get("acis_data")

    use_session = bool(raw) and (
        not saved_location or _session_matches_saved(session_location, saved_location)
    )
    if not use_session:
        return None, location, selected_unit, run_id, True

    try:
        df = pd.read_json(io.StringIO(raw))
        latitude = (location or {}).get("latitude", session_location.get("latitude", 49.7))
        frame = prepare_et_results_frame(df, latitude)
    except (TypeError, ValueError, KeyError):
        return None, location or session_location, selected_unit, run_id, True
    if frame is None or len(frame) == 0:
        return None, location or session_location, selected_unit, run_id, True
    return frame, location or session_location, selected_unit, run_id, False


def _page_urls(request, method: str, unit: str, run_id: str) -> dict:
    page = reverse("et:et_calculation_trace")
    download = reverse("et:et_calculation_trace_download")
    tabs = []
    for code in METHOD_ORDER:
        tabs.append(
            {
                "code": code,
                "label": METHOD_LABELS[code],
                "active": code == method,
                "url": query_url(page, method=code, unit=unit, run=run_id),
            }
        )
    if run_id:
        back = reverse("et:et_run_detail", args=[run_id])
    else:
        back = query_url(reverse("et:comparison_with_acis"), unit=unit)
    return {
        "tabs": tabs,
        "back_url": back,
        "download_url": query_url(download, method=method, unit=unit, run=run_id),
        "download_all_url": query_url(download, method="all", unit=unit, run=run_id),
    }


def et_calculation_trace_view(request):
    method = normalize_method(request.GET.get("method"))
    frame, location, unit, run_id, missing = _load_weather_frame(request)
    if missing and not location:
        return redirect("et:acis_fetch")

    unit_info = get_unit_info(unit)
    latitude = 49.7
    if location and location.get("latitude") not in (None, ""):
        try:
            latitude = float(location["latitude"])
        except (TypeError, ValueError):
            latitude = 49.7

    spec = None
    if frame is not None and len(frame):
        try:
            spec = build_method_trace(frame, method, latitude, unit)
        except (TypeError, ValueError, KeyError):
            spec = None
            missing = True
    if spec is None:
        spec = method_spec(method, latitude, 766.0, unit_info["daily_label"])
        spec["headers"] = []
        spec["rows"] = []

    context = {
        "spec": spec,
        "location": location or {},
        "selected_unit": unit,
        "unit_info": unit_info,
        "method": method,
        "missing_weather": missing or not spec.get("rows"),
        "run_id": run_id,
        **_page_urls(request, method, unit, run_id),
    }
    return render(request, "et/et_calculation_trace.html", context)


def et_calculation_trace_download_view(request):
    method_raw = (request.GET.get("method") or "pm").strip().lower()
    frame, location, unit, _run_id, missing = _load_weather_frame(request)
    if missing or frame is None or not len(frame):
        return HttpResponse("No daily inputs are available for this result.", status=404)

    latitude = 49.7
    if location and location.get("latitude") not in (None, ""):
        try:
            latitude = float(location["latitude"])
        except (TypeError, ValueError):
            latitude = 49.7

    if method_raw == "all":
        payload = trace_zip(frame, latitude, unit)
        response = HttpResponse(payload, content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="et_method_inputs.zip"'
        return response

    method = normalize_method(method_raw)
    spec = build_method_trace(frame, method, latitude, unit)
    response = HttpResponse(trace_csv(spec), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{method}_inputs.csv"'
    return response
