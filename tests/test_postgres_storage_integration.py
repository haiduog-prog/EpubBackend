import pytest
import os
from sqlalchemy import create_engine
from app.config import settings
from app.db.base import Base
from app.db.session import reset_db_engine, db_session
from app.schemas.library import NovelCreateRequest, ChapterItem, ChapterStatus, ImportJobStatus
from app.schemas.book_bible import BookBible, BookBibleDelta, CharacterEntry
from app.schemas.character_profile import (
    BookMetadata,
    FingerprintBundle,
    BookResolutionRequest,
    EditionCreateRequest,
    ChapterSubmissionRequest,
    CharacterEventCandidate,
)
from app.services.library_service import LibraryService
from app.services.character_profile_service import CharacterProfileService
from app.core.storage import storage_repo
from app.repositories.book_bible_repository import BookBibleRepository
from app.repositories.character_profile_repository import CharacterProfileRepository


@pytest.fixture
def postgres_test_db(monkeypatch):
    # Use in-memory SQLite for tests, but reconfigure engine and SessionLocal
    test_db_url = "sqlite:///file:test_mem_db?mode=memory&cache=shared&uri=true"
    monkeypatch.setattr(settings, "database_url", test_db_url)
    monkeypatch.setattr(settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(settings, "structured_storage_read_source", "postgres")
    
    engine = reset_db_engine(test_db_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


def test_library_and_import_jobs_in_postgres(postgres_test_db):
    lib_svc = LibraryService()

    # 1. Create Novel in postgres mode
    req = NovelCreateRequest(
        title="Phàm Nhân Tu Tiên",
        author="Vong Ngữ",
        description="Hành trình tu tiên của Hàn Lập",
    )
    novel = lib_svc.create_novel(req)
    assert novel.novel_id == "pham-nhan-tu-tien"

    # 2. Add chapter
    ch = lib_svc.add_or_update_chapter(
        novel_id="pham-nhan-tu-tien",
        chapter_index=1,
        chapter_title="Chương 1: Sơn thôn thiếu niên",
        content="Hàn Lập sinh ra ở một sơn thôn nhỏ...",
    )
    assert ch.chapter_index == 1

    # 3. Read novel from DB after clearing cache
    lib_svc._cache.clear()
    fetched = lib_svc.get_novel("pham-nhan-tu-tien")
    assert fetched is not None
    assert fetched.title == "Phàm Nhân Tu Tiên"
    assert len(fetched.chapters) == 1

    # 4. Import Job persistence
    job = ImportJobStatus(
        job_id="import-test-123",
        status="completed",
        current_step="Hoàn thành",
        progress_percentage=100,
        added_chapters=10,
    )
    lib_svc._persist_import_job(job)
    
    # New service instance reads job from DB
    lib_svc2 = LibraryService()
    job_from_db = lib_svc2.get_import_job("import-test-123")
    assert job_from_db is not None
    assert job_from_db.status == "completed"
    assert job_from_db.added_chapters == 10


def test_character_profile_cold_start_and_hydration(postgres_test_db):
    # Instance 1: write data
    cp_svc1 = CharacterProfileService(min_independent_sources=1, auto_approve=True)

    resolve_req = BookResolutionRequest(
        metadata=BookMetadata(title="Đấu Phá Thương Khung", author="Thiên Tằm Thổ Đậu"),
    )
    res = cp_svc1.resolve_book(resolve_req)
    book_id = res.book_id
    assert book_id is not None

    ed_res = cp_svc1.create_edition(book_id, EditionCreateRequest(chapter_count=1000))
    edition_id = ed_res.edition_id

    sub = cp_svc1.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="idem-dp-ch1",
        local_chapter_index=1,
        input_type="structured_events",
        content_fingerprint="dp-fp-1",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Tiêu Viêm",
                character_id="char-tieu-viem",
                category="identity",
                attribute_key="realm",
                operation="set",
                value="Đấu Chi Khí Tam Đoạn",
                certainty="observed",
                confidence=1.0,
            ),
        ],
    )
    assert sub.status == "completed"

    # Instance 2 (Cold start simulation - completely empty memory)
    cp_svc2 = CharacterProfileService(min_independent_sources=1, auto_approve=True)
    cp_svc2._hydrate_all_from_storage(force=True)

    # Verify that Instance 2 reads all data accurately from PostgreSQL
    snapshot = cp_svc2.snapshot(edition_id=edition_id, local_chapter_index=1)
    assert len(snapshot.characters) >= 1
    tieu_viem = next(c for c in snapshot.characters if c.character_id == "char-tieu-viem")
    assert tieu_viem.attributes["realm"] == "Đấu Chi Khí Tam Đoạn"

    # Verify delete cascade
    deleted = cp_svc2.delete_book(book_id)
    assert deleted is True

    # After deletion, database should have 0 books and 0 events for this book
    with db_session() as session:
        assert CharacterProfileRepository.get_book(session, book_id) is None
        assert len(CharacterProfileRepository.list_events(session, book_id=book_id)) == 0


