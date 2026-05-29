"""Supabase session auth helpers and login decorator."""

from __future__ import annotations

import hashlib
import logging
from functools import wraps

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

logger = logging.getLogger(__name__)

SESSION_USER_ID = "supabase_user_id"
SESSION_ACCESS_TOKEN = "supabase_access_token"
SESSION_REFRESH_TOKEN = "supabase_refresh_token"
SESSION_USER_EMAIL = "supabase_user_email"
SESSION_ACTIVE_FARM_ID = "active_farm_id"


def hash_sensitive(value: str | None) -> str | None:
    """One-way hash for storing identifiers in usage metadata."""
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def normalize_user_id(user_id) -> str | None:
    """Canonical UUID string for Supabase user_id columns."""
    if user_id is None:
        return None
    uid = str(user_id).strip()
    return uid or None


def get_current_user_id(request) -> str | None:
    return normalize_user_id(request.session.get(SESSION_USER_ID))


def is_authenticated(request) -> bool:
    return bool(get_current_user_id(request))


def set_supabase_session(request, *, user_id: str, email: str, access_token: str, refresh_token: str | None):
    uid = normalize_user_id(user_id)
    if not uid:
        raise ValueError("Invalid Supabase user id for session")
    request.session[SESSION_USER_ID] = uid
    request.session[SESSION_USER_EMAIL] = email
    request.session[SESSION_ACCESS_TOKEN] = access_token
    if refresh_token:
        request.session[SESSION_REFRESH_TOKEN] = refresh_token
    request.session.modified = True


def clear_supabase_session(request):
    for key in (
        SESSION_USER_ID,
        SESSION_ACCESS_TOKEN,
        SESSION_REFRESH_TOKEN,
        SESSION_USER_EMAIL,
        SESSION_ACTIVE_FARM_ID,
    ):
        request.session.pop(key, None)
    request.session.modified = True


def supabase_login_required(view_func):
    """Redirect anonymous users to login; preserve ?next=."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_authenticated(request):
            login_url = reverse("et:login")
            next_url = request.get_full_path()
            return redirect(f"{login_url}?next={next_url}")
        return view_func(request, *args, **kwargs)

    return _wrapped


def login_required_if_supabase_configured(view_func):
    """Enforce login when Supabase is configured; otherwise allow local dev without cloud."""

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if settings.SUPABASE_ENFORCE_AUTH and not is_authenticated(request):
            login_url = reverse("et:login")
            next_url = request.get_full_path()
            return redirect(f"{login_url}?next={next_url}")
        return view_func(request, *args, **kwargs)

    return _wrapped
