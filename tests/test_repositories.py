import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.repositories.library_repository import LibraryRepository
from app.repositories.book_bible_repository import BookBibleRepository
from app.repositories.character_profile_repository import CharacterProfileRepository
from app.schemas.library import (
    NovelCreateRequest,
    NovelUpdateRequest,
    NovelMetadata,
    ChapterItem,
    ChapterStatus,
    ImportJobStatus,
)
from app.schemas.translation import TranslationJob, JobStatusEnum, InputType
from app.schemas.book_bible import BookBible, CharacterEntry
from app.schemas.character_profile import (
    BookMetadata,
    FingerprintBundle,
    EditionRecord,
    ChapterMapping,
    SubmissionRecord,
    CharacterEvent,
    EventEvidence,
)


@pytest.fixture
def db_session():
    # Use SQLite in-memory for testing
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_library_repository_crud(db_session):
    # 1. Create novel
    req = NovelCreateRequest(
        title='Test Novel',
        original_title='Original Test Novel',
        author='Test Author',
        genre=['Action', 'Fantasy'],
        description='Test Description',
    )
    novel = LibraryRepository.create_novel(db_session, 'novel-test-1', req)
    assert novel.novel_id == 'novel-test-1'
    assert novel.title == 'Test Novel'

    # 2. Get novel
    fetched = LibraryRepository.get_novel(db_session, 'novel-test-1')
    assert fetched is not None
    assert fetched.author == 'Test Author'
    assert len(fetched.chapters) == 0

    # 3. Add chapters via save_novel
    ch1 = ChapterItem(
        chapter_index=1,
        chapter_id='ch_0001',
        chapter_title='Chapter 1: The Beginning',
        status=ChapterStatus.COMPLETED,
        word_count=1500,
        original_text_preview='Orig text',
        translated_text_preview='Trans text',
    )
    ch2 = ChapterItem(
        chapter_index=2,
        chapter_id='ch_0002',
        chapter_title='Chapter 2: The Journey',
        status=ChapterStatus.NOT_TRANSLATED,
        word_count=2000,
    )
    fetched.chapters = [ch1, ch2]
    fetched.total_chapters = 2
    fetched.translated_chapters = 1
    saved = LibraryRepository.save_novel(db_session, fetched)
    assert len(saved.chapters) == 2

    # 4. Check chapter queries
    ch_fetch = LibraryRepository.get_chapter(db_session, 'novel-test-1', 1)
    assert ch_fetch is not None
    assert ch_fetch.chapter_title == 'Chapter 1: The Beginning'
    assert ch_fetch.status == ChapterStatus.COMPLETED

    all_chapters = LibraryRepository.list_chapters(db_session, 'novel-test-1')
    assert len(all_chapters) == 2

    # 5. Update novel
    up_req = NovelUpdateRequest(title='Updated Test Novel')
    updated = LibraryRepository.update_novel(db_session, 'novel-test-1', up_req)
    assert updated.title == 'Updated Test Novel'

    # 6. Delete novel & cascade
    deleted = LibraryRepository.delete_novel(db_session, 'novel-test-1')
    assert deleted is True
    assert LibraryRepository.get_novel(db_session, 'novel-test-1') is None
    assert LibraryRepository.get_chapter(db_session, 'novel-test-1', 1) is None


def test_jobs_repository(db_session):
    # Import Job
    job = ImportJobStatus(
        job_id='job-imp-1',
        novel_id='novel-1',
        title='Importing Novel',
        status='processing',
        current_chapter=5,
        total_chapters=10,
        progress_percentage=50,
    )
    saved_job = LibraryRepository.save_import_job(db_session, job)
    assert saved_job.job_id == 'job-imp-1'
    assert saved_job.progress_percentage == 50

    fetch_job = LibraryRepository.get_import_job(db_session, 'job-imp-1')
    assert fetch_job is not None
    assert fetch_job.current_chapter == 5

    # Translation Job
    tjob = TranslationJob(
        job_id='tjob-1',
        filename='test.epub',
        input_type=InputType.EPUB,
        status=JobStatusEnum.PROCESSING,
        progress_percentage=30.0,
    )
    saved_tjob = LibraryRepository.save_translation_job(db_session, tjob)
    assert saved_tjob.job_id == 'tjob-1'
    assert saved_tjob.input_type == InputType.EPUB

    fetch_tjob = LibraryRepository.get_translation_job(db_session, 'tjob-1')
    assert fetch_tjob is not None
    assert fetch_tjob.progress_percentage == 30.0


