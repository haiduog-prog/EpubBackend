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
    Factory function để khởi tạo instance của LLM Provider tương ứng (Anthropic hoặc Gemini).
    """
    prov = (provider or "").lower()
    
    # Auto-detect if provider not specified
    if not prov:
        if api_key:
            prov = "gemini" if api_key.startswith("AIzaSy") else "anthropic"
        elif settings.anthropic_api_key:
            prov = "anthropic"
        elif settings.gemini_api_key:
            prov = "gemini"
        else:
            prov = "anthropic"

    if prov == "gemini":
        return GeminiProvider(api_key=api_key, model=model)
    elif prov == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model)
    else:
        raise ValueError(f"Provider '{provider}' không được hỗ trợ. Sử dụng 'anthropic' hoặc 'gemini'.")
