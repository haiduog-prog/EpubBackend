import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.errors import GeminiRateLimitError, GeminiServiceUnavailableError
from app.llm.gemini_provider import (
    GeminiProvider,
    _GLOBAL_MODEL_COOLDOWNS,
    _normalise_provider_error,
    _parse_retry_after,
)
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.prompts.templates import PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA, PROMPT_4_QA_CHECK
from app.schemas.translation import QAIssue


@pytest.mark.asyncio
async def test_anthropic_extract_uses_instance_default_model(monkeypatch):
    provider = AnthropicProvider(api_key="dummy", model="instance-model")
    provider._call_structured = AsyncMock(return_value=BookBibleDelta())

    await provider.extract_book_bible_delta("source", "known")

    assert provider._call_structured.await_args.kwargs["model"] == "instance-model"


@pytest.mark.asyncio
async def test_gemini_correction_uses_correction_prompt_and_preferred_model():
    provider = object.__new__(GeminiProvider)
    provider._call_with_fallback = AsyncMock(return_value=SimpleNamespace(text="fixed"))
    issue = QAIssue(issue="CJK", found="老师", expected="Sư phụ", location="老师")

    result = await provider.correct_translation_terms(
        "老师，我们走吧。",
        "老师, chúng ta đi thôi.",
        BookBible(),
        [issue],
        model="correction-model",
    )

    assert result == "fixed"
    kwargs = provider._call_with_fallback.await_args.kwargs
    assert kwargs["preferred_model"] == "correction-model"
    assert "Sư phụ" in kwargs["contents"]
    assert "老师, chúng ta đi thôi." in kwargs["contents"]


@pytest.mark.asyncio
async def test_anthropic_correction_returns_text_from_adapter():
    provider = AnthropicProvider(api_key="dummy", model="instance-model")
    provider.client.messages.create = AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(type="text", text="fixed")])
    )

    result = await provider.correct_translation_terms(
        "老师，我们走吧。",
        "老师, chúng ta đi thôi.",
        BookBible(),
        [QAIssue(issue="CJK", found="老师", expected="Sư phụ", location="老师")],
    )

    assert result == "fixed"
    assert provider.client.messages.create.await_args.kwargs["model"] == "instance-model"


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


def test_gemini_parses_retry_info_and_quota_scope():
    payload = {
        "error": {
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "12s",
                },
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaMetric": "GenerateContentRequestsPerDay"}],
                },
            ],
        }
    }
    error = _normalise_provider_error(APIError(429, payload), "gemini-flash-latest")

    assert isinstance(error, GeminiRateLimitError)
    assert error.retry_after_seconds == 12
    assert error.quota_scope == "GenerateContentRequestsPerDay"
    assert _parse_retry_after(payload) == 12


@pytest.mark.asyncio
async def test_gemini_does_not_bypass_global_cooldowns():
    provider = object.__new__(GeminiProvider)
    provider.default_model = "cool-model"
    provider.working_model = None
    provider.failed_models = set()
    provider.model_pool = ["cool-model", "cool-lite"]
    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock()))
    )
    _GLOBAL_MODEL_COOLDOWNS["cool-model"] = 9_999_999_999.0
    _GLOBAL_MODEL_COOLDOWNS["cool-lite"] = 9_999_999_999.0

    try:
        with pytest.raises(GeminiServiceUnavailableError):
            await provider._call_with_fallback("text", object())
        provider.client.aio.models.generate_content.assert_not_awaited()
    finally:
        _GLOBAL_MODEL_COOLDOWNS.pop("cool-model", None)
        _GLOBAL_MODEL_COOLDOWNS.pop("cool-lite", None)


@pytest.mark.asyncio
async def test_gemini_transient_failure_is_not_marked_permanently_failed():
    provider = object.__new__(GeminiProvider)
    provider.default_model = "first-model"
    provider.working_model = None
    provider.failed_models = set()
    provider.model_pool = ["first-model", "second-model"]
    calls = []

    async def generate_content(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "first-model":
            raise APIError(503, {"error": {"status": "UNAVAILABLE", "message": "high demand"}})
        return SimpleNamespace(text="ok")

    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    response = await provider._call_with_fallback("text", object())

    assert response.text == "ok"
    assert calls == ["first-model", "second-model"]
    assert "first-model" not in provider.failed_models
    _GLOBAL_MODEL_COOLDOWNS.pop("first-model", None)


@pytest.mark.asyncio
async def test_gemini_stops_fallback_for_project_wide_quota():
    provider = object.__new__(GeminiProvider)
    provider.default_model = "first-model"
    provider.working_model = None
    provider.failed_models = set()
    provider.model_pool = ["first-model", "second-model"]
    calls = []

    async def generate_content(**kwargs):
        calls.append(kwargs["model"])
        raise APIError(
            429,
            {
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "daily quota exceeded",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [{"quotaMetric": "GenerateContentRequestsPerDay"}],
                        }
                    ],
                }
            },
        )

    provider.client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    try:
        with pytest.raises(GeminiRateLimitError):
            await provider._call_with_fallback("text", object())
        assert calls == ["first-model"]
    finally:
        _GLOBAL_MODEL_COOLDOWNS.pop("first-model", None)
        _GLOBAL_MODEL_COOLDOWNS.pop("second-model", None)
