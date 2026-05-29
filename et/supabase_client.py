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


def get_service_client():
    """
    Server-side admin client (service role). Bypasses RLS — always filter by user_id in queries.
    Not cached so .env / settings always apply after load_dotenv.
    """
    key = getattr(settings, "SUPABASE_SERVICE_KEY", "") or ""
    return _create_client(key)


def get_anon_client():
    """Used only for Supabase Auth sign-up / sign-in on the server."""
    key = getattr(settings, "SUPABASE_ANON_KEY", "") or ""
    return _create_client(key)


def supabase_configured() -> bool:
    return bool(
        (getattr(settings, "SUPABASE_URL", "") or "").strip()
        and (getattr(settings, "SUPABASE_SERVICE_KEY", "") or "").strip()
        and (getattr(settings, "SUPABASE_ANON_KEY", "") or "").strip()
    )
