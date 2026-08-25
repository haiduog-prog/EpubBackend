from typing import Optional
from app.config import settings
from app.llm.base import BaseLLMClient
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.gemini_provider import GeminiProvider


def create_llm_client(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    request_timeout_seconds: Optional[float] = None,
) -> BaseLLMClient:
    """
    Factory function để khởi tạo instance của LLM Provider (Mặc định: Google Gemini).
    """
    prov = (provider or settings.default_provider or "gemini").strip().lower()

    if prov == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model)
    if prov == "gemini":
        return GeminiProvider(
            api_key=api_key,
            model=model,
            request_timeout_seconds=request_timeout_seconds,
        )
    raise ValueError(f"Unsupported LLM provider: {provider}")
