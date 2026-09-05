from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.infrastructure.cache.direct_translation import DirectTranslationCache
from app.db.session import db_session
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.errors import IncompleteLLMResponseError
from app.llm.gemini_provider import GeminiProvider
from app.modules.book_bible.persistence.legacy_repository import BookBibleRepository
from app.modules.translation.application.qa_service import QAService
from app.modules.library.persistence.legacy_models import EpubBuildJobModel, NovelModel
from app.modules.library.legacy_service import LegacyLibraryService
from app.modules.library.persistence.legacy_repository import (
    ChapterRevisionConflictError,
    LibraryRepository,
)
from app.schemas.book_bible import AddressObservation, BookBible, BookBibleDelta, CharacterEntry
from app.schemas.library import ChapterItem, ChapterStatus
from app.schemas.translation import HTMLTranslationItem, QAIssue
from app.parsers.html_merger import HTMLMarkerValidationError, HTMLMerger


def _novel(novel_id: str) -> NovelModel:
    return NovelModel(novel_id=novel_id, title="Regression novel", status="ongoing")


def test_chapter_only_write_preserves_other_chapters_and_detects_stale_revision():
    novel_id = f"test-{uuid4().hex}"
    with db_session() as session:
        session.add(_novel(novel_id))
        session.commit()
        first = LibraryRepository.save_chapter(
            session,
            novel_id,
            ChapterItem(chapter_index=1, chapter_title="One", status=ChapterStatus.COMPLETED),
        )
        second = LibraryRepository.save_chapter(
            session,
            novel_id,
            ChapterItem(chapter_index=2, chapter_title="Two"),
        )
        session.commit()

    with db_session() as session:
        first.status = ChapterStatus.NEEDS_REVIEW
        updated = LibraryRepository.save_chapter(
            session, novel_id, first, expected_revision=first.revision
        )
        session.commit()
        assert updated.revision == first.revision + 1

    with db_session() as session:
        stale_item = LibraryRepository.get_chapter(session, novel_id, 1)
        assert stale_item is not None
        stale_item.status = ChapterStatus.COMPLETED
        with pytest.raises(ChapterRevisionConflictError):
            LibraryRepository.save_chapter(
                session, novel_id, stale_item, expected_revision=first.revision
            )
        assert LibraryRepository.get_chapter(session, novel_id, 2).chapter_title == "Two"
        session.rollback()

    with db_session() as session:
        novel = session.get(NovelModel, novel_id)
        assert novel.total_chapters == 2
        assert novel.translated_chapters == 0  # the second chapter is not translated


def test_sqlite_chapter_revision_guard_has_one_winner_for_same_chapter():
    novel_id = f"test-{uuid4().hex}"
    with db_session() as session:
        session.add(_novel(novel_id))
        session.commit()
        LibraryRepository.save_chapter(
            session,
            novel_id,
            ChapterItem(chapter_index=1, chapter_title="Initial"),
        )
        session.commit()

    loaded = Barrier(2)

    def save_from_snapshot(title: str):
        with db_session() as session:
            item = LibraryRepository.get_chapter(session, novel_id, 1)
            assert item is not None
            loaded.wait(timeout=10)
            item.chapter_title = title
            try:
                saved = LibraryRepository.save_chapter(
                    session,
                    novel_id,
                    item,
                    expected_revision=item.revision,
                )
                session.commit()
                return "saved", saved.revision
            except ChapterRevisionConflictError:
                session.rollback()
                return "conflict", None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(save_from_snapshot, ["Worker A", "Worker B"]))

    assert sorted(outcome[0] for outcome in outcomes) == ["conflict", "saved"]
    with db_session() as session:
        saved = LibraryRepository.get_chapter(session, novel_id, 1)
        assert saved is not None
        assert saved.chapter_title in {"Worker A", "Worker B"}
        assert saved.revision == 2


def test_translation_file_is_not_written_when_chapter_claim_is_rejected():
    service = LegacyLibraryService.__new__(LegacyLibraryService)
    service._novel_locks = {}
    service._chapter_extract_locks = {}
    service._chapter_extract_cache = {}
    service._global_lock = Lock()
    service._save_chapter_state = Mock(
        side_effect=ChapterRevisionConflictError("stale chapter")
    )
    service._save_raw_file = Mock()
    chapter = ChapterItem(chapter_index=1, chapter_title="One")
    meta = SimpleNamespace(novel_id=f"test-{uuid4().hex}", chapters=[chapter])

    with pytest.raises(ChapterRevisionConflictError):
        service._persist_completed_translation(meta, chapter, "Bản dịch")

    service._save_raw_file.assert_not_called()


