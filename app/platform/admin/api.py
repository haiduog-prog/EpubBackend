from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.dependencies import require_write_access
from app.infrastructure.storage.facade import storage_repo
from app.modules.character_profiles.api import profile_service
from app.modules.library.application.facade import library_service
from app.schemas.book_bible import BookBible
from app.schemas.translation import TranslationJob

router = APIRouter(prefix="/admin", tags=["Admin & Database Inspector"])


class StorageSummaryResponse(BaseModel):
    jobs: List[TranslationJob]
    bibles: Dict[str, BookBible]


@router.get("/storage/summary", response_model=StorageSummaryResponse)
def get_storage_summary(_: None = Depends(require_write_access)):
    return StorageSummaryResponse(
        jobs=storage_repo.list_jobs(),
        bibles=storage_repo.list_bibles(),
    )


@router.post("/clean-test-data")
def clean_test_data(_: None = Depends(require_write_access)):
    if not storage_repo.is_r2_active:
        return {
            "status": "skipped",
            "reason": "R2 is not active on this environment",
            "deleted_count": 0,
            "deleted_keys": [],
        }

    test_patterns = [
        "test-",
        "test_",
        "-test-",
        "-test.",
        "test novel",
        "di-nang-giao-su-260818",
        "dau-pha-test",
        "pham-nhan-test",
        "tru-tien-test",
        "au-la-ai-luc",
        "co-chan-nhan-dich",
    ]

    matching_books = []
    for book_id, book_data in profile_service.list_book_records_for_maintenance():
        metadata = book_data.get("metadata")
        title = (
            metadata.title
            if hasattr(metadata, "title")
            else metadata.get("title", "")
            if isinstance(metadata, dict)
            else ""
        )
        if any(
            pattern in (title or book_id).lower() or pattern in book_id.lower()
            for pattern in test_patterns
        ):
            matching_books.append(book_id)

    for book_id in matching_books:
        profile_service.delete_book(book_id)

    for novel in library_service.list_novels():
        novel_id = getattr(novel, "novel_id", "")
        if any(pattern in novel_id.lower() for pattern in test_patterns):
            library_service.delete_novel(novel_id)

    for bible_id in list(storage_repo.list_bibles().keys()):
        if any(pattern in bible_id.lower() for pattern in test_patterns):
            storage_repo.delete_bible(bible_id)

    deleted_keys = [
        key
        for key in storage_repo.list_files()
        if any(pattern in key.lower() for pattern in test_patterns)
    ]
    if deleted_keys:
        storage_repo.delete_files(deleted_keys)

    return {
        "status": "success",
        "deleted_count": len(deleted_keys),
        "deleted_keys": deleted_keys,
        "deleted_books_from_memory": matching_books,
    }


@router.post("/purge-legacy-data-folders")
def purge_legacy_data_folders(_: None = Depends(require_write_access)):
    if not storage_repo.is_blob_active and not storage_repo.is_r2_active:
        return {
            "status": "skipped",
            "reason": "Storage is not active on this environment",
            "deleted_count": 0,
            "deleted_keys": [],
        }

    legacy_prefixes = [
        "data/profile_submissions/",
        "data/profile_events/",
        "data/profile_books/",
        "data/profile_editions/",
        "data/bibles/",
    ]
    deleted_keys = [
        key
        for prefix in legacy_prefixes
        for key in storage_repo.list_files(prefix)
    ]
    if deleted_keys:
        storage_repo.delete_files(deleted_keys)

    return {
        "status": "success",
        "message": f"Da don dep sach {len(deleted_keys)} file cu trong data/!",
        "deleted_count": len(deleted_keys),
        "deleted_keys": deleted_keys,
    }


__all__ = [
    "router",
    "StorageSummaryResponse",
    "get_storage_summary",
    "clean_test_data",
    "purge_legacy_data_folders",
]
