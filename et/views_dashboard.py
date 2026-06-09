"""User dashboard, run history details, and location profile."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .auth_supabase import SESSION_ACTIVE_FARM_ID, get_current_user_id, normalize_user_id, supabase_login_required
from .forms_auth import FarmProfileForm
from .et_results_display import comparison_context_from_saved_row, parse_run_result_data
from .saved_run_display import aquacrop_context_from_saved_row, forecast_context_from_saved_row
from .persistence import log_feature_usage
from .crop_catalog import crop_catalog_context, crop_label, match_crop_slug
from .location_services import (
    AQUACROP_DEFAULT_PROVINCE,
    aquacrop_cities_by_province,
    resolve_aquacrop_region_fields,
)
from .supabase_storage import (
    DASHBOARD_AQUACROP_COLUMNS,
    DASHBOARD_ET_COLUMNS,
    DASHBOARD_FORECAST_COLUMNS,
    delete_aquacrop_run,
    delete_et_calculation,
    delete_forecast_run,
    delete_location_for_user,
    get_aquacrop_run_by_id,
    get_et_calculation_by_id,
    get_forecast_run_by_id,
    get_primary_location_for_user,
    get_profile,
    list_locations_for_user,
    list_recent_aquacrop_runs,
    list_recent_et_calculations,
    list_recent_forecast_runs,
    save_farm,
    set_primary_location,
    update_run_note,
)

DASHBOARD_RUN_LIMIT = 5

_DELETE_RUN_HANDLERS = {
    "et": (delete_et_calculation, "ET calculation"),
    "aquacrop": (delete_aquacrop_run, "AquaCrop run"),
    "forecast": (delete_forecast_run, "forecast run"),
}


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


def _profile_context_from_row(profile: dict | None) -> dict[str, str]:
    full_name = (profile or {}).get("full_name", "").strip()
    first = _first_name(full_name)
    return {
        "profile": profile,
        "full_name": full_name,
        "first_name": first,
        "possessive_name": _possessive(first or full_name),
    }


def _load_dashboard_supabase_data(user_id: str) -> dict[str, Any]:
    """Fetch farm, profile, and recent runs in parallel (slim columns, capped at 5 rows)."""
    if not user_id:
        return {
            "farm": None,
            "locations": [],
            "profile": None,
            "et_runs": [],
            "aquacrop_runs": [],
            "forecast_runs": [],
        }

    tasks = {
        "farm": lambda: get_primary_location_for_user(user_id),
        "locations": lambda: list_locations_for_user(user_id),
        "profile": lambda: get_profile(user_id),
        "et_runs": lambda: list_recent_et_calculations(
            user_id, limit=DASHBOARD_RUN_LIMIT, columns=DASHBOARD_ET_COLUMNS
        ),
        "aquacrop_runs": lambda: list_recent_aquacrop_runs(
            user_id, limit=DASHBOARD_RUN_LIMIT, columns=DASHBOARD_AQUACROP_COLUMNS
        ),
        "forecast_runs": lambda: list_recent_forecast_runs(
            user_id, limit=DASHBOARD_RUN_LIMIT, columns=DASHBOARD_FORECAST_COLUMNS
        ),
    }

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in futures:
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception:
                results[key] = None if key in ("farm", "profile") else []

    return results


@supabase_login_required
def dashboard_view(request):
    user_id = get_current_user_id(request)
    supabase_data = _load_dashboard_supabase_data(user_id)

    context = {
        "farm": supabase_data["farm"],
        "primary_location": supabase_data["farm"],
        "locations": supabase_data.get("locations") or [],
        "et_runs": supabase_data["et_runs"],
        "aquacrop_runs": supabase_data["aquacrop_runs"],
        "forecast_runs": supabase_data["forecast_runs"],
        **_profile_context_from_row(supabase_data["profile"]),
    }
    return render(request, "et/dashboard.html", context)


@supabase_login_required
@require_http_methods(["POST"])
def delete_run_view(request, run_type: str, run_id):
    handler = _DELETE_RUN_HANDLERS.get((run_type or "").strip().lower())
    redirect_url = reverse("et:dashboard") + "#recent-history"

    if not handler:
        messages.error(request, "Invalid calculation type.")
        return redirect(redirect_url)

    delete_fn, label = handler
    user_id = get_current_user_id(request)
    if not user_id:
        messages.error(request, "You must be signed in to delete calculations.")
        return redirect(reverse("et:login"))

    if delete_fn(user_id, str(run_id)):
        messages.success(request, f"Deleted {label}.")
        log_feature_usage(request, "dashboard", "delete_run", {"run_type": run_type, "run_id": str(run_id)})
    else:
        messages.error(request, f"Could not delete that {label}. It may have already been removed.")

    return redirect(redirect_url)


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

    log_feature_usage(request, "aquacrop", "view_saved", {"run_id": str(run_id)})
    ctx = aquacrop_context_from_saved_row(row)
    if not ctx.get("has_results"):
        return render(
            request,
            "et/run_aquacrop_detail.html",
            {
                "run": row,
                "result_data": _parse_result_data_row(row),
                "result_json": _pretty_json(_parse_result_data_row(row)),
                "display_warning": (
                    "This saved run does not include chart data. Run a new simulation to see full results."
                ),
            },
        )
    return render(request, "et/aquacrop_simulation.html", ctx)


@supabase_login_required
def forecast_run_detail_view(request, run_id):
    user_id = get_current_user_id(request)
    row = get_forecast_run_by_id(user_id, str(run_id)) if user_id else None
    if not row:
        raise Http404("Forecast run not found.")

    log_feature_usage(request, "forecast", "view_saved", {"run_id": str(run_id)})
    ctx = forecast_context_from_saved_row(row)
    if not ctx.get("df_forecast"):
        return render(
            request,
            "et/run_forecast_detail.html",
            {
                "run": row,
                "result_data": _parse_result_data_row(row),
                "result_json": _pretty_json(_parse_result_data_row(row)),
                "display_warning": (
                    "This saved run does not include forecast table data. Run a new forecast to see full results."
                ),
            },
        )
    return render(request, "et/env_canada_forecast.html", ctx)


def _parse_result_data_row(row: dict) -> dict:
    raw = row.get("result_data") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


_NOTE_TABLES = {
    "et": "et_calculations",
    "aquacrop": "aquacrop_runs",
    "forecast": "forecast_runs",
}


@supabase_login_required
@require_http_methods(["POST"])
def update_run_note_view(request, run_type: str, run_id):
    from django.http import JsonResponse

    user_id = get_current_user_id(request)
    table = _NOTE_TABLES.get((run_type or "").strip().lower())
    if not user_id or not table:
        return JsonResponse({"success": False, "error": "Invalid request."}, status=400)
    note = (request.POST.get("note") or "").strip()
    if update_run_note(table, user_id, str(run_id), note):
        return JsonResponse({"success": True, "note": note})
    return JsonResponse({"success": False, "error": "Could not save note."}, status=500)


@supabase_login_required
@require_http_methods(["GET", "POST"])
def farm_profile_view(request):
    user_id = get_current_user_id(request)
    if not user_id:
        messages.error(request, "Your session is missing a user id. Please sign out and sign in again.")
        return redirect(reverse("et:login"))

    locations = list_locations_for_user(user_id)
    edit_id = (request.GET.get("location") or request.POST.get("location_id") or "").strip()
    adding_new = edit_id in ("", "new") or request.GET.get("new") == "1"
    existing = None
    if edit_id and edit_id != "new":
        existing = next((loc for loc in locations if str(loc.get("id")) == str(edit_id)), None)

    is_save_post = request.method == "POST" and request.POST.get("action", "save") == "save"
    if is_save_post:
        region_province, region_city, city_choices = resolve_aquacrop_region_fields(
            post_province=request.POST.get("province"),
            post_city=request.POST.get("city"),
        )
    else:
        region_province, region_city, city_choices = resolve_aquacrop_region_fields(
            saved_province=(existing or {}).get("province"),
            saved_city=(existing or {}).get("city"),
        )

    initial = {"province": region_province}
    if existing:
        initial.update(
            {
                "farm_name": existing.get("farm_name", ""),
                "city": region_city,
                "area_hectares": existing.get("area_hectares"),
                "crop_type": match_crop_slug(existing.get("crop_type", "")),
                "soil_type": (existing.get("soil_type") or "").strip(),
            }
        )
    elif not locations:
        initial["province"] = AQUACROP_DEFAULT_PROVINCE

    if request.method == "POST" and request.POST.get("action") == "delete" and request.POST.get("location_id"):
        if delete_location_for_user(user_id, request.POST.get("location_id")):
            messages.success(request, "Location deleted.")
        else:
            messages.error(request, "Could not delete location.")
        return redirect(reverse("et:farm_profile"))

    if request.method == "POST" and request.POST.get("action") == "set_primary" and request.POST.get("location_id"):
        if set_primary_location(user_id, request.POST.get("location_id")):
            request.session[SESSION_ACTIVE_FARM_ID] = str(request.POST.get("location_id"))
            request.session.modified = True
            messages.success(request, "Primary location updated.")
        else:
            messages.error(request, "Could not set primary location.")
        return redirect(reverse("et:farm_profile"))

    form = FarmProfileForm(
        request.POST if is_save_post else None,
        initial=None if is_save_post else initial,
        city_choices=city_choices,
    )
    if is_save_post and form.is_valid():
        area = form.cleaned_data.get("area_hectares")
        crop_slug = match_crop_slug(form.cleaned_data.get("crop_type") or "")
        post_location_id = (request.POST.get("location_id") or "").strip()
        update_row = None
        if post_location_id:
            update_row = next(
                (loc for loc in locations if str(loc.get("id")) == post_location_id),
                None,
            )
        is_new = not update_row
        saved = save_farm(
            user_id=normalize_user_id(user_id),
            farm_id=str(update_row["id"]) if update_row and update_row.get("id") else None,
            farm_name=form.cleaned_data["farm_name"],
            province=form.cleaned_data["province"],
            city=form.cleaned_data["city"],
            area_hectares=float(area) if area is not None else None,
            crop_type=crop_slug,
            irrigation_type=(update_row.get("irrigation_type") or "") if update_row else "",
            soil_type=(form.cleaned_data.get("soil_type") or "").strip(),
            is_primary=True if is_new and not locations else None,
        )
        if saved and saved.get("id"):
            request.session[SESSION_ACTIVE_FARM_ID] = str(saved["id"])
            request.session.modified = True
            messages.success(
                request,
                "Location added." if is_new else "Location profile saved.",
            )
            log_feature_usage(request, "location_profile", "run")
            return redirect(f"{reverse('et:farm_profile')}?location={saved['id']}")

        messages.error(
            request,
            "Could not save location profile. Confirm Supabase tables exist and run migrations 004–005 if needed.",
        )

    numbered_locations = []
    for i, loc in enumerate(locations, start=1):
        numbered_locations.append({**loc, "display_name": loc.get("farm_name") or f"Location {i}"})

    profile = get_profile(user_id) if user_id else None
    primary_location = next((loc for loc in numbered_locations if loc.get("is_primary")), None)
    if not primary_location and numbered_locations:
        primary_location = numbered_locations[0]

    ctx = {
        "form": form,
        "farm": existing,
        "location": existing,
        "locations": numbered_locations,
        "primary_location": primary_location,
        "edit_location_id": str(existing.get("id")) if existing and existing.get("id") else "",
        "adding_new": adding_new and not existing,
        "cities_by_province": aquacrop_cities_by_province(),
        "selected_province": region_province,
        **crop_catalog_context(
            match_crop_slug(
                (request.POST.get("crop_type") if is_save_post else initial.get("crop_type")) or "spring_wheat"
            )
            or "spring_wheat"
        ),
        **_profile_context_from_row(profile),
    }
    return render(request, "et/farm_profile.html", ctx)
