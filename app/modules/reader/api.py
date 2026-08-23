from typing import List

from fastapi import APIRouter, HTTPException, Path

from app.modules.reader.schemas import ReaderBookDetail, ReaderBookSummary, ReaderChapterResponse
from app.modules.reader.service import (
    ReaderNotFoundError,
    ReaderValidationError,
    reader_service,
)


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
