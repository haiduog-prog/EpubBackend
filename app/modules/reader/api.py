from io import BytesIO
from mimetypes import guess_type
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse

from app.auth import AuthUser, get_current_user
from app.db.session import get_db
from app.infrastructure.storage.facade import storage_repo
from app.modules.reader.schemas import ReaderBookDetail, ReaderBookSummary, ReaderChapterResponse
from app.modules.reader.service import ReaderNotFoundError, ReaderValidationError, reader_service
from app.modules.reader.sync_schemas import (
    ReaderLocalMigrationPayload,
    ReaderPreferencesPayload,
    ReaderProgressPayload,
    ReaderProgressUpdatePayload,
    ReaderStateResponse,
)
from app.modules.reader.sync_service import reader_sync_service


router = APIRouter(prefix="/reader", tags=["Web Reader"])


@router.get("/books", response_model=List[ReaderBookSummary])
def list_reader_books():
    return reader_service.list_books()


@router.get("/books/{novel_id}", response_model=ReaderBookDetail)
def get_reader_book(novel_id: str):
    try:
        return reader_service.get_book(novel_id)
    except ReaderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReaderNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/books/{novel_id}/cover")
def get_reader_cover(novel_id: str):
    """Serve covers through the authenticated API instead of public storage URLs."""
    try:
        reader_service._validate_novel_id(novel_id)
    except ReaderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metadata = reader_service._library.get_novel(novel_id)
    if not metadata or not metadata.cover_url:
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh bìa.")
    cover_url = str(metadata.cover_url)
    if "/novels/" in cover_url:
        key = "novels/" + cover_url.split("/novels/", 1)[1]
    elif "/storage/" in cover_url:
        key = cover_url.split("/storage/", 1)[-1].lstrip("/")
    else:
        key = ""
    if not key.startswith("novels/"):
        key = f"novels/{novel_id}/cover.jpg"
    data = storage_repo.get_bytes(key)
    if data is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh bìa.")
    content_type = guess_type(key)[0] or "image/jpeg"
    return StreamingResponse(BytesIO(data), media_type=content_type, headers={"Cache-Control": "private, max-age=300"})


@router.get("/books/{novel_id}/chapters/{chapter_index}", response_model=ReaderChapterResponse)
def get_reader_chapter(
    novel_id: str,
    chapter_index: int = Path(..., ge=1),
):
    try:
        return reader_service.get_chapter(novel_id, chapter_index)
    except ReaderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReaderNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/me/state", response_model=ReaderStateResponse)
def get_reader_state(user: AuthUser = Depends(get_current_user), db=Depends(get_db)):
    return reader_sync_service.state(db, user.user_id)


@router.post("/me/migrate-local", response_model=ReaderStateResponse)
def migrate_reader_state(
    payload: ReaderLocalMigrationPayload,
    user: AuthUser = Depends(get_current_user),
    db=Depends(get_db),
):
    return reader_sync_service.migrate_local(db, user.user_id, payload)


@router.put("/me/preferences", response_model=ReaderStateResponse)
def update_reader_preferences(
    payload: ReaderPreferencesPayload,
    user: AuthUser = Depends(get_current_user),
    db=Depends(get_db),
):
    return reader_sync_service.update_preferences(db, user.user_id, payload.preferences)


@router.put("/me/progress/{novel_id}", response_model=ReaderStateResponse)
def update_reader_progress(
    novel_id: str,
    payload: ReaderProgressUpdatePayload,
    user: AuthUser = Depends(get_current_user),
    db=Depends(get_db),
):
    return reader_sync_service.update_progress(db, user.user_id, novel_id, payload.chapter_index, payload.scroll_top)