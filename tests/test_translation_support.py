from pathlib import Path

import pytest

from app.infrastructure.cache.direct_translation import DirectTranslationCache
from app.modules.translation.application.qa_service import QAService
from app.prompts import PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA
from app.schemas.book_bible import AddressObservation, AddressTerm, BookBible, CharacterEntry
from app.schemas.translation import QAReport
from app.services.prompts import PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA as LegacyPrompt
from scripts.repair_cjk_address_terms import repair_bible


class FakeQAClient:
    def __init__(self):
        self.calls = 0

    async def qa_check_chunk(self, translated_chunk, book_bible, model=None):
        self.calls += 1
        return QAReport(issues=[])


class FakeCorrectionClient:
    def __init__(self, translated_text: str, corrected_text: str):
        self.translated_text = translated_text
        self.corrected_text = corrected_text
        self.correction_calls = 0

    async def translate_prose_chunk(self, **kwargs):
        return self.translated_text

    async def correct_translation_terms(self, **kwargs):
        self.correction_calls += 1
        return self.corrected_text


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


@pytest.mark.asyncio
async def test_quality_gate_corrects_cjk_once_and_rechecks():
    client = FakeCorrectionClient(
        'Tiêu Viêm: "老师, chúng ta đi thôi."',
        'Tiêu Viêm: "Sư phụ, chúng ta đi thôi."',
    )
    service = QAService(client)

    result = await service.translate_with_quality(
        "萧炎: 老师，我们走吧。",
        BookBible(novel_id="qa-test"),
    )

    assert result.translated_text == 'Tiêu Viêm: "Sư phụ, chúng ta đi thôi."'
    assert result.report.issues == []
    assert result.correction_attempted is True
    assert client.correction_calls == 1


def test_direct_translation_cache_roundtrip_and_revision_invalidation(tmp_path: Path):
    cache = DirectTranslationCache(str(tmp_path))
    bible = BookBible(novel_id="cache-test", bible_revision=3)

    cache.put("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 2, "xin chao", bible)

    assert cache.get("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 2)["translated_text"] == "xin chao"
    assert cache.get("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 1) is None


def test_direct_translation_cache_rejects_cjk_output(tmp_path: Path):
    cache = DirectTranslationCache(str(tmp_path))
    bible = BookBible(novel_id="cache-test", bible_revision=3)

    cache.put("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 2, "老师", bible)

    assert cache.get("cache-test", "hello", 1, "ch-1", "gemini", "model-a", 2) is None


def test_repair_bible_keeps_clean_data_and_rejects_legacy_cjk_terms():
    clean = BookBible(
        novel_id="clean",
        characters=[
            CharacterEntry(
                original_name="萧炎",
                vi_name="Tiêu Viêm",
                address_terms=[
                    AddressTerm(**{"with": "Dược Lão", "self": "ta", "other": "sư phụ"})
                ],
            )
        ],
    )
    repaired_clean, clean_counts, clean_changed = repair_bible(clean)

    assert clean_changed is False
    assert clean_counts == {"removed_address_terms": 0, "rejected_observations": 0}
    assert repaired_clean.bible_revision == clean.bible_revision

    dirty = BookBible(
        novel_id="dirty",
        characters=[
            CharacterEntry(
                original_name="萧炎",
                vi_name="Tiêu Viêm",
                address_terms=[
                    AddressTerm(**{"with": "Dược Lão", "self": "我", "other": "老师"})
                ],
            )
        ],
        address_observations=[
            AddressObservation(
                observation_id="dirty-observation",
                character_id="xiao-yan",
                self_term="老夫",
                other_term="好小子",
            )
        ],
    )
    repaired_dirty, dirty_counts, dirty_changed = repair_bible(dirty)

    assert dirty_changed is True
    assert dirty_counts == {"removed_address_terms": 1, "rejected_observations": 1}
    assert repaired_dirty.characters[0].address_terms == []
    assert repaired_dirty.address_observations[0].resolution == "rejected"
    assert repaired_dirty.bible_revision == dirty.bible_revision + 1


def test_direct_api_returns_needs_review_for_uncorrected_cjk(monkeypatch):
    from fastapi.testclient import TestClient

    from app.infrastructure.storage.facade import storage_repo
    from app.main import app
    from app.schemas.book_bible import BookBibleDelta

    novel_id = "qa-api-cjk-regression"

    class DirtyClient:
        async def extract_book_bible_delta(self, *args, **kwargs):
            return BookBibleDelta()

        async def translate_prose_chunk(self, **kwargs):
            return "Tiêu Viêm 老师 vẫn còn chữ gốc."

    monkeypatch.setattr(
        "app.modules.translation.api.create_llm_client",
        lambda **kwargs: DirtyClient(),
    )
    jobs_before = {job.job_id for job in storage_repo.list_jobs()}

    try:
        response = TestClient(app).post(
            "/api/v1/translate/text",
            json={"text": "萧炎 老师 chúng ta đi thôi.", "novel_id": novel_id},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["qa_status"] == "needs_review"
        assert any("CJK" in issue["issue"] for issue in payload["qa_issues"])
        assert {job.job_id for job in storage_repo.list_jobs()} == jobs_before
    finally:
        storage_repo.delete_bible(novel_id)


def test_prompt_compatibility_export_is_canonical():
    assert LegacyPrompt == PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA
