from pathlib import Path

import pytest

from app.infrastructure.cache.direct_translation import DirectTranslationCache
from app.modules.translation.application.qa_service import QAService
from app.prompts import PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA
from app.schemas.book_bible import BookBible, CharacterEntry
from app.schemas.translation import QAReport
from app.services.prompts import PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA as LegacyPrompt


class FakeQAClient:
    def __init__(self):
        self.calls = 0

    async def qa_check_chunk(self, translated_chunk, book_bible, model=None):
        self.calls += 1
        return QAReport(issues=[])


@pytest.mark.asyncio
async def test_qa_skips_ai_when_rule_check_is_clean():
    client = FakeQAClient()
    service = QAService(client)
    bible = BookBible(novel_id="qa-test")

    result = await service.verify_chunk("hello", "xin chao", bible)

    assert result.issues == []
    assert client.calls == 0


@pytest.mark.asyncio
async def test_qa_calls_ai_when_original_name_leaks():
    client = FakeQAClient()
    service = QAService(client)
    bible = BookBible(
        novel_id="qa-test",
        characters=[CharacterEntry(original_name="Xiao Yan", vi_name="Tiêu Viêm")],
    )

    result = await service.verify_chunk("Xiao Yan arrived", "Xiao Yan đến", bible)

    assert len(result.issues) == 1
    assert client.calls == 1


def test_direct_translation_cache_roundtrip_and_revision_invalidation(tmp_path: Path):
    cache = DirectTranslationCache(str(tmp_path))
    bible = BookBible(novel_id="cache-test", bible_revision=3)

    cache.put("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 2, "xin chao", bible)

    assert cache.get("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 2)["translated_text"] == "xin chao"
    assert cache.get("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 1) is None


def test_prompt_compatibility_export_is_canonical():
    assert LegacyPrompt == PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA
