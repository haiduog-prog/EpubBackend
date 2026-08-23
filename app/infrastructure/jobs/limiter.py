import asyncio
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from app.config import settings


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])
_background_work_semaphore: asyncio.Semaphore | None = None
_background_work_loop: asyncio.AbstractEventLoop | None = None


def _get_background_work_semaphore() -> asyncio.Semaphore:
    """Keep the limiter bound to the active event loop (important for tests/workers)."""
    global _background_work_loop, _background_work_semaphore

    loop = asyncio.get_running_loop()
    if _background_work_semaphore is None or _background_work_loop is not loop:
        _background_work_loop = loop
        _background_work_semaphore = asyncio.Semaphore(
            max(1, settings.max_concurrent_background_jobs)
        )
    return _background_work_semaphore


def limited_background_work(func: F) -> F:
    """Bound concurrent LLM/file work while preserving the public coroutine API."""
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        async with _get_background_work_semaphore():
            return await func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
