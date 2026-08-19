import json
import logging
import os
import sys
from typing import Any, Dict, List, Set, Tuple
from sqlalchemy import select, func

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.storage import storage_repo
from app.db.session import db_session
from app.db.models.library import NovelModel, ChapterModel
from app.db.models.jobs import TranslationJobModel, ImportJobModel
from app.db.models.book_bible import BookBibleModel
from app.db.models.character_profile import (
    ProfileBookModel,
    ProfileEditionModel,
    ProfileChapterMappingModel,
    ProfileSubmissionModel,
    ProfileEventModel,
    ProfileEvidenceModel,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('AuditScript')


def audit_entities(session) -> bool:
    logger.info('=== Starting Comprehensive Structured Storage Audit ===')
    all_files = storage_repo.list_files(prefix='', raise_on_error=True)
    
    # 1. Collect Storage IDs
    storage_novel_ids: Set[str] = set()
    storage_chapter_tuples: Set[Tuple[str, int]] = set()
    storage_bible_ids: Set[str] = set()
    storage_job_ids: Set[str] = set()
    storage_profile_book_ids: Set[str] = set()
    storage_edition_ids: Set[str] = set()
    storage_mapping_tuples: Set[Tuple[str, int]] = set()
    storage_submission_ids: Set[str] = set()
    storage_event_ids: Set[str] = set()
    storage_evidence_ids: Set[str] = set()

    for k in all_files:
        if k.endswith('/metadata.json'):
            parts = k.split('/')
            if len(parts) >= 2:
                storage_novel_ids.add(parts[1])
                data = storage_repo.download_json(k, raise_on_error=True)
                if isinstance(data, dict):
                    for ch in data.get('chapters', []):
                        if isinstance(ch, dict) and 'chapter_index' in ch:
                            storage_chapter_tuples.add((parts[1], int(ch['chapter_index'])))

        elif k.endswith('/bible.json'):
            parts = k.split('/')
            if len(parts) >= 2:
                storage_bible_ids.add(parts[1])
        elif k.startswith('data/bibles/') and k.endswith('.json'):
            fname = os.path.basename(k)[:-5]
            storage_bible_ids.add(fname)

        elif (k.startswith('data/jobs/') or k.startswith('jobs/')) and k.endswith('.json'):
            fname = os.path.basename(k)[:-5]
            storage_job_ids.add(fname)

        elif k.startswith('profile_books/') or k.startswith('data/profile_books/') or '/profile/profile_books/' in k or '/profile/books/' in k or k.endswith('/profile/book.json'):
            data = storage_repo.download_json(k, raise_on_error=True)
            if isinstance(data, dict) and data.get('book_id'):
                storage_profile_book_ids.add(data['book_id'])

        elif k.startswith('profile_editions/') or k.startswith('data/profile_editions/') or '/profile/profile_editions/' in k or '/profile/editions/' in k:
            data = storage_repo.download_json(k, raise_on_error=True)
            if isinstance(data, dict) and data.get('edition_id'):
                storage_edition_ids.add(data['edition_id'])

        elif k.startswith('profile_chapter_mappings/') or k.startswith('data/profile_chapter_mappings/') or '/profile/profile_chapter_mappings/' in k or '/profile/mappings/' in k:
            data = storage_repo.download_json(k, raise_on_error=True)
            if isinstance(data, dict) and data.get('edition_id') and 'local_chapter_index' in data:
                storage_mapping_tuples.add((data['edition_id'], int(data['local_chapter_index'])))

        elif k.startswith('profile_submissions/') or k.startswith('data/profile_submissions/') or '/profile/profile_submissions/' in k or '/profile/submissions/' in k:
            data = storage_repo.download_json(k, raise_on_error=True)
            if isinstance(data, dict) and data.get('submission_id'):
                storage_submission_ids.add(data['submission_id'])

        elif k.startswith('profile_events/') or k.startswith('data/profile_events/') or '/profile/profile_events/' in k or '/profile/events/' in k:
            data = storage_repo.download_json(k, raise_on_error=True)
            if isinstance(data, dict) and data.get('event_id'):
                storage_event_ids.add(data['event_id'])

        elif k.startswith('profile_evidence/') or k.startswith('data/profile_evidence/') or '/profile/profile_evidence/' in k or '/profile/evidence/' in k:
            data = storage_repo.download_json(k, raise_on_error=True)
            if isinstance(data, dict) and data.get('evidence_id'):
                storage_evidence_ids.add(data['evidence_id'])


    # 2. Collect DB IDs
    db_novel_ids = set(session.scalars(select(NovelModel.novel_id)).all())
    db_chapter_tuples = set(session.execute(select(ChapterModel.novel_id, ChapterModel.chapter_index)).all())
    db_bible_ids = set(session.scalars(select(BookBibleModel.novel_id)).all())
    db_job_ids = set(session.scalars(select(TranslationJobModel.job_id)).all())
    db_profile_book_ids = set(session.scalars(select(ProfileBookModel.book_id)).all())
    db_edition_ids = set(session.scalars(select(ProfileEditionModel.edition_id)).all())
    db_mapping_tuples = set(session.execute(select(ProfileChapterMappingModel.edition_id, ProfileChapterMappingModel.local_chapter_index)).all())
    db_submission_ids = set(session.scalars(select(ProfileSubmissionModel.submission_id)).all())
    db_event_ids = set(session.scalars(select(ProfileEventModel.event_id)).all())
    db_evidence_ids = set(session.scalars(select(ProfileEvidenceModel.evidence_id)).all())

    logger.info('--- Audit Manifest Counts ---')
    logger.info(f'Novels: Storage={len(storage_novel_ids)}, DB={len(db_novel_ids)}')
    logger.info(f'Chapters: Storage={len(storage_chapter_tuples)}, DB={len(db_chapter_tuples)}')
    logger.info(f'Book Bibles: Storage={len(storage_bible_ids)}, DB={len(db_bible_ids)}')
    logger.info(f'Translation Jobs: Storage={len(storage_job_ids)}, DB={len(db_job_ids)}')
    logger.info(f'Profile Books: Storage={len(storage_profile_book_ids)}, DB={len(db_profile_book_ids)}')
    logger.info(f'Profile Editions: Storage={len(storage_edition_ids)}, DB={len(db_edition_ids)}')
    logger.info(f'Profile Mappings: Storage={len(storage_mapping_tuples)}, DB={len(db_mapping_tuples)}')
    logger.info(f'Profile Submissions: Storage={len(storage_submission_ids)}, DB={len(db_submission_ids)}')
    logger.info(f'Profile Events: Storage={len(storage_event_ids)}, DB={len(db_event_ids)}')
    logger.info(f'Profile Evidence: Storage={len(storage_evidence_ids)}, DB={len(db_evidence_ids)}')

    # 3. ID Inclusion Checks (Storage IDs must be a subset of DB IDs)
    missing_errors: List[str] = []

    def check_subset(name: str, storage_set: Set, db_set: Set):
        diff = storage_set - db_set
        if diff:
            missing_errors.append(f'Entity {name}: {len(diff)} storage items missing in DB! Sample missing: {list(diff)[:5]}')

    check_subset('Novels', storage_novel_ids, db_novel_ids)
    check_subset('Chapters', storage_chapter_tuples, db_chapter_tuples)
    check_subset('Book Bibles', storage_bible_ids, db_bible_ids)
    check_subset('Translation Jobs', storage_job_ids, db_job_ids)
    check_subset('Profile Books', storage_profile_book_ids, db_profile_book_ids)
    check_subset('Profile Editions', storage_edition_ids, db_edition_ids)
    check_subset('Profile Mappings', storage_mapping_tuples, db_mapping_tuples)
    check_subset('Profile Submissions', storage_submission_ids, db_submission_ids)
    check_subset('Profile Events', storage_event_ids, db_event_ids)
    check_subset('Profile Evidence', storage_evidence_ids, db_evidence_ids)

    # 4. Foreign Key & Integrity Checks in DB
    orphan_errors: List[str] = []
    
    # Check evidence trỏ tới event
    stmt_ev_orphans = select(ProfileEvidenceModel.evidence_id).where(
        ~ProfileEvidenceModel.event_id.in_(select(ProfileEventModel.event_id))
    )
    ev_orphans = session.scalars(stmt_ev_orphans).all()
    if ev_orphans:
        orphan_errors.append(f'{len(ev_orphans)} Profile Evidence items have orphan event_id (no parent event in DB)!')

    # Check event trỏ tới submission
    stmt_event_orphans = select(ProfileEventModel.event_id).where(
        ~ProfileEventModel.source_submission_id.in_(select(ProfileSubmissionModel.submission_id))
    )
    event_orphans = session.scalars(stmt_event_orphans).all()
    if event_orphans:
        orphan_errors.append(f'{len(event_orphans)} Profile Events have orphan submission_id!')

    if missing_errors or orphan_errors:
        logger.error('================ AUDIT FAILED ================')
        for err in missing_errors:
            logger.error(f' [MISSING DATA] {err}')
        for err in orphan_errors:
            logger.error(f' [INTEGRITY VIOLATION] {err}')
        logger.error('==============================================')
        return False

    logger.info('=== AUDIT PASSED: 100% IDs verified and Foreign Key integrity intact! ===')
    return True


def main():
    with db_session() as session:
        ok = audit_entities(session)
        if not ok:
            sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