def test_sqlite_claim_next_job_has_one_winner():
    novel_id = f"test-{uuid4().hex}"
    with db_session() as session:
        session.add(_novel(novel_id))
        session.commit()
        LibraryRepository.mark_dirty_and_enqueue_job(session, novel_id, dirty_indexes=[1])
        session.commit()

    def claim(worker: str):
        with db_session() as session:
            job = LibraryRepository.claim_next_job(session, worker_id=worker, novel_id=novel_id)
            session.commit()
            return job.job_id if job else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ["worker-a", "worker-b"]))
    assert [job_id for job_id in claimed if job_id] == [claimed[0] or claimed[1]]

    with db_session() as session:
        job = session.query(EpubBuildJobModel).filter_by(novel_id=novel_id).one()
        assert job.status == "processing"
        assert job.lease_token in {"worker-a", "worker-b"}
        assert LibraryRepository.heartbeat_job(session, job.job_id, "wrong-worker") is False
        assert LibraryRepository.complete_job(
            session, job.job_id, built_revision=1, epub_key="exports/stale.epub", worker_id="wrong-worker"
        ) is False


def test_book_bible_concurrent_delta_merge_is_monotonic_and_keeps_both_entries():
    novel_id = f"test-{uuid4().hex}"

    def merge(original_name: str):
        with db_session() as session:
            result = BookBibleRepository.merge_delta_transactional(
                session,
                novel_id,
                BookBibleDelta(
                    new_characters=[CharacterEntry(original_name=original_name, vi_name=f"Vi {original_name}")]
                ),
            )
            session.commit()
            return result.bible_revision

    with ThreadPoolExecutor(max_workers=2) as pool:
        revisions = list(pool.map(merge, ["A", "B"]))

    with db_session() as session:
        bible = BookBibleRepository.get_book_bible(session, novel_id)
        assert bible is not None
        assert bible.bible_revision == 2
        assert {character.original_name for character in bible.characters} == {"A", "B"}
        assert sorted(revisions) == [1, 2]


def test_full_book_bible_snapshot_merge_preserves_address_observations():
    novel_id = f"test-{uuid4().hex}"
    character = CharacterEntry(
        character_id="char-main",
        original_name="Main",
        vi_name="Nhân vật chính",
    )
    observations = [
        AddressObservation(
            observation_id=f"observation-{index}",
            character_id=character.character_id,
            counterpart_text=f"Counterpart {index}",
            self_term="ta",
            other_term="ngươi",
            chapter_index=index,
            chapter_id=f"chapter-{index}",
            chunk_id=f"chunk-{index}",
            confidence=1.0,
            resolution="confirmed",
        )
        for index in range(1, 8)
    ]

    with db_session() as session:
        BookBibleRepository.save_book_bible(
            session,
            BookBible(
                novel_id=novel_id,
                characters=[character],
                address_observations=observations,
            ),
        )
        session.commit()

    new_observation = AddressObservation(
        observation_id="observation-8",
        character_id=character.character_id,
        counterpart_text="Counterpart 8",
        self_term="ta",
        other_term="ngươi",
        chapter_index=8,
        chapter_id="chapter-8",
        chunk_id="chunk-8",
        confidence=1.0,
        resolution="confirmed",
    )
    with db_session() as session:
        saved = BookBibleRepository.save_book_bible(
            session,
            BookBible(
                novel_id=novel_id,
                characters=[character],
                address_observations=[*observations, new_observation],
            ),
        )
        session.commit()

    assert {item.observation_id for item in saved.address_observations} == {
        *(item.observation_id for item in observations),
        new_observation.observation_id,
    }