def test_book_bible_transactional_merge_in_postgres(postgres_test_db):
    novel_id = "test-novel-concurrency"
    
    # 1. Delta 1
    delta1 = BookBibleDelta(
        new_characters=[
            CharacterEntry(character_id="char-1", original_name="Hàn Lập", vi_name="Hàn Lập", role="protagonist"),
        ],
    )
    with db_session() as session:
        b1 = BookBibleRepository.merge_delta_transactional(session, novel_id, delta1)
        session.commit()
    assert len(b1.characters) == 1

    # 2. Delta 2
    delta2 = BookBibleDelta(
        new_characters=[
            CharacterEntry(character_id="char-2", original_name="Nam Cung Uyển", vi_name="Nam Cung Uyển", role="heroine"),
        ],
    )
    with db_session() as session:
        b2 = BookBibleRepository.merge_delta_transactional(session, novel_id, delta2)
        session.commit()
    
    assert len(b2.characters) == 2
    assert {c.original_name for c in b2.characters} == {"Hàn Lập", "Nam Cung Uyển"}


def test_fail_fast_on_database_error(monkeypatch):
    # When in postgres mode, DB errors MUST NOT be silently swallowed
    monkeypatch.setattr(settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(settings, "structured_storage_read_source", "postgres")
    # Point to an invalid database URL to force connection errors
    reset_db_engine("sqlite:////nonexistent/invalid_path/test.db")

    lib_svc = LibraryService()
    req = NovelCreateRequest(title="Test Fail Fast Novel", author="Author")
    with pytest.raises(Exception):
        lib_svc.create_novel(req)

    cp_svc = CharacterProfileService()
    with pytest.raises(Exception):
        cp_svc.resolve_book(BookResolutionRequest(metadata=BookMetadata(title="Fail Book", author="Fail Author")))


def test_manual_evidence_and_settings_default(postgres_test_db):
    # 1. Test Default Settings
    with db_session() as session:
        st = CharacterProfileRepository.get_settings(session)
        assert st.auto_approve is False

    # 2. Test Manual Evidence
    cp_svc = CharacterProfileService(min_independent_sources=2, auto_approve=False)
    resolve_res = cp_svc.resolve_book(BookResolutionRequest(metadata=BookMetadata(title="Manual Evidence Book", author="Author")))
    book_id = resolve_res.book_id
    ed_res = cp_svc.create_edition(book_id, EditionCreateRequest(chapter_count=100))
    edition_id = ed_res.edition_id

    sub = cp_svc.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="idem-manual-1",
        local_chapter_index=1,
        input_type="structured_events",
        content_fingerprint="fp-manual-1",
        candidates=[

            CharacterEventCandidate(
                character_original_name="Hero",
                character_id="char-hero",
                category="identity",
                attribute_key="name",
                operation="set",
                value="Hero",
                certainty="observed",
                confidence=0.8,
            ),
        ],
    )
    event_id = list(cp_svc.events.keys())[0]
    
    # Add manual evidence through review
    cp_svc.approve_event(event_id=event_id, evidence="Manual review evidence text by admin")


    with db_session() as session:
        db_evidence = CharacterProfileRepository.list_all_evidence(session)
        assert len(db_evidence) == 2  # 1 from submit, 1 from manual review
        assert any(e.source_group_id == "manual-review" for e in db_evidence)

    # Cold start test for evidence
    cp_svc2 = CharacterProfileService()
    cp_svc2._hydrate_all_from_storage(force=True)
    assert len(cp_svc2.evidence) == 2
    ev_key = list(cp_svc._event_keys.keys())[0]
    assert "manual-review" in cp_svc2._event_evidence_groups.get(ev_key, set())


def test_list_events_in_postgres_mode(postgres_test_db):
    """Test that list_events works in postgres mode with and without book_id filter."""
    cp_svc = CharacterProfileService(min_independent_sources=2, auto_approve=False)

    # Book 1
    res1 = cp_svc.resolve_book(BookResolutionRequest(metadata=BookMetadata(title="Book One", author="Author 1")))
    ed1 = cp_svc.create_edition(res1.book_id, EditionCreateRequest(chapter_count=100))
    cp_svc.submit(
        book_id=res1.book_id,
        edition_id=ed1.edition_id,
        idempotency_key="idem-book1",
        local_chapter_index=1,
        input_type="structured_events",
        content_fingerprint="fp-b1",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Alice",
                category="identity",
                attribute_key="name",
                operation="set",
                value="Alice",
            )
        ],
    )

    # Book 2
    res2 = cp_svc.resolve_book(BookResolutionRequest(metadata=BookMetadata(title="Book Two", author="Author 2")))
    ed2 = cp_svc.create_edition(res2.book_id, EditionCreateRequest(chapter_count=100))
    cp_svc.submit(
        book_id=res2.book_id,
        edition_id=ed2.edition_id,
        idempotency_key="idem-book2",
        local_chapter_index=2,
        input_type="structured_events",
        content_fingerprint="fp-b2",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Bob",
                category="identity",
                attribute_key="name",
                operation="set",
                value="Bob",
            )
        ],
    )

    # 1. Query with book_id=None -> should return events from BOTH books
    all_events = cp_svc.list_events(book_id=None)
    assert len(all_events) == 2
    book_ids = {e.book_id for e in all_events}
    assert res1.book_id in book_ids
    assert res2.book_id in book_ids

    # 2. Query with specific book_id and canonical_chapter
    b1_events = cp_svc.list_events(book_id=res1.book_id, canonical_chapter=1)
    assert len(b1_events) == 1
    assert b1_events[0].character_original_name == "Alice"

    # 3. Query with status filter
    pending_events = cp_svc.list_events(status="pending")
    assert len(pending_events) == 2






