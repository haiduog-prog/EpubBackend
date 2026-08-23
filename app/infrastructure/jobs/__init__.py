"""Shared infrastructure for bounded background work."""

from app.infrastructure.jobs.limiter import limited_background_work

__all__ = ["limited_background_work"]
