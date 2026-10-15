"""Register, login, logout via Supabase Auth."""

from __future__ import annotations

import logging
import threading

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from django_ratelimit.decorators import ratelimit

from .auth_supabase import clear_supabase_session, is_authenticated, set_supabase_session
from .forms_auth import ForgotPasswordForm, LoginForm, RegisterForm, ResetPasswordForm
from .supabase_client import create_anon_client, get_anon_client, supabase_configured
from .supabase_storage import upsert_profile

logger = logging.getLogger(__name__)


def _safe_next(request):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return reverse("et:dashboard")


def root_redirect_view(request):
    """Send site root to dashboard when signed in, otherwise to login."""
    if is_authenticated(request):
        return redirect(reverse("et:dashboard"))
    return redirect(reverse("et:login"))


def _upsert_profile_background(*, user_id: str, email: str, full_name: str | None = None) -> None:
    try:
        upsert_profile(user_id=user_id, email=email, full_name=full_name)
    except Exception:
        logger.exception("Background profile upsert failed for user %s", user_id)


@ratelimit(key="ip", rate="5/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.method == "GET" and is_authenticated(request):
        return redirect(_safe_next(request))

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
    if request.method == "GET" and is_authenticated(request):
        return redirect(_safe_next(request))

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
                threading.Thread(
                    target=_upsert_profile_background,
                    kwargs={"user_id": str(user.id), "email": email},
                    daemon=True,
                ).start()
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


def _recovery_redirect_url(request) -> str:
    return request.build_absolute_uri(reverse("et:reset_password"))


def _auth_error_message(exc, fallback: str) -> str:
    """Surface a short Supabase auth error without echoing tokens."""
    try:
        from supabase_auth.errors import AuthError
    except ImportError:
        return fallback
    if not isinstance(exc, AuthError):
        return fallback
    message = str(getattr(exc, "message", "") or "").strip()
    if not message or len(message) > 180 or "eyJ" in message:
        return fallback
    return message


@ratelimit(key="ip", rate="5/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def forgot_password_view(request):
    if not supabase_configured():
        messages.error(request, "Supabase is not configured. Contact the administrator.")
        return render(request, "et/auth/forgot_password.html", {"form": ForgotPasswordForm()})

    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        client = get_anon_client()
        try:
            client.auth.reset_password_for_email(
                email,
                {"redirect_to": _recovery_redirect_url(request)},
            )
        except Exception as exc:
            logger.warning("Supabase password recovery email failed: %s", exc)
            messages.error(
                request,
                "We couldn't send a reset email right now. Try again in a few minutes.",
            )
        else:
            messages.info(
                request,
                "If an account exists for that email, we sent a link to reset your password. Check your inbox.",
            )
            form = ForgotPasswordForm()

    return render(request, "et/auth/forgot_password.html", {"form": form})


def _session_user(session):
    user = getattr(session, "user", None)
    if user is None:
        return None
    user_id = getattr(user, "id", None)
    if not user_id:
        return None
    return user


@ratelimit(key="ip", rate="10/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def reset_password_view(request):
    if not supabase_configured():
        messages.error(request, "Supabase is not configured. Contact the administrator.")
        return render(request, "et/auth/reset_password.html", {"form": ResetPasswordForm()})

    initial = {}
    if request.method == "GET":
        initial = {
            "access_token": (request.GET.get("access_token") or "").strip(),
            "refresh_token": (request.GET.get("refresh_token") or "").strip(),
            "token_hash": (request.GET.get("token_hash") or "").strip(),
        }
        recovery_type = (request.GET.get("type") or "").strip().lower()
        if recovery_type and recovery_type != "recovery":
            initial = {}

    form = ResetPasswordForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data["password"]
        access_token = form.cleaned_data["access_token"]
        refresh_token = form.cleaned_data["refresh_token"]
        token_hash = form.cleaned_data["token_hash"]
        client = create_anon_client()
        if client is None:
            messages.error(request, "Supabase is not configured. Contact the administrator.")
        else:
            try:
                if token_hash:
                    resp = client.auth.verify_otp(
                        {"token_hash": token_hash, "type": "recovery"}
                    )
                else:
                    resp = client.auth.set_session(access_token, refresh_token)
                session = getattr(resp, "session", None)
                user = _session_user(session)
                if not session or not user:
                    messages.error(
                        request,
                        "This reset link is invalid or has expired. Request a new one.",
                    )
                else:
                    client.auth.update_user({"password": password})
                    email = (getattr(user, "email", None) or "").strip().lower()
                    set_supabase_session(
                        request,
                        user_id=str(user.id),
                        email=email,
                        access_token=session.access_token,
                        refresh_token=getattr(session, "refresh_token", None),
                    )
                    messages.success(request, "Your password has been updated.")
                    return redirect(reverse("et:dashboard"))
            except Exception as exc:
                logger.warning("Supabase password reset failed: %s", type(exc).__name__)
                messages.error(
                    request,
                    _auth_error_message(
                        exc,
                        "Could not reset your password. Request a new link and try again.",
                    ),
                )

    return render(request, "et/auth/reset_password.html", {"form": form})
