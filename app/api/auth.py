import os

from fastapi import APIRouter, HTTPException

from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _local_auth_enabled() -> bool:
    """Allow the backend-owned local identity only in local development."""
    app_env = os.getenv("APP_ENV", settings.app_env).strip().lower()
    return app_env in {"development", "dev", "local", "test"} and not settings.auth_required


@router.get("/config")
def auth_config():
    """Return the auth mode selected by the backend.

    Browser code never owns auth configuration. In local development the
    backend explicitly advertises a local identity so the reader can run
    without a Supabase project. Production still fails closed when the
    Supabase URL or publishable key is missing.
    """
    if settings.supabase_url and settings.supabase_publishable_key:
        return {
            "mode": "supabase",
            "auth_required": True,
            "supabase_url": settings.supabase_url.rstrip("/"),
            "supabase_publishable_key": settings.supabase_publishable_key,
        }
    if _local_auth_enabled():
        return {"mode": "local", "auth_required": False}
    raise HTTPException(
        status_code=503,
        detail=(
            "Authentication is not configured. Set SUPABASE_URL and "
            "SUPABASE_PUBLISHABLE_KEY on the backend."
        ),
    )
