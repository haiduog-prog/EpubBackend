import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.repositories.library_repository import LibraryRepository
from app.schemas.book_bible import BookBible, TermEntry
from app.schemas.character_profile import (
    BookMetadata,
    BookResolutionRequest,
    CharacterEventCandidate,
    EditionCreateRequest,
)
from app.schemas.library import ChapterItem, ChapterStatus, NovelCreateRequest
from app.services.book_bible_service import BookBibleService
from app.services.character_profile_service import CharacterProfileService
from app.services.library_service import LibraryService


def _book_and_edition(service: CharacterProfileService, title: str):
    book = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(title=title, author="Author", language="vi"),
            create_if_missing=True,
        )
    )
    edition = service.create_edition(
        book.book_id,
        EditionCreateRequest(metadata=BookMetadata(title=title, author="Author", language="vi")),
    )
    return book.book_id, edition.edition_id


def test_submission_idempotency_is_scoped_and_conflicts_are_rejected():
    service = CharacterProfileService(min_independent_sources=2)
    book_a, edition_a = _book_and_edition(service, "Scoped A")
    book_b, edition_b = _book_and_edition(service, "Scoped B")
    candidate = CharacterEventCandidate(
        character_original_name="A",
        category="status",
        attribute_key="state",
        operation="set",
        value="alive",
    )

    first = service.submit(
        book_a, edition_a, "same-client-key", 1, "structured_events", "fp-a", candidates=[candidate]
    )
    second = service.submit(
        book_b, edition_b, "same-client-key", 1, "structured_events", "fp-b", candidates=[candidate]
    )
    assert first.submission_id != second.submission_id

    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        service.submit(
            book_a,
            edition_a,
            "same-client-key",
            2,
            "structured_events",
            "different-fingerprint",
            candidates=[candidate],
        )


def test_snapshot_complete_through_does_not_skip_a_hole():
    service = CharacterProfileService(min_independent_sources=2)
    book_id, edition_id = _book_and_edition(service, "Contiguous Snapshot")
    candidate = CharacterEventCandidate(
        character_original_name="A",
        category="status",
        attribute_key="state",
        operation="set",
        value="alive",
    )
    for chapter in (1, 3):
        service.submit(
            book_id,
            edition_id,
            f"key-{chapter}",
            chapter,
            "structured_events",
            f"fp-{chapter}",
            candidates=[candidate],
        )

    snapshot = service.snapshot(edition_id, 3)
    assert snapshot.complete_through_chapter == 1
    assert snapshot.snapshot_status == "partial"


def test_known_names_index_uses_real_line_breaks():
    bible = BookBible(
        characters=[],
        places=[],
        terms=[],
    )
    bible.terms.extend([
        TermEntry(original_name="A", vi_name="A"),
        TermEntry(original_name="B", vi_name="B"),
    ])
    assert BookBibleService.get_known_names_index(bible) == "A -> A\nB -> B"


def test_raw_chapter_overwrite_invalidates_old_translation():
    service = LibraryService()
    novel_id = "review-overwrite-regression"
    service.delete_novel(novel_id)
    service.create_novel(NovelCreateRequest(title="Overwrite Regression", novel_id=novel_id))
    service.add_or_update_chapter(novel_id, 1, "Chapter 1", "old source")
    translated_key = service._chapter_key(novel_id, 1, is_translated=True)
    service._save_raw_file(translated_key, b"old translation")
    meta = service.get_novel(novel_id)
    chapter = meta.chapters[0]
    chapter.status = ChapterStatus.COMPLETED
    chapter.r2_translated_key = translated_key
    service._save_metadata(meta)

    updated = service.add_or_update_chapter(novel_id, 1, "Chapter 1", "new source")
    assert updated.status == ChapterStatus.NOT_TRANSLATED
    assert updated.r2_translated_key == ""
    assert service.get_chapter_content(novel_id, 1, version="translated") is None
    service.delete_novel(novel_id)


def test_stale_novel_aggregate_does_not_delete_newer_chapter_rows():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        meta = LibraryRepository.create_novel(
            session,
            "stale-aggregate",
            NovelCreateRequest(title="Stale Aggregate"),
        )
        meta.chapters = [
            ChapterItem(
                chapter_index=1,
                chapter_id="ch_0001",
                chapter_title="One",
                status=ChapterStatus.NOT_TRANSLATED,
            )
        ]
        LibraryRepository.save_novel(session, meta)
        session.commit()

        stale = LibraryRepository.get_novel(session, "stale-aggregate")
        stale.chapters.append(
            ChapterItem(
                chapter_index=2,
                chapter_id="ch_0002",
                chapter_title="Two",
                status=ChapterStatus.NOT_TRANSLATED,
            )
        )
        LibraryRepository.save_novel(session, stale)
        session.commit()

        stale_without_newer = LibraryRepository.get_novel(session, "stale-aggregate")
        stale_without_newer.chapters = stale_without_newer.chapters[:1]
        LibraryRepository.save_novel(session, stale_without_newer)
        session.commit()

        assert len(LibraryRepository.list_chapters(session, "stale-aggregate")) == 2
    finally:
        session.close()
