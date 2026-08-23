"""Shared API dependencies for protecting state-changing operations."""

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException

from app.config import settings


def require_write_access(
    x_book_bible_client_key: Optional[str] = Header(default=None),
) -> None:
    """Require the configured write credential outside local development.

    The default development mode intentionally remains convenient for local CLI
    and test usage. Deployments must set ``APP_ENV`` and the token explicitly.
    """
    expected = os.getenv("BOOK_BIBLE_WRITE_TOKEN", settings.book_bible_write_token).strip()
    app_env = os.getenv("APP_ENV", settings.app_env)
    protected_environment = app_env.lower() in {
        "production",
        "prod",
        "staging",
    }
    if not expected:
        if protected_environment:
            raise HTTPException(status_code=503, detail="Write authentication is not configured.")
        return
    if not x_book_bible_client_key:
        raise HTTPException(status_code=401, detail="Trusted client credential required.")
    if not hmac.compare_digest(expected, x_book_bible_client_key):
        raise HTTPException(status_code=403, detail="Invalid client credential.")
