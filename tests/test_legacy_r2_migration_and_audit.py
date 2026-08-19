import json
import os
import shutil
import pytest
from app.config import settings
from app.db.base import Base
from app.db.session import reset_db_engine, db_session
from app.core.storage import storage_repo
from scripts.migrate_structured_r2_to_postgres import (
    migrate_novels,
    migrate_book_bibles,
    migrate_translation_jobs,
    migrate_character_profiles,
    compute_logical_event_key,
)
from scripts.audit_structured_storage import audit_entities
from app.repositories.character_profile_repository import CharacterProfileRepository


@pytest.fixture
def mock_legacy_r2_storage(monkeypatch, tmp_path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    # Reconfigure settings
    test_db_url = "sqlite:///file:test_legacy_db?mode=memory&cache=shared&uri=true"
    monkeypatch.setattr(settings, "database_url", test_db_url)
    monkeypatch.setattr(settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(settings, "structured_storage_read_source", "postgres")

    engine = reset_db_engine(test_db_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    # 1. Create Novel JSON
    novel_id = "test-legacy-novel"
    novel_dir = storage_dir / "novels" / novel_id
    novel_dir.mkdir(parents=True, exist_ok=True)
    
    novel_meta = {
        "novel_id": novel_id,
        "title": "Legacy Novel",
        "author": "Legacy Author",
        "chapters": [
            {"chapter_index": 1, "chapter_title": "Chương 1", "chapter_url": "1.html"},
            {"chapter_index": 2, "chapter_title": "Chương 2", "chapter_url": "2.html"},
        ],
    }
    with open(novel_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(novel_meta, f)

    # 2. Create Bible JSON
    bible_data = {
        "novel_id": novel_id,
        "schema_version": 2,
        "bible_revision": 1,
        "characters": [{"character_id": "c1", "original_name": "Hero", "vi_name": "Hero"}],
        "places": [],
        "terms": [],
        "rules": [],
    }
    with open(novel_dir / "bible.json", "w", encoding="utf-8") as f:
        json.dump(bible_data, f)

    # 3. Create Translation Job JSON
    job_dir = storage_dir / "data" / "jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    job_data = {
        "job_id": "legacy-job-1",
        "filename": "book.epub",
        "input_type": "epub",
        "status": "completed",
        "progress_percentage": 100.0,
    }
    with open(job_dir / "legacy-job-1.json", "w", encoding="utf-8") as f:
        json.dump(job_data, f)

    # 4. Create Profile JSONs
    prof_dir = novel_dir / "profile"
    (prof_dir / "profile_books").mkdir(parents=True, exist_ok=True)
    (prof_dir / "profile_editions").mkdir(parents=True, exist_ok=True)
    (prof_dir / "profile_chapter_mappings").mkdir(parents=True, exist_ok=True)
    (prof_dir / "profile_submissions").mkdir(parents=True, exist_ok=True)
    (prof_dir / "profile_events").mkdir(parents=True, exist_ok=True)
    (prof_dir / "profile_evidence").mkdir(parents=True, exist_ok=True)

    book_id = "book-legacy-1"
    edition_id = "edition-legacy-1"
    sub_id = "sub-legacy-1"
    event_id = "event-legacy-1"
    evidence_id = "evidence-legacy-1"

    with open(prof_dir / "profile_books" / f"{book_id}.json", "w", encoding="utf-8") as f:
        json.dump({"book_id": book_id, "title": "Legacy Book", "author": "Author"}, f)

    with open(prof_dir / "profile_editions" / f"{edition_id}.json", "w", encoding="utf-8") as f:
        json.dump({"edition_id": edition_id, "book_id": book_id, "chapter_count": 100}, f)

    with open(prof_dir / "profile_chapter_mappings" / f"{edition_id}-1.json", "w", encoding="utf-8") as f:
        json.dump({"edition_id": edition_id, "local_chapter_index": 1, "canonical_chapter_start": 1, "canonical_chapter_end": 1}, f)

    with open(prof_dir / "profile_submissions" / f"{sub_id}.json", "w", encoding="utf-8") as f:
        json.dump({"submission_id": sub_id, "book_id": book_id, "edition_id": edition_id, "local_chapter_index": 1}, f)

    # Event JSON WITHOUT event_key (legacy format)
    event_candidate_val = "Hero Character"
    with open(prof_dir / "profile_events" / f"{event_id}.json", "w", encoding="utf-8") as f:
        json.dump({
            "event_id": event_id,
            "book_id": book_id,
            "character_id": "char-hero",
            "canonical_chapter": 1,
            "category": "identity",
            "attribute_key": "name",
            "operation": "set",
            "value": event_candidate_val,
            "source_submission_id": sub_id,
            "status": "approved",
            # NOTE: event_key is deliberately missing to test reconstruction
        }, f)

    # Compute expected logical key for evidence
    logical_key = compute_logical_event_key(
        book_id=book_id,
        character_id="char-hero",
        canonical_chapter=1,
        category="identity",
        attribute_key="name",
        operation="set",
        value=event_candidate_val,
    )

    # Evidence JSON with only logical event_key (NO event_id)
    with open(prof_dir / "profile_evidence" / f"{evidence_id}.json", "w", encoding="utf-8") as f:
        json.dump({
            "evidence_id": evidence_id,
            "event_key": logical_key,
            # NOTE: event_id is deliberately missing to test mapping
            "submission_id": sub_id,
            "source_group_id": "legacy-group",
            "excerpt": "Evidence excerpt",
            "confidence": 1.0,
        }, f)

    # Monkeypatch storage_repo to read from tmp_path/storage
    def mock_list_files(prefix="", raise_on_error=False):
        keys = []
        base = str(storage_dir)
        for root, _, files in os.walk(base):
            for file in files:
                full = os.path.join(root, file)
                rel = os.path.relpath(full, base).replace("\\", "/")
                if rel.startswith(prefix):
                    keys.append(rel)
        return sorted(keys)

    def mock_download_json(key, raise_on_error=False):
        full_path = os.path.join(storage_dir, key)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None


    monkeypatch.setattr(storage_repo, "list_files", mock_list_files)
    monkeypatch.setattr(storage_repo, "download_json", mock_download_json)

    yield engine
    Base.metadata.drop_all(engine)


def test_legacy_r2_migration_and_strict_audit(mock_legacy_r2_storage):
    # 1. Run Migration
    with db_session() as session:
        novels = migrate_novels(session)
        bibles = migrate_book_bibles(session)
        jobs = migrate_translation_jobs(session)
        profiles = migrate_character_profiles(session)
        session.commit()

    assert novels == 1
    assert bibles == 1
    assert jobs == 1
    assert profiles["books"] == 1
    assert profiles["editions"] == 1
    assert profiles["mappings"] == 1
    assert profiles["submissions"] == 1
    assert profiles["events"] == 1
    assert profiles["evidence"] == 1  # Evidence MUST be successfully migrated!

    # 2. Verify Evidence was linked properly to Event in DB
    with db_session() as session:
        from app.db.models.character_profile import ProfileEvidenceModel
        ev_model = session.get(ProfileEvidenceModel, "evidence-legacy-1")
        assert ev_model is not None
        assert ev_model.event_id == "event-legacy-1"
        assert ev_model.source_group_id == "legacy-group"

        # 3. Run Strict Audit Verification
        audit_passed = audit_entities(session)
        assert audit_passed is True

