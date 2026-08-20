import concurrent.futures
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy import select, func


# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.config import settings
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
    ProfileSettingsModel,
)
from app.schemas.library import NovelMetadata, ChapterItem
from app.schemas.translation import TranslationJob
from app.schemas.book_bible import BookBible
from app.repositories.library_repository import LibraryRepository
from app.repositories.book_bible_repository import BookBibleRepository
from app.repositories.character_profile_repository import CharacterProfileRepository

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('MigrationScript')


def _norm(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().split())


def _hash(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def compute_logical_event_key(
    book_id: str,
    character_id: str,
    canonical_chapter: int,
    category: str,
    attribute_key: str,
    operation: str,
    value: Any,
) -> str:
    val_str = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    raw = "|".join([
        book_id,
        character_id,
        str(canonical_chapter),
        _norm(category),
        _norm(attribute_key),
        operation,
        val_str,
    ])
    return _hash(raw, 40)


def parse_datetime(dt_val: Any) -> Optional[datetime]:
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        return dt_val
    try:
        return datetime.fromisoformat(str(dt_val))
    except Exception:
        return datetime.now(timezone.utc)


def download_all_json_parallel(keys: List[str], max_workers: int = 20) -> Dict[str, dict]:
    """Download multiple JSON files concurrently with ThreadPoolExecutor."""
    results: Dict[str, dict] = {}
    if not keys:
        return results
    unique_keys = list(set(keys))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_key = {executor.submit(storage_repo.download_json, k, True): k for k in unique_keys}
        for fut in concurrent.futures.as_completed(future_to_key):
            k = future_to_key[fut]
            try:
                data = fut.result()
                if data and isinstance(data, dict):
                    results[k] = data
            except Exception as exc:
                logger.warning(f"Failed to download or parse JSON {k} (skipping): {exc}")
    return results



def migrate_novels(session) -> int:
    logger.info('Migrating novels and chapters...')
    keys = storage_repo.list_files(prefix='novels/', raise_on_error=True)
    novel_meta_keys = [k for k in keys if k.endswith('/metadata.json')]
    count = 0

    for key in novel_meta_keys:
        data = storage_repo.download_json(key, raise_on_error=True)
        if not data:
            continue
        try:
            if isinstance(data, dict):
                for ch in data.get('chapters', []):
                    if isinstance(ch, dict) and not ch.get('chapter_id'):
                        ch['chapter_id'] = f"ch_{int(ch.get('chapter_index', 1)):04d}"
            meta = NovelMetadata.model_validate(data)
            LibraryRepository.save_novel(session, meta)
            count += 1
            logger.info(f'Migrated novel: {meta.novel_id} with {len(meta.chapters)} chapters')

        except Exception as e:
            logger.error(f'Failed to migrate novel metadata {key}: {e}')
            raise e

    return count


def migrate_book_bibles(session) -> int:
    logger.info('Migrating book bibles...')
    keys = storage_repo.list_files(prefix='novels/', raise_on_error=True)
    bible_keys = [k for k in keys if k.endswith('/bible.json')]
    legacy_keys = storage_repo.list_files(prefix='data/bibles/', raise_on_error=True)
    all_keys = set(bible_keys + [k for k in legacy_keys if k.endswith('.json')])

    # Group by novel_id, prefer highest bible_revision
    bibles_by_novel_id: Dict[str, Tuple[dict, str]] = {}
    for key in all_keys:
        data = storage_repo.download_json(key, raise_on_error=True)
        if not data or not isinstance(data, dict):
            continue
        novel_id = data.get('novel_id')
        if not novel_id:
            continue
        existing = bibles_by_novel_id.get(novel_id)
        if existing:
            existing_rev = int(existing[0].get('bible_revision', 0))
            new_rev = int(data.get('bible_revision', 0))
            if new_rev <= existing_rev:
                continue
        bibles_by_novel_id[novel_id] = (data, key)

    count = 0
    for novel_id, (data, key) in bibles_by_novel_id.items():
        try:
            bible = BookBible.model_validate(data)
            BookBibleRepository.save_book_bible(session, bible)
            count += 1
            logger.info(f'Migrated Book Bible for novel: {bible.novel_id}')
        except Exception as e:
            logger.error(f'Failed to migrate book bible {key}: {e}')
            raise e

    return count


def migrate_translation_jobs(session) -> int:
    logger.info('Migrating translation jobs...')
    keys = storage_repo.list_files(prefix='data/jobs/', raise_on_error=True) + storage_repo.list_files(prefix='jobs/', raise_on_error=True)
    job_keys = [k for k in set(keys) if k.endswith('.json')]

    # Group by job_id, prefer newest timestamp
    jobs_by_id: Dict[str, Tuple[dict, str]] = {}
    for key in job_keys:
        data = storage_repo.download_json(key, raise_on_error=True)
        if not data or not isinstance(data, dict):
            continue
        job_id = data.get('job_id')
        if not job_id:
            continue
        existing = jobs_by_id.get(job_id)
        if existing:
            existing_dt = parse_datetime(existing[0].get('created_at'))
            new_dt = parse_datetime(data.get('created_at'))
            if existing_dt and new_dt and new_dt <= existing_dt:
                continue
        jobs_by_id[job_id] = (data, key)

    count = 0
    for job_id, (data, key) in jobs_by_id.items():
        try:
            job = TranslationJob.model_validate(data)
            LibraryRepository.save_translation_job(session, job)
            count += 1
        except Exception as e:
            logger.error(f'Failed to migrate translation job {key}: {e}')
            raise e

    logger.info(f'Migrated {count} translation jobs.')
    return count


def migrate_character_profiles(session) -> Dict[str, int]:
    logger.info('Migrating character profile entities...')
    counts = {
        'books': 0,
        'editions': 0,
        'mappings': 0,
        'submissions': 0,
        'events': 0,
        'evidence': 0,
    }

    all_keys = storage_repo.list_files(prefix='', raise_on_error=True)
    profile_keys = [k for k in all_keys if (k.startswith('profile_') or k.startswith('data/profile_') or '/profile/' in k) and k.endswith('.json')]
    logger.info(f'Downloading {len(profile_keys)} profile JSON files in parallel...')
    profile_json_map = download_all_json_parallel(profile_keys, max_workers=30)
    logger.info(f'Downloaded {len(profile_json_map)} profile JSON files successfully.')

    # Pre-fetch all existing DB IDs in 1 pass
    db_existing_book_ids = set(session.scalars(select(ProfileBookModel.book_id)).all())
    db_existing_edition_ids = set(session.scalars(select(ProfileEditionModel.edition_id)).all())
    db_existing_mapping_tuples = set(session.execute(select(ProfileChapterMappingModel.edition_id, ProfileChapterMappingModel.local_chapter_index)).all())
    db_existing_sub_ids = set(session.scalars(select(ProfileSubmissionModel.submission_id)).all())
    db_existing_event_ids = set(session.scalars(select(ProfileEventModel.event_id)).all())
    db_existing_evi_ids = set(session.scalars(select(ProfileEvidenceModel.evidence_id)).all())

    logical_key_to_event_id: Dict[str, str] = {}
    event_id_to_logical_key: Dict[str, str] = {}
    for eid, ekey in session.execute(select(ProfileEventModel.event_id, ProfileEventModel.event_key)).all():
        if ekey:
            logical_key_to_event_id[ekey] = eid
            event_id_to_logical_key[eid] = ekey

    # 1. Profile Books
    book_keys = [k for k in profile_keys if k.startswith('profile_books/') or k.startswith('data/profile_books/') or '/profile/profile_books/' in k or '/profile/books/' in k or k.endswith('/profile/book.json') or '/profile/book_' in k]
    books_by_id: Dict[str, Tuple[dict, str]] = {}
    for key in book_keys:
        data = profile_json_map.get(key)
        if not data or not isinstance(data, dict):
            continue
        book_id = data.get('book_id')
        if not book_id:
            continue
        existing = books_by_id.get(book_id)
        if existing:
            existing_rev = int(existing[0].get('revision', 0))
            new_rev = int(data.get('revision', 0))
            if new_rev <= existing_rev:
                continue
        books_by_id[book_id] = (data, key)

    for book_id, (data, key) in books_by_id.items():
        meta = data.get('metadata') or {}
        b_model = ProfileBookModel(
            book_id=book_id,
            novel_id=book_id if not book_id.startswith('book-') else None,
            title=meta.get('title') or data.get('title', ''),
            author=meta.get('author') or data.get('author', ''),
            language=meta.get('language') or data.get('language', ''),
            publisher=meta.get('publisher') or data.get('publisher', ''),
            identifier=meta.get('identifier') or data.get('identifier'),
            title_key=data.get('title_key', ''),
            author_key=data.get('author_key', ''),
            sampled_chapters=data.get('sampled_chapters', []),
            revision=data.get('revision', 0),
            created_at=parse_datetime(data.get('created_at')) or datetime.now(timezone.utc),
            updated_at=parse_datetime(data.get('updated_at')) or datetime.now(timezone.utc),
        )
        session.merge(b_model)
        db_existing_book_ids.add(book_id)
        counts['books'] += 1

    # 2. Profile Editions
    edition_keys = [k for k in profile_keys if k.startswith('profile_editions/') or k.startswith('data/profile_editions/') or '/profile/profile_editions/' in k or '/profile/editions/' in k]
    editions_by_id: Dict[str, Tuple[dict, str]] = {}
    for key in edition_keys:
        data = profile_json_map.get(key)
        if not data or not isinstance(data, dict):
            continue
        edition_id = data.get('edition_id')
        book_id = data.get('book_id')
        if not edition_id or not book_id:
            continue
        existing = editions_by_id.get(edition_id)
        if existing:
            existing_rev = int(existing[0].get('mapping_revision', 0))
            new_rev = int(data.get('mapping_revision', 0))
            if new_rev <= existing_rev:
                continue
        editions_by_id[edition_id] = (data, key)

    for edition_id, (data, key) in editions_by_id.items():
        book_id = data.get('book_id')
        if book_id not in db_existing_book_ids:
            session.merge(ProfileBookModel(
                book_id=book_id,
                novel_id=book_id if not book_id.startswith('book-') else None,
                title='Unknown Book',
                revision=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            db_existing_book_ids.add(book_id)

        ed_model = ProfileEditionModel(
            edition_id=edition_id,
            book_id=book_id,
            metadata_payload=data.get('metadata') or {},
            fingerprints=data.get('fingerprints') or {},
            chapter_count=data.get('chapter_count'),
            mapping_revision=data.get('mapping_revision', 1),
            created_at=parse_datetime(data.get('created_at')) or datetime.now(timezone.utc),
        )
        session.merge(ed_model)
        db_existing_edition_ids.add(edition_id)
        counts['editions'] += 1

    # 3. Chapter Mappings
    mapping_keys = [k for k in profile_keys if k.startswith('profile_chapter_mappings/') or k.startswith('data/profile_chapter_mappings/') or '/profile/profile_chapter_mappings/' in k or '/profile/mappings/' in k or '/profile/chapter_mappings/' in k]
    mappings_by_tuple: Dict[Tuple[str, int], Tuple[dict, str]] = {}
    for key in mapping_keys:
        data = profile_json_map.get(key)
        if not data or not isinstance(data, dict):
            continue
        edition_id = data.get('edition_id')
        if not edition_id or 'local_chapter_index' not in data:
            continue
        m_tuple = (edition_id, int(data.get('local_chapter_index', 0)))
        existing = mappings_by_tuple.get(m_tuple)
        if existing:
            existing_rev = int(existing[0].get('mapping_revision', 0))
            new_rev = int(data.get('mapping_revision', 0))
            if new_rev <= existing_rev:
                continue
        mappings_by_tuple[m_tuple] = (data, key)

    for (edition_id, local_chapter_index), (data, key) in mappings_by_tuple.items():
        m_model = ProfileChapterMappingModel(
            edition_id=edition_id,
            local_chapter_index=local_chapter_index,
            canonical_chapter_start=int(data.get('canonical_chapter_start', 0)),
            canonical_chapter_end=int(data.get('canonical_chapter_end', 0)),
            confidence=float(data.get('confidence', 1.0)),
            source=data.get('source', 'metadata'),
            mapping_revision=int(data.get('mapping_revision', 1)),
        )
        session.merge(m_model)
        counts['mappings'] += 1

    # 4. Submissions
    sub_keys = [k for k in profile_keys if k.startswith('profile_submissions/') or k.startswith('data/profile_submissions/') or '/profile/profile_submissions/' in k or '/profile/submissions/' in k]
    subs_by_id: Dict[str, Tuple[dict, str]] = {}
    for key in sub_keys:
        data = profile_json_map.get(key)
        if not data or not isinstance(data, dict):
            continue
        sub_id = data.get('submission_id')
        book_id = data.get('book_id')
        edition_id = data.get('edition_id')
        if not sub_id or not book_id or not edition_id:
            continue
        existing = subs_by_id.get(sub_id)
        if existing:
            existing_dt = parse_datetime(existing[0].get('created_at'))
            new_dt = parse_datetime(data.get('created_at'))
            if existing_dt and new_dt and new_dt <= existing_dt:
                continue
        subs_by_id[sub_id] = (data, key)

    for sub_id, (data, key) in subs_by_id.items():
        book_id = data.get('book_id')
        edition_id = data.get('edition_id')
        if book_id not in db_existing_book_ids:
            session.merge(ProfileBookModel(
                book_id=book_id,
                novel_id=book_id if not book_id.startswith('book-') else None,
                title='Unknown Book',
                revision=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))
            db_existing_book_ids.add(book_id)
        if edition_id not in db_existing_edition_ids:
            ed_obj = ProfileEditionModel(
                edition_id=edition_id,
                book_id=book_id,
                metadata_payload={},
                fingerprints={},
                created_at=datetime.now(timezone.utc),
            )
            session.merge(ed_obj)
            db_existing_edition_ids.add(edition_id)


        s_model = ProfileSubmissionModel(
            submission_id=sub_id,
            idempotency_key=data.get('idempotency_key') or sub_id,
            book_id=book_id,
            edition_id=edition_id,
            local_chapter_start=int(data.get('local_chapter_index', 0)),
            local_chapter_end=int(data.get('local_chapter_index', 0)),
            canonical_chapter_start=int(data.get('canonical_chapter_start', 0)),
            canonical_chapter_end=int(data.get('canonical_chapter_end', 0)),
            input_type=data.get('input_type', 'structured_events'),
            chapter_fingerprint=data.get('content_fingerprint', ''),
            source_group_id=data.get('source_group_id', ''),
            source_type=data.get('source_label', 'user'),
            status=data.get('status', 'completed'),
            error_message=data.get('error_message'),
            created_at=parse_datetime(data.get('created_at')) or datetime.now(timezone.utc),
            completed_at=parse_datetime(data.get('completed_at')),
        )
        session.merge(s_model)
        db_existing_sub_ids.add(sub_id)
        counts['submissions'] += 1

    # 5. Events
    event_keys = [k for k in profile_keys if k.startswith('profile_events/') or k.startswith('data/profile_events/') or '/profile/profile_events/' in k or '/profile/events/' in k]
    event_data_by_id: Dict[str, Tuple[dict, str]] = {}
    for key in event_keys:
        data = profile_json_map.get(key)
        if not data or not isinstance(data, dict):
            continue
        event_id = data.get('event_id')
        if not event_id:
            continue
        existing = event_data_by_id.get(event_id)
        if existing:
            existing_dt = parse_datetime(existing[0].get('created_at'))
            new_dt = parse_datetime(data.get('created_at'))
            if existing_dt and new_dt and new_dt <= existing_dt:
                continue
        event_data_by_id[event_id] = (data, key)

    for event_id, (data, key) in event_data_by_id.items():
        book_id = data.get('book_id')
        sub_id = data.get('source_submission_id')
        if not book_id or not sub_id:
            continue

        if event_id in db_existing_event_ids:
            continue

        if sub_id not in db_existing_sub_ids:
            if 'default-edition' not in db_existing_edition_ids:
                if book_id not in db_existing_book_ids:
                    CharacterProfileRepository.save_book(session, {'book_id': book_id})
                    db_existing_book_ids.add(book_id)
                session.merge(ProfileEditionModel(
                    edition_id='default-edition',
                    book_id=book_id,
                    metadata_payload={},
                    fingerprints={},
                    created_at=datetime.now(timezone.utc),
                ))
                db_existing_edition_ids.add('default-edition')
            sub = ProfileSubmissionModel(
                submission_id=sub_id,
                idempotency_key=sub_id,
                book_id=book_id,
                edition_id='default-edition',
                created_at=datetime.now(timezone.utc),
            )
            session.add(sub)
            db_existing_sub_ids.add(sub_id)

        val = data.get('value')
        val_to_save = val if isinstance(val, (dict, list)) else {'val': val}

        char_id = data.get('character_id', '')
        canonical_chapter = int(data.get('canonical_chapter', 0))
        category = data.get('category', 'identity')
        attr_key = data.get('attribute_key', 'name')
        operation = data.get('operation', 'set')

        logical_key = data.get('event_key')
        if not logical_key:
            logical_key = compute_logical_event_key(
                book_id=book_id,
                character_id=char_id,
                canonical_chapter=canonical_chapter,
                category=category,
                attribute_key=attr_key,
                operation=operation,
                value=val,
            )

        logical_key_to_event_id[logical_key] = event_id
        event_id_to_logical_key[event_id] = logical_key

        ev_model = ProfileEventModel(
            event_id=event_id,
            event_key=logical_key,
            book_id=book_id,
            character_id=char_id,
            character_original_name=data.get('character_original_name', ''),
            canonical_chapter=canonical_chapter,
            category=category,
            attribute_key=attr_key,
            operation=operation,
            value=val_to_save,
            certainty=data.get('certainty', 'observed'),
            status=data.get('status', 'pending'),
            evidence=data.get('evidence', ''),
            confidence=float(data.get('confidence', 1.0)),
            source_group_id=data.get('source_group_id', ''),
            source_submission_id=sub_id,
            supersedes_event_id=data.get('supersedes_event_id'),
            created_at=parse_datetime(data.get('created_at')) or datetime.now(timezone.utc),
            reviewed_at=parse_datetime(data.get('reviewed_at')),
            schema_version=int(data.get('schema_version', 1)),
        )
        session.add(ev_model)
        db_existing_event_ids.add(event_id)
        counts['events'] += 1

    # 6. Evidence
    evi_keys = [k for k in profile_keys if k.startswith('profile_evidence/') or k.startswith('data/profile_evidence/') or '/profile/profile_evidence/' in k or '/profile/evidence/' in k]
    evi_data_by_id: Dict[str, Tuple[dict, str]] = {}
    for key in evi_keys:
        data = profile_json_map.get(key)
        if not data or not isinstance(data, dict):
            continue
        evi_id = data.get('evidence_id')
        if not evi_id:
            continue
        existing = evi_data_by_id.get(evi_id)
        if existing:
            existing_dt = parse_datetime(existing[0].get('created_at'))
            new_dt = parse_datetime(data.get('created_at'))
            if existing_dt and new_dt and new_dt <= existing_dt:
                continue
        evi_data_by_id[evi_id] = (data, key)

    for evi_id, (data, key) in evi_data_by_id.items():
        event_id = data.get('event_id')
        event_key = data.get('event_key', '')
        sub_id = data.get('submission_id')

        if not event_id and event_key:
            event_id = logical_key_to_event_id.get(event_key)
            if not event_id and event_key.startswith('ev-'):
                event_id = event_key[3:]

        if not event_id or not sub_id:
            continue

        if evi_id in db_existing_evi_ids:
            continue

        if event_id not in db_existing_event_ids:
            continue

        if sub_id not in db_existing_sub_ids:
            session.merge(ProfileSubmissionModel(
                submission_id=sub_id,
                idempotency_key=sub_id,
                book_id='default-book',
                edition_id='default-edition',
                created_at=datetime.now(timezone.utc),
            ))
            db_existing_sub_ids.add(sub_id)

        resolved_event_key = event_key or event_id_to_logical_key.get(event_id, f'ev-{event_id}')
        evi_model = ProfileEvidenceModel(
            evidence_id=evi_id,
            event_id=event_id,
            event_key=resolved_event_key,
            source_group_id=data.get('source_group_id', ''),
            submission_id=sub_id,
            excerpt=data.get('excerpt', ''),
            confidence=float(data.get('confidence', 1.0)),
            created_at=parse_datetime(data.get('created_at')) or datetime.now(timezone.utc),
        )
        session.add(evi_model)
        db_existing_evi_ids.add(evi_id)
        counts['evidence'] += 1

    session.flush()
    return counts


def run_audit_verification(session) -> bool:
    logger.info('=== Running Post-Migration Audit Verification ===')
    from scripts.audit_structured_storage import audit_entities
    return audit_entities(session)


def _validate_r2_connectivity() -> None:
    """Ensure R2 is reachable before migration. Fail fast if not."""
    if not storage_repo.is_r2_active:
        raise RuntimeError(
            "R2 storage is not active (missing credentials or bucket config). "
            "Cannot run backfill without R2 access. Set CLOUDFLARE_R2_ACCESS_KEY_ID, "
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY, and CLOUDFLARE_R2_BUCKET_NAME."
        )
    # Probe with a small listing to verify credentials work
    try:
        storage_repo.list_files(prefix='novels/', raise_on_error=True)
    except Exception as exc:
        raise RuntimeError(f"R2 connectivity check failed: {exc}") from exc
    logger.info("R2 connectivity verified OK.")


def main():
    # Guard: only run when explicitly requested
    if os.environ.get("RUN_BACKFILL", "").strip() != "1":
        logger.info(
            "Skipping R2→PostgreSQL backfill (RUN_BACKFILL != 1). "
            "Set RUN_BACKFILL=1 to run the one-time migration."
        )
        return

    logger.info('=== Starting ONE-TIME Structured Storage Migration (R2 → PostgreSQL) ===')

    # Validate R2 connectivity before doing anything
    _validate_r2_connectivity()

    with db_session() as session:
        novels_count = migrate_novels(session)
        bibles_count = migrate_book_bibles(session)
        jobs_count = migrate_translation_jobs(session)
        profile_counts = migrate_character_profiles(session)
        session.commit()

        # Run verification audit
        audit_ok = run_audit_verification(session)
        if not audit_ok:
            logger.error('CRITICAL: Post-migration audit failed! Aborting.')
            sys.exit(1)



    logger.info('=== Migration Summary ===')
    logger.info(f'Novels imported: {novels_count}')
    logger.info(f'Book Bibles imported: {bibles_count}')
    logger.info(f'Translation Jobs imported: {jobs_count}')
    for k, v in profile_counts.items():
        logger.info(f'Profile {k}: {v}')
    logger.info('=== Migration Completed & Verified Successfully ===')


if __name__ == '__main__':
    main()

