"""Supabase clients — service role is server-only; never expose to templates or static JS."""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _create_client(api_key: str):
    from supabase import create_client

    url = (getattr(settings, "SUPABASE_URL", "") or "").strip()
    key = (api_key or "").strip()
    if not url or not key:
        return None
    return create_client(url, key)


_service_client = None


def get_service_client():
    """
    Server-side admin client (service role). Bypasses RLS — always filter by user_id in queries.
    Cached — restarting the process picks up .env changes.
    """
    global _service_client
    if _service_client is None:
        key = getattr(settings, "SUPABASE_SERVICE_KEY", "") or ""
        _service_client = _create_client(key)
    return _service_client


_anon_client = None


def get_anon_client():
    """Used only for Supabase Auth sign-up / sign-in on the server (cached)."""
    global _anon_client
    if _anon_client is None:
        key = getattr(settings, "SUPABASE_ANON_KEY", "") or ""
        _anon_client = _create_client(key)
    return _anon_client


def supabase_configured() -> bool:
    return bool(
        (getattr(settings, "SUPABASE_URL", "") or "").strip()
        and (getattr(settings, "SUPABASE_SERVICE_KEY", "") or "").strip()
        and (getattr(settings, "SUPABASE_ANON_KEY", "") or "").strip()
    )