def test_stale_book_bible_snapshot_cannot_revert_newer_canonical_name():
    novel_id = f"test-{uuid4().hex}"
    stale = BookBible(
        novel_id=novel_id,
        bible_revision=1,
        characters=[CharacterEntry(original_name="Name", vi_name="Tên cũ")],
    )
    with db_session() as session:
        BookBibleRepository.save_book_bible(session, stale)
        session.commit()

    fresh = stale.model_copy(deep=True)
    fresh.bible_revision = 2
    fresh.characters[0].vi_name = "Tên mới"
    with db_session() as session:
        BookBibleRepository.save_book_bible(session, fresh)
        session.commit()

    with db_session() as session:
        BookBibleRepository.save_book_bible(session, stale)
        session.commit()
        current = BookBibleRepository.get_book_bible(session, novel_id)

    assert current is not None
    assert current.characters[0].vi_name == "Tên mới"
    assert current.bible_revision == 3


def test_direct_translation_cache_requires_exact_committed_revision(tmp_path):
    cache = DirectTranslationCache(str(tmp_path))
    bible = BookBible(novel_id="test-cache", bible_revision=8)
    cache.put("test-cache", "source", 1, None, "gemini", "model", 7, "translated", bible)
    assert cache.get("test-cache", "source", 1, None, "gemini", "model", 8) is not None
    assert cache.get("test-cache", "source", 1, None, "gemini", "model", 7) is None


def test_html_roundtrip_preserves_inline_media_links_and_rejects_missing_markers():
    source = '<div>outside<br>line</div><p>Hello <em>world</em> <a id="note" href="#n">link</a><img src="cover.png"/></p>'
    items, soup = HTMLMerger.extract_semantic_nodes(source)
    protected = next(item.protected_text for item in items if item.protected_text)
    paragraph = next(item for item in items if item.protected_text)
    translated = HTMLTranslationItem(
        id=paragraph.id,
        text_vi=protected.replace("Hello", "Xin").replace("world", "thế giới"),
    )
    plain = [
        HTMLTranslationItem(id=item.id, text_vi=translated.text_vi if item.id == paragraph.id else item.text)
        for item in items
    ]
    output = HTMLMerger.reconstruct_html(soup, plain, strict_markers=True)
    assert '<em>thế giới</em>' in output
    assert '<a href="#n" id="note">link</a>' in output
    assert '<img src="cover.png"/>' in output
    assert "outside<br/>line" in output

    items2, soup2 = HTMLMerger.extract_semantic_nodes('<p>Hello <em>world</em></p>')
    with pytest.raises(HTMLMarkerValidationError):
        HTMLMerger.reconstruct_html(
            soup2,
            [HTMLTranslationItem(id=items2[0].id, text_vi="Xin chào")],
            strict_markers=True,
        )


def test_html_markers_are_invisible_to_foreign_token_qa_and_restore_exactly():
    items, _ = HTMLMerger.extract_semantic_nodes("<p>Hello <em>world</em></p>")
    item = items[0]
    protected = item.protected_text
    qa_text, marker_tokens = HTMLMerger.protect_markers(protected)

    issues = QAService(None).fast_rule_check(item.text, qa_text, BookBible())

    assert not any(issue.found.lower() == "close" for issue in issues)
    assert HTMLMerger.restore_markers(qa_text, marker_tokens) == protected


@pytest.mark.asyncio
async def test_anthropic_incomplete_response_is_not_accepted_and_close_is_idempotent():
    provider = AnthropicProvider(api_key="dummy", model="model")
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    stop_reason="max_tokens",
                    content=[SimpleNamespace(type="text", text="partial")],
                )
            )
        ),
        aclose=AsyncMock(),
    )
    provider.client = fake_client
    with pytest.raises(IncompleteLLMResponseError):
        await provider.translate_prose_chunk("source", BookBible())
    await provider.aclose()
    await provider.aclose()
    fake_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_correction_error_keeps_previous_translation():
    class CorrectionClient:
        async def translate_prose_chunk(self, **kwargs):
            return "bản dịch đầy đủ"

        async def correct_translation_terms(self, **kwargs):
            raise IncompleteLLMResponseError("cut", operation="correct_translation_terms")

    from app.modules.translation.application.qa_service import QAService

    result = await QAService(CorrectionClient()).correct_and_recheck(
        "source",
        "bản dịch đầy đủ 老师",
        BookBible(),
    )
    assert result.translated_text == "bản dịch đầy đủ 老师"
