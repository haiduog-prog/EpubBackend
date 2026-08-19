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

    bucket = settings.cloudflare_r2_bucket_name
    client = storage_repo.r2_client
    paginator = client.get_paginator("list_objects_v2")

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
    ]

    for page in paginator.paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            key = item.get("Key", "")
            lower_key = key.lower()
            if any(pat in lower_key for pat in test_patterns):
                deleted_keys.append({"Key": key})

    if deleted_keys:
        for i in range(0, len(deleted_keys), 500):
            chunk = deleted_keys[i : i + 500]
            client.delete_objects(Bucket=bucket, Delete={"Objects": chunk})

    return {
        "status": "success",
        "deleted_count": len(deleted_keys),
        "deleted_keys": [item["Key"] for item in deleted_keys],
    }

