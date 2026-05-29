"""Farmer dashboard, run history details, and farm profile."""

from __future__ import annotations

import json
from typing import Any

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .auth_supabase import SESSION_ACTIVE_FARM_ID, get_current_user_id, normalize_user_id, supabase_login_required
from .forms_auth import FarmProfileForm
from .et_results_display import comparison_context_from_saved_row, parse_run_result_data
from .persistence import log_feature_usage
from .supabase_storage import (
    get_aquacrop_run_by_id,
    get_et_calculation_by_id,
    get_farm_for_user,
    get_forecast_run_by_id,
    get_profile,
    list_recent_aquacrop_runs,
    list_recent_et_calculations,
    list_recent_forecast_runs,
    save_farm,
)


def _first_name(full_name: str) -> str:
    name = (full_name or "").strip()
    if not name:
        return ""
    return name.split()[0]


def _possessive(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "Your"
    if name[-1].lower() == "s":
        return f"{name}'"
    return f"{name}'s"


def _pretty_json(data: Any) -> str:
    if data is None:
        return ""
    try:
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(data)


def _dashboard_profile_context(user_id: str) -> dict[str, str]:
    profile = get_profile(user_id) if user_id else None
    full_name = (profile or {}).get("full_name", "").strip()
    first = _first_name(full_name)
    return {
        "profile": profile,
        "full_name": full_name,
        "first_name": first,
        "possessive_name": _possessive(first or full_name),
    }


@supabase_login_required
def dashboard_view(request):
    user_id = get_current_user_id(request)
    farm = get_farm_for_user(user_id) if user_id else None
    log_feature_usage(request, "dashboard", "view")

    context = {
        "farm": farm,
        "et_runs": list_recent_et_calculations(user_id, limit=8) if user_id else [],
        "aquacrop_runs": list_recent_aquacrop_runs(user_id, limit=8) if user_id else [],
        "forecast_runs": list_recent_forecast_runs(user_id, limit=8) if user_id else [],
        **_dashboard_profile_context(user_id or ""),
    }
    return render(request, "et/dashboard.html", context)


@supabase_login_required
def et_run_detail_view(request, run_id):
    user_id = get_current_user_id(request)
    row = get_et_calculation_by_id(user_id, str(run_id)) if user_id else None
    if not row:
        raise Http404("ET calculation not found.")

    log_feature_usage(request, "et_calculator", "view_saved", {"run_id": str(run_id)})
    ctx = comparison_context_from_saved_row(row)
    ctx["run"] = row
    ctx["is_saved_run"] = True
    return render(request, "et/run_et_detail.html", ctx)


@supabase_login_required
def et_run_download_csv_view(request, run_id):
    user_id = get_current_user_id(request)
    row = get_et_calculation_by_id(user_id, str(run_id)) if user_id else None
    if not row:
        raise Http404("ET calculation not found.")

    _, results = parse_run_result_data(row)
    csv_data = results.get("csv") or results.get("et_data_csv") or ""
    if not csv_data:
        return HttpResponse("No CSV data stored for this run.", status=404)

    log_feature_usage(request, "et_calculator", "export", {"format": "csv", "run_id": str(run_id)})
    response = HttpResponse(csv_data, content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="et_comparison_data.csv"'
    return response


@supabase_login_required
def aquacrop_run_detail_view(request, run_id):
    user_id = get_current_user_id(request)
    row = get_aquacrop_run_by_id(user_id, str(run_id)) if user_id else None
    if not row:
        raise Http404("AquaCrop run not found.")

    result_data = row.get("result_data") or {}
    if isinstance(result_data, str):
        try:
            result_data = json.loads(result_data)
        except json.JSONDecodeError:
            result_data = {}

    profile_ctx = _dashboard_profile_context(user_id or "")
    return render(
        request,
        "et/run_aquacrop_detail.html",
        {
            "run": row,
            "result_data": result_data,
            "result_json": _pretty_json(result_data),
            **profile_ctx,
        },
    )


@supabase_login_required
def forecast_run_detail_view(request, run_id):
    user_id = get_current_user_id(request)
    row = get_forecast_run_by_id(user_id, str(run_id)) if user_id else None
    if not row:
        raise Http404("Forecast run not found.")

    result_data = row.get("result_data") or {}
    if isinstance(result_data, str):
        try:
            result_data = json.loads(result_data)
        except json.JSONDecodeError:
            result_data = {}

    profile_ctx = _dashboard_profile_context(user_id or "")
    return render(
        request,
        "et/run_forecast_detail.html",
        {
            "run": row,
            "result_data": result_data,
            "result_json": _pretty_json(result_data),
            **profile_ctx,
        },
    )


@supabase_login_required
@require_http_methods(["GET", "POST"])
def farm_profile_view(request):
    user_id = get_current_user_id(request)
    if not user_id:
        messages.error(request, "Your session is missing a user id. Please sign out and sign in again.")
        return redirect(reverse("et:login"))

    existing = get_farm_for_user(user_id)

    initial = {}
    if existing:
        initial = {
            "farm_name": existing.get("farm_name", ""),
            "province": existing.get("province", ""),
            "city": existing.get("city", ""),
            "area_hectares": existing.get("area_hectares"),
            "crop_type": existing.get("crop_type", ""),
            "irrigation_type": existing.get("irrigation_type", ""),
        }

    form = FarmProfileForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        area = form.cleaned_data.get("area_hectares")
        saved = save_farm(
            user_id=normalize_user_id(user_id),
            farm_id=str(existing["id"]) if existing and existing.get("id") else None,
            farm_name=form.cleaned_data["farm_name"],
            province=form.cleaned_data["province"],
            city=form.cleaned_data["city"],
            area_hectares=float(area) if area is not None else None,
            crop_type=form.cleaned_data.get("crop_type") or "",
            irrigation_type=form.cleaned_data.get("irrigation_type") or "",
        )
        if saved and saved.get("id"):
            request.session[SESSION_ACTIVE_FARM_ID] = str(saved["id"])
            request.session.modified = True
            messages.success(request, "Farm profile saved.")
            log_feature_usage(request, "farm_profile", "run")
            return redirect(reverse("et:dashboard"))

        refetched = get_farm_for_user(user_id)
        if refetched and refetched.get("id"):
            request.session[SESSION_ACTIVE_FARM_ID] = str(refetched["id"])
            request.session.modified = True
            messages.success(request, "Farm profile saved.")
            log_feature_usage(request, "farm_profile", "run")
            return redirect(reverse("et:dashboard"))

        messages.error(
            request,
            "Could not save farm profile. Confirm Supabase tables exist (run supabase/schema.sql) "
            "and check server logs for details.",
        )

    return render(request, "et/farm_profile.html", {"form": form, "farm": existing})
