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
