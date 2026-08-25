"""Supabase access-token verification and shared authentication helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional

import jwt
from fastapi import Header, HTTPException, Query

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    claims: Dict[str, Any]


def _unauthorized(detail: str = "Authentication required.") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _issuer() -> str:
    configured = (settings.supabase_jwt_issuer or "").strip().rstrip("/")
    return configured or (f"{settings.supabase_url.rstrip('/')}/auth/v1" if settings.supabase_url else "")


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=max(30, settings.supabase_jwks_cache_seconds), timeout=5)


def _decode_access_token(token: str) -> AuthUser:
    issuer = _issuer()
    if not settings.supabase_url or not issuer:
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    try:
        header = jwt.get_unverified_header(token)
        algorithm = str(header.get("alg") or "")
        if algorithm in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}:
            jwks_url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
            signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, signing_key, algorithms=[algorithm], audience=settings.supabase_jwt_audience, issuer=issuer, options={"require": ["exp", "sub", "aud", "iss"]})
        elif algorithm == "HS256" and settings.supabase_jwt_secret:
            claims = jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], audience=settings.supabase_jwt_audience, issuer=issuer, options={"require": ["exp", "sub", "aud", "iss"]})
        else:
            raise _unauthorized("Unsupported access token.")
    except HTTPException:
        raise
    except jwt.PyJWKClientError as exc:
        logger.warning("Supabase JWKS unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Authentication service is temporarily unavailable.") from exc
    except (jwt.PyJWTError, OSError, TimeoutError, ConnectionError) as exc:
        logger.info("Supabase access-token verification failed: %s", exc)
        raise _unauthorized("Invalid or expired access token.") from exc
    user_id = str(claims.get("sub") or "").strip()
    if not user_id:
        raise _unauthorized("Invalid access token.")
    return AuthUser(user_id=user_id, claims=dict(claims))


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
    access_token: Optional[str] = Query(default=None),
) -> AuthUser:
    value = authorization.strip() if isinstance(authorization, str) else ""
    if not value and (token or access_token):
        param_token = (token or access_token or "").strip()
        if param_token:
            value = f"Bearer {param_token}"
    if not value:
        app_env = os.getenv("APP_ENV", settings.app_env).lower()
        required = settings.auth_required or app_env not in {"development", "dev", "local", "test"}
        if not required:
            return AuthUser(user_id="local-development-user", claims={"sub": "local-development-user"})
        raise _unauthorized()
    scheme, _, token_val = value.partition(" ")
    if scheme.lower() != "bearer" or not token_val.strip():
        raise _unauthorized()
    return _decode_access_token(token_val.strip())


def get_optional_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
    access_token: Optional[str] = Query(default=None),
) -> Optional[AuthUser]:
    try:
        return get_current_user(authorization=authorization, token=token, access_token=access_token)
    except HTTPException:
        return None


__all__ = ["AuthUser", "get_current_user", "get_optional_current_user"]