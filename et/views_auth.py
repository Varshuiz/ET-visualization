"""Register, login, logout via Supabase Auth."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from django_ratelimit.decorators import ratelimit

from .auth_supabase import clear_supabase_session, set_supabase_session
from .forms_auth import LoginForm, RegisterForm
from .supabase_client import get_anon_client, supabase_configured
from .supabase_storage import upsert_profile

logger = logging.getLogger(__name__)


def _safe_next(request):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return reverse("et:dashboard")


@ratelimit(key="ip", rate="5/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def register_view(request):
    if not supabase_configured():
        messages.error(request, "Supabase is not configured. Contact the administrator.")
        return render(request, "et/auth/register.html", {"form": RegisterForm()})

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        client = get_anon_client()
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        full_name = form.cleaned_data["full_name"]
        try:
            resp = client.auth.sign_up({"email": email, "password": password})
            user = getattr(resp, "user", None)
            session = getattr(resp, "session", None)
            if user is None:
                messages.error(request, "Registration failed. Please try again.")
            else:
                upsert_profile(user_id=str(user.id), email=email, full_name=full_name)
                if session and getattr(session, "access_token", None):
                    set_supabase_session(
                        request,
                        user_id=str(user.id),
                        email=email,
                        access_token=session.access_token,
                        refresh_token=getattr(session, "refresh_token", None),
                    )
                    messages.success(request, "Account created. Welcome to AqualET!")
                    return redirect(_safe_next(request))
                messages.info(
                    request,
                    "Account created. Check your email to confirm, then sign in.",
                )
                return redirect(reverse("et:login"))
        except Exception as exc:
            logger.warning("Supabase sign_up failed: %s", exc)
            messages.error(request, f"Registration failed: {exc}")

    return render(request, "et/auth/register.html", {"form": form})


@ratelimit(key="ip", rate="10/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def login_view(request):
    if not supabase_configured():
        messages.error(request, "Supabase is not configured. Contact the administrator.")
        return render(request, "et/auth/login.html", {"form": LoginForm()})

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        client = get_anon_client()
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        try:
            resp = client.auth.sign_in_with_password({"email": email, "password": password})
            session = getattr(resp, "session", None)
            user = getattr(resp, "user", None)
            if not session or not user:
                messages.error(request, "Invalid email or password.")
            else:
                set_supabase_session(
                    request,
                    user_id=str(user.id),
                    email=email,
                    access_token=session.access_token,
                    refresh_token=getattr(session, "refresh_token", None),
                )
                upsert_profile(user_id=str(user.id), email=email)
                messages.success(request, "Signed in successfully.")
                return redirect(_safe_next(request))
        except Exception as exc:
            logger.warning("Supabase sign_in failed: %s", exc)
            messages.error(request, "Invalid email or password.")

    return render(
        request,
        "et/auth/login.html",
        {"form": form, "next": request.GET.get("next", "")},
    )


@require_http_methods(["GET", "POST"])
def logout_view(request):
    clear_supabase_session(request)
    messages.info(request, "You have been signed out.")
    return redirect(reverse("et:login"))
