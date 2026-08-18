from typing import Optional
from app.config import settings
from app.llm.base import BaseLLMClient
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.gemini_provider import GeminiProvider


def create_llm_client(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None
) -> BaseLLMClient:
    """
    Factory function để khởi tạo instance của LLM Provider (Mặc định: Google Gemini).
    """
    prov = (provider or "").lower()
    
    # Auto-detect or default to Gemini
    if not prov:
        if api_key and not api_key.startswith("AIzaSy") and settings.anthropic_api_key:
            prov = "anthropic"
        else:
            prov = "gemini"

    if prov == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(api_key=api_key, model=model)
    else:
        # Default all LLM calls to Gemini
        return GeminiProvider(api_key=api_key, model=model)
