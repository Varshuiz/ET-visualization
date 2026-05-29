from .auth_supabase import is_authenticated


def supabase_auth(request):
    return {
        "supabase_logged_in": is_authenticated(request),
        "supabase_user_email": request.session.get("supabase_user_email"),
    }
