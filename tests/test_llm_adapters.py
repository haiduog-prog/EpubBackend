from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.gemini_provider import GeminiProvider
from app.schemas.book_bible import BookBibleDelta
from app.prompts.templates import PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA, PROMPT_4_QA_CHECK


@pytest.mark.asyncio
async def test_anthropic_extract_uses_instance_default_model(monkeypatch):
    provider = AnthropicProvider(api_key="dummy", model="instance-model")
    provider._call_structured = AsyncMock(return_value=BookBibleDelta())

    await provider.extract_book_bible_delta("source", "known")

    assert provider._call_structured.await_args.kwargs["model"] == "instance-model"


def test_prompt_json_examples_survive_formatting():
    assert '"role": "Nam chính"' in PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA.format(
        known_names_index="known",
        source_text="source",
    )
    assert '"issues": [{"issue"' in PROMPT_4_QA_CHECK.format(
        book_bible_json="{}",
        translated_chunk="text",
    )


@pytest.mark.asyncio
async def test_gemini_fallback_runs_sync_sdk_call_off_event_loop():
    provider = object.__new__(GeminiProvider)
    provider.default_model = "preferred-model"
    provider.working_model = None
    provider.failed_models = set()

    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs["model"])
        return SimpleNamespace(text="ok")

    provider.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    response = await provider._call_with_fallback("text", object())

    assert response.text == "ok"
    assert calls == ["preferred-model"]
