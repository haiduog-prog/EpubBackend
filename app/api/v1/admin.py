from typing import Dict, List
from fastapi import APIRouter
from pydantic import BaseModel
from app.schemas.translation import TranslationJob
from app.schemas.book_bible import BookBible
from app.core import storage_repo

router = APIRouter(prefix="/admin", tags=["Admin & Database Inspector"])


class StorageSummaryResponse(BaseModel):
    jobs: List[TranslationJob]
    bibles: Dict[str, BookBible]


@router.get("/storage/summary", response_model=StorageSummaryResponse)
def get_storage_summary():
    """
    Trả về toàn bộ danh sách Jobs và Book Bibles trong Database hiện tại để hiển thị lên UI Inspector.
    """
    jobs = storage_repo.list_jobs()
    bibles = storage_repo.list_bibles()
    return StorageSummaryResponse(jobs=jobs, bibles=bibles)


@router.post("/clean-test-data")
def clean_test_data():
    """
    Quét và xóa tất cả các file test, dữ liệu thử nghiệm trong Cloudflare R2 bucket.
    """
    if not storage_repo.is_r2_active:
        return {
            "status": "skipped",
            "reason": "R2 is not active on this environment",
            "deleted_count": 0,
            "deleted_keys": [],
        }

    from app.config import settings
    from app.api.v1.character_profiles import profile_service
    from app.services.library_service import library_service

    deleted_keys = []
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

    # 1. Clean in-memory character profile books & bibles
    matching_books = []
    for bid, bdata in list(profile_service.books.items()):
        meta = bdata.get("metadata")
        title = meta.title if hasattr(meta, "title") else (meta.get("title", "") if isinstance(meta, dict) else "")
        title_lower = (title or bid).lower()
        if any(pat in title_lower or pat in bid.lower() for pat in test_patterns):
            matching_books.append(bid)

    for bid in matching_books:
        profile_service.delete_book(bid)

    # 2. Clean library service cache & storage bibles
    for nid in list(library_service._cache.keys()):
        if any(pat in nid.lower() for pat in test_patterns):
            library_service.delete_novel(nid)

    if hasattr(storage_repo, "_bibles"):
        for bid in list(storage_repo._bibles.keys()):
            if any(pat in bid.lower() for pat in test_patterns):
                storage_repo.delete_bible(bid)

    # 3. Clean Cloud/Local Storage
    all_keys = storage_repo.list_files()
    for key in all_keys:
        lower_key = key.lower()
        if any(pat in lower_key for pat in test_patterns):
            deleted_keys.append(key)

    if deleted_keys:
        storage_repo.delete_files(deleted_keys)

    return {
        "status": "success",
        "deleted_count": len(deleted_keys),
        "deleted_keys": deleted_keys,
        "deleted_books_from_memory": matching_books,
    }



@router.post("/purge-legacy-data-folders")
def purge_legacy_data_folders():
    """
    Xóa toàn bộ các file rời rạc cũ trong prefix data/ (data/profile_submissions/, data/profile_events/, data/profile_books/, data/profile_editions/, data/bibles/)
    để chuyển sang 100% cấu trúc mới gom theo thư mục tên truyện: novels/{novel_id}/.
    """
    if not storage_repo.is_blob_active and not storage_repo.is_r2_active:
        return {
            "status": "skipped",
            "reason": "Storage is not active on this environment",
            "deleted_count": 0,
            "deleted_keys": [],
        }

    deleted_keys = []
    legacy_prefixes = [
        "data/profile_submissions/",
        "data/profile_events/",
        "data/profile_books/",
        "data/profile_editions/",
        "data/bibles/",
    ]

    for prefix in legacy_prefixes:
        files = storage_repo.list_files(prefix)
        deleted_keys.extend(files)

    if deleted_keys:
        storage_repo.delete_files(deleted_keys)

    return {
        "status": "success",
        "message": f"Đã dọn dẹp sạch {len(deleted_keys)} file cũ trong data/! Giờ đây toàn bộ dữ liệu chỉ nằm gọn trong novels/{{novel_id}}/",
        "deleted_count": len(deleted_keys),
        "deleted_keys": deleted_keys,
    }


