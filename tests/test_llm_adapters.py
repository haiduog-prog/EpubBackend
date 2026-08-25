import asyncio
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
async def test_gemini_fallback_uses_native_async_sdk_call():
    provider = object.__new__(GeminiProvider)
    provider.default_model = "preferred-model"
    provider.working_model = None
    provider.failed_models = set()

    calls = []

    async def generate_content(**kwargs):
        calls.append(kwargs["model"])
        return SimpleNamespace(text="ok")

    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    response = await provider._call_with_fallback("text", object())

    assert response.text == "ok"
    assert calls == ["preferred-model"]


@pytest.mark.asyncio
async def test_gemini_native_async_call_is_cancelled_by_deadline():
    provider = object.__new__(GeminiProvider)
    provider.default_model = "preferred-model"
    provider.working_model = None
    provider.failed_models = set()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def generate_content(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(provider._call_with_fallback("text", object()), timeout=0.01)

    assert started.is_set()
    assert cancelled.is_set()


def test_gemini_configures_sdk_request_timeout_in_milliseconds(monkeypatch):
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("app.llm.gemini_provider.genai.Client", fake_client)

    GeminiProvider(
        api_key="dummy",
        model="preferred-model",
        request_timeout_seconds=45.0,
    )

    assert captured["http_options"].timeout == 45_000
