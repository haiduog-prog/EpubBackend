"""HTTP mapping for provider failures shared by translation endpoints."""

from __future__ import annotations

from math import ceil

from fastapi import HTTPException

from app.llm.errors import (
    GeminiModelUnavailableError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiServiceUnavailableError,
)


def provider_http_exception(error: GeminiProviderError) -> HTTPException:
    """Expose retry semantics without leaking the raw SDK payload."""

    if isinstance(error, GeminiRateLimitError):
        status_code = 429
    elif isinstance(error, (GeminiServiceUnavailableError, GeminiModelUnavailableError)):
        # A model can be unavailable for this key/project even when Gemini is
        # generally healthy; this is still a server-side configuration issue.
        status_code = 503
    else:
        status_code = 502 if error.retryable else 500

    headers = {}
    if error.retry_after_seconds is not None and error.retry_after_seconds > 0:
        headers["Retry-After"] = str(max(1, ceil(error.retry_after_seconds)))

    detail = error.as_dict()
    detail["provider"] = "gemini"
    return HTTPException(status_code=status_code, detail=detail, headers=headers or None)


__all__ = ["provider_http_exception"]