def test_book_bible_repository(db_session):
    bible = BookBible(
        novel_id='novel-bible-1',
        schema_version=2,
        bible_revision=1,
        characters=[
            CharacterEntry(
                character_id='char-1',
                original_name='方源',
                vi_name='Phương Nguyên',
                role='Main Character',
            )
        ],
    )
    saved_bible = BookBibleRepository.save_book_bible(db_session, bible)
    assert saved_bible.novel_id == 'novel-bible-1'
    assert len(saved_bible.characters) == 1
    assert saved_bible.characters[0].vi_name == 'Phương Nguyên'

    # Lock test
    locked = BookBibleRepository.lock_book_bible_for_update(db_session, 'novel-bible-1')
    assert locked is not None
    assert locked.novel_id == 'novel-bible-1'


def test_character_profile_repository(db_session):
    # 1. Book
    book_dict = {
        'book_id': 'book-cp-1',
        'title': 'Tru Tiên',
        'author': 'Tiêu Đỉnh',
        'language': 'zh',
        'title_key': 'trutien',
        'author_key': 'tieudinh',
    }
    saved_book = CharacterProfileRepository.save_book(db_session, book_dict)
    assert saved_book['book_id'] == 'book-cp-1'

    # 2. Edition
    ed = EditionRecord(
        edition_id='ed-1',
        book_id='book-cp-1',
        metadata=BookMetadata(title='Tru Tiên', author='Tiêu Đỉnh'),
        fingerprints=FingerprintBundle(edition='ed-fp-1'),
    )
    saved_ed = CharacterProfileRepository.save_edition(db_session, ed)
    assert saved_ed.edition_id == 'ed-1'

    # 3. Chapter Mapping
    mapping = ChapterMapping(
        edition_id='ed-1',
        local_chapter_index=1,
        canonical_chapter_start=1,
        canonical_chapter_end=1,
    )
    saved_map = CharacterProfileRepository.save_chapter_mapping(db_session, mapping)
    assert saved_map.canonical_chapter_start == 1

    # 4. Submission & Idempotency
    sub = SubmissionRecord(
        submission_id='sub-1',
        idempotency_key='idem-key-1',
        book_id='book-cp-1',
        edition_id='ed-1',
        local_chapter_index=1,
        canonical_chapter_start=1,
        canonical_chapter_end=1,
        input_type='structured_events',
        content_fingerprint='fp-hash-1',
        source_group_id='grp-1',
    )
    saved_sub = CharacterProfileRepository.save_submission(db_session, sub)
    assert saved_sub.submission_id == 'sub-1'

    idem_sub = CharacterProfileRepository.get_submission_by_idempotency_key(db_session, 'idem-key-1')
    assert idem_sub is not None
    assert idem_sub.submission_id == 'sub-1'

    # 5. Events & Evidence
    event = CharacterEvent(
        event_id='event-1',
        book_id='book-cp-1',
        character_id='char-tt-1',
        character_original_name='Trương Tiểu Phàm',
        canonical_chapter=1,
        category='identity',
        attribute_key='name',
        operation='set',
        value='Trương Tiểu Phàm',
        certainty='observed',
        status='pending',
        evidence='Excerpt from text',
        confidence=0.95,
        source_group_id='grp-1',
        source_submission_id='sub-1',
    )
    saved_ev = CharacterProfileRepository.save_event(db_session, event, 'event-key-1')
    assert saved_ev.event_id == 'event-1'

    evidence = EventEvidence(
        evidence_id='evi-1',
        event_key='event-key-1',
        source_group_id='grp-1',
        submission_id='sub-1',
        excerpt='Excerpt from text',
        confidence=0.95,
    )
    saved_evi = CharacterProfileRepository.save_evidence(db_session, evidence, 'event-1')
    assert saved_evi.evidence_id == 'evi-1'

    # 6. Event Query & Status Update
    events = CharacterProfileRepository.list_events(db_session, 'book-cp-1', status='pending')
    assert len(events) == 1

    up_ev = CharacterProfileRepository.update_event_status(db_session, 'event-1', 'approved')
    assert up_ev.status == 'approved'

    # 7. Settings
    st = CharacterProfileRepository.get_settings(db_session)
    assert st.auto_approve is False
    st_up = CharacterProfileRepository.update_settings(db_session, auto_approve=True)
    assert st_up.auto_approve is True

