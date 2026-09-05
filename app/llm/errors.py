"""Typed errors exposed by LLM providers.

The translation API uses these errors to preserve the provider's retry
semantics instead of reducing every upstream failure to a generic 400.
"""

from __future__ import annotations

from typing import Any, Optional


class GeminiProviderError(RuntimeError):
    """Base error for a Gemini request that could not be completed."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        status: Optional[str] = None,
        model: Optional[str] = None,
        retryable: bool = False,
        retry_after_seconds: Optional[float] = None,
        quota_scope: Optional[str] = None,
        attempts: Optional[list[str]] = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.model = model
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.quota_scope = quota_scope
        self.attempts = attempts or []
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        """Return a safe, provider-independent representation for an API body."""

        return {
            "code": self.error_code,
            "message": str(self),
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "quota_scope": self.quota_scope,
            "model": self.model,
            "attempts": self.attempts,
        }

    @property
    def error_code(self) -> str:
        return "GEMINI_PROVIDER_ERROR"


class GeminiRateLimitError(GeminiProviderError):
    """Gemini rejected the request because a project/model quota was exceeded."""

    @property
    def error_code(self) -> str:
        return "GEMINI_RATE_LIMITED"


class GeminiServiceUnavailableError(GeminiProviderError):
    """Gemini was temporarily unavailable or timed out."""

    @property
    def error_code(self) -> str:
        return "GEMINI_UNAVAILABLE"


class GeminiModelUnavailableError(GeminiProviderError):
    """No configured model is available for the current API key/project."""

    @property
    def error_code(self) -> str:
        return "GEMINI_MODEL_UNAVAILABLE"



class StructuredOutputError(ValueError):
    """LLM returned a response that could not be validated against its schema."""

    def __init__(self, message: str, *, operation: str = "", details: Any = None) -> None:
        super().__init__(message)
        self.operation = operation
        self.details = details


class LLMResponseError(RuntimeError):
    """The provider answered, but the answer is not safe to persist."""

    def __init__(self, message: str, *, operation: str = "", stop_reason: Any = None) -> None:
        super().__init__(message)
        self.operation = operation
        self.stop_reason = stop_reason


class IncompleteLLMResponseError(LLMResponseError):
    """The provider stopped before producing a complete response."""


class EmptyLLMResponseError(LLMResponseError):
    """The provider returned no usable text."""
