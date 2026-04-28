from supabase import create_client

from app.config import settings


if not settings.supabase_url or not settings.supabase_secret_key:
    raise RuntimeError(
        "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SECRET_KEY "
        "in your environment or .env file before starting the app."
    )


# The backend intentionally uses the secret key so it can bypass RLS.
supabase_client = create_client(settings.supabase_url, settings.supabase_secret_key)
