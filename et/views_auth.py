"""Register, login, logout via Supabase Auth."""

from __future__ import annotations

import logging
import threading
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from django_ratelimit.decorators import ratelimit

from .auth_supabase import (
    SESSION_ACCESS_TOKEN,
    SESSION_REFRESH_TOKEN,
    SESSION_USER_EMAIL,
    clear_supabase_session,
    is_authenticated,
    set_supabase_session,
)
from .forms_auth import (
    PASSWORD_TOO_SHORT,
    RESET_LINK_INVALID,
    ChangePasswordForm,
    ForgotPasswordForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm,
)
from .supabase_client import create_anon_client, get_anon_client, supabase_configured
from .supabase_storage import upsert_profile

logger = logging.getLogger(__name__)


def _safe_next(request):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return reverse("et:dashboard")


_RECOVERY_QUERY_KEYS = (
    "code",
    "token_hash",
    "type",
    "access_token",
    "refresh_token",
    "error",
    "error_code",
    "error_description",
)


def _forwarded_recovery_query(request) -> str:
    """Keep a Supabase recovery callback when an earlier page redirects."""
    recovery_type = (request.GET.get("type") or "").strip().lower()
    has_credential = any(
        (request.GET.get(key) or "").strip()
        for key in ("code", "token_hash", "access_token")
    )
    if recovery_type != "recovery" and not has_credential:
        return ""
    pairs = []
    for key in _RECOVERY_QUERY_KEYS:
        value = (request.GET.get(key) or "").strip()
        if value:
            pairs.append((key, value))
    return urlencode(pairs)


def _redirect_recovery_query(request):
    forwarded = _forwarded_recovery_query(request)
    if not forwarded:
        return None
    return redirect(f"{reverse('et:reset_password')}?{forwarded}")


def root_redirect_view(request):
    """Send site root to dashboard when signed in, otherwise to login."""
    recovery = _redirect_recovery_query(request)
    if recovery is not None:
        return recovery
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
    if request.method == "GET":
        recovery = _redirect_recovery_query(request)
        if recovery is not None:
            return recovery
        if is_authenticated(request):
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
    if request.method == "GET":
        recovery = _redirect_recovery_query(request)
        if recovery is not None:
            return recovery
        if is_authenticated(request):
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
    """Public reset URL for this request. HTTPS on Render via SECURE_PROXY_SSL_HEADER."""
    return request.build_absolute_uri("/et/auth/reset-password/")


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


def _password_change_error(exc, *, expired: str, fallback: str) -> str:
    """Map Supabase auth failures to a short message for the password forms."""
    try:
        from supabase_auth.errors import AuthSessionMissingError
    except ImportError:
        AuthSessionMissingError = ()  # type: ignore[misc, assignment]
    if AuthSessionMissingError and isinstance(exc, AuthSessionMissingError):
        return expired
    code = str(getattr(exc, "code", "") or "")
    message = _auth_error_message(exc, "")
    lowered = message.lower()
    if code in {"otp_expired", "flow_state_expired", "bad_code_verifier"} or "expired" in lowered or (
        "invalid" in lowered and "password" not in lowered
    ):
        return expired
    if code == "weak_password" or "too short" in lowered or "at least 6" in lowered or "at least 8" in lowered:
        return PASSWORD_TOO_SHORT
    if code == "same_password":
        return "Choose a different password than your current one."
    return message or fallback


def _remember_session(request, session, user) -> None:
    email = (getattr(user, "email", None) or request.session.get(SESSION_USER_EMAIL) or "").strip().lower()
    set_supabase_session(
        request,
        user_id=str(user.id),
        email=email,
        access_token=session.access_token,
        refresh_token=getattr(session, "refresh_token", None),
    )


def _session_from_recovery(client, *, access_token: str, refresh_token: str, token_hash: str, auth_code: str):
    """Turn a recovery link into a Supabase session on this server."""
    if token_hash:
        resp = client.auth.verify_otp({"token_hash": token_hash, "type": "recovery"})
    elif auth_code:
        resp = client.auth.exchange_code_for_session({"auth_code": auth_code})
    else:
        resp = client.auth.set_session(access_token, refresh_token)
    session = getattr(resp, "session", None)
    return session, _session_user(session)


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
            "auth_code": (request.GET.get("code") or "").strip(),
        }
        recovery_type = (request.GET.get("type") or "").strip().lower()
        if recovery_type and recovery_type != "recovery":
            # A PKCE callback is only `?code=`. Ignore other grant types' tokens.
            initial = {"auth_code": initial["auth_code"]} if initial["auth_code"] else {}

    form = ResetPasswordForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data["password"]
        client = create_anon_client()
        if client is None:
            messages.error(request, "Supabase is not configured. Contact the administrator.")
        else:
            try:
                session, user = _session_from_recovery(
                    client,
                    access_token=form.cleaned_data["access_token"],
                    refresh_token=form.cleaned_data["refresh_token"],
                    token_hash=form.cleaned_data["token_hash"],
                    auth_code=form.cleaned_data["auth_code"],
                )
                if not session or not user:
                    messages.error(request, RESET_LINK_INVALID)
                else:
                    client.auth.update_user({"password": password})
                    _remember_session(request, session, user)
                    messages.success(request, "Your password has been updated.")
                    return redirect(reverse("et:dashboard"))
            except Exception as exc:
                logger.warning("Supabase password reset failed: %s", type(exc).__name__)
                messages.error(
                    request,
                    _password_change_error(
                        exc,
                        expired=RESET_LINK_INVALID,
                        fallback="Could not reset your password. Request a new link and try again.",
                    ),
                )

    return render(request, "et/auth/reset_password.html", {"form": form})


@ratelimit(key="ip", rate="10/m", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def change_password_view(request):
    """Let a signed-in user set a new password with Supabase update_user."""
    if not is_authenticated(request):
        login_url = reverse("et:login")
        return redirect(f"{login_url}?next={reverse('et:change_password')}")

    if not supabase_configured():
        messages.error(request, "Supabase is not configured. Contact the administrator.")
        return render(request, "et/auth/change_password.html", {"form": ChangePasswordForm()})

    form = ChangePasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        access_token = (request.session.get(SESSION_ACCESS_TOKEN) or "").strip()
        refresh_token = (request.session.get(SESSION_REFRESH_TOKEN) or "").strip()
        client = create_anon_client()
        if client is None:
            messages.error(request, "Supabase is not configured. Contact the administrator.")
        elif not access_token or not refresh_token:
            messages.error(
                request,
                "Your session has expired. Sign in again, then change your password.",
            )
        else:
            try:
                resp = client.auth.set_session(access_token, refresh_token)
                session = getattr(resp, "session", None)
                user = _session_user(session)
                if not session or not user:
                    messages.error(
                        request,
                        "Your session has expired. Sign in again, then change your password.",
                    )
                else:
                    client.auth.update_user({"password": form.cleaned_data["password"]})
                    _remember_session(request, session, user)
                    messages.success(request, "Your password has been updated.")
                    return redirect(reverse("et:dashboard"))
            except Exception as exc:
                logger.warning("Supabase password change failed: %s", type(exc).__name__)
                messages.error(
                    request,
                    _password_change_error(
                        exc,
                        expired="Your session has expired. Sign in again, then change your password.",
                        fallback="Could not change your password. Try again.",
                    ),
                )

    return render(request, "et/auth/change_password.html", {"form": form})
