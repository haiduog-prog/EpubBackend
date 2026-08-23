from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import storage_repo
from app.api.dependencies import require_write_access
from app.schemas.book_bible import BookBible, PendingBibleChange

router = APIRouter(prefix="/book-bible", tags=["Book Bible"])


class PendingReviewRequest(BaseModel):
    reviewed_by: Optional[str] = None


@router.get("/{novel_id}/pending", response_model=List[PendingBibleChange])
def list_pending_changes(novel_id: str):
    bible = storage_repo.get_bible(novel_id)
    if not bible:
        raise HTTPException(status_code=404, detail="Chua co Book Bible cho novel nay.")
    return [item for item in bible.pending_changes if item.status == "pending"]


@router.post(
    "/{novel_id}/pending/{change_id}/approve",
    response_model=BookBible,
)
def approve_pending_change(
    novel_id: str,
    change_id: str,
    request: PendingReviewRequest,
    _: None = Depends(require_write_access),
):
    bible = storage_repo.review_pending_change(
        novel_id, change_id, "approved", request.reviewed_by
    )
    if not bible:
        raise HTTPException(status_code=404, detail="Khong tim thay pending change.")
    return bible


@router.post(
    "/{novel_id}/pending/{change_id}/reject",
    response_model=BookBible,
)
def reject_pending_change(
    novel_id: str,
    change_id: str,
    request: PendingReviewRequest,
    _: None = Depends(require_write_access),
):
    bible = storage_repo.review_pending_change(
        novel_id, change_id, "rejected", request.reviewed_by
    )
    if not bible:
        raise HTTPException(status_code=404, detail="Khong tim thay pending change.")
    return bible


@router.get("/{job_id}", response_model=BookBible)
def get_book_bible(job_id: str):
    bible = storage_repo.get_bible(job_id)
    if not bible:
        raise HTTPException(status_code=404, detail="Chua co Book Bible cho ID nay.")
    return bible

