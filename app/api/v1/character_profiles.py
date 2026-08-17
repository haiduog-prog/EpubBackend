"""HTTP API for the chapter-aware shared character profile Book Bible."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core import storage_repo
from app.llm import create_llm_client
from app.schemas.character_profile import (
    BookResolutionRequest,
    BookResolutionResponse,
    ChapterMapping,
    ChapterMappingRequest,
    CharacterEvent,
    CharacterSnapshotResponse,
    EditionCreateRequest,
    EditionRecord,
    SubmissionRecord,
    SubmissionStatusResponse,
    ChapterSubmissionRequest,
)
from app.services.character_profile_service import CharacterProfileService

router = APIRouter(prefix="/book-bible", tags=["Chapter-aware Book Bible"])
profile_service = CharacterProfileService(firestore_db=storage_repo.firestore_db)


def _require_trusted_client(client_key: Optional[str]) -> None:
    """Require a server-configured credential when one is configured.

    Local development remains usable when BOOK_BIBLE_WRITE_TOKEN is unset. In
    a deployed environment the token should be replaced by App Check/attestation
    verification rather than embedding a secret in the APK.
    """
    expected = os.getenv("BOOK_BIBLE_WRITE_TOKEN", "").strip()
    if expected and not client_key:
        raise HTTPException(status_code=401, detail="Trusted Book Bible client credential required.")
    if expected and not hmac.compare_digest(expected, client_key or ""):
        raise HTTPException(status_code=403, detail="Invalid Book Bible client credential.")


def _content_fingerprint(content: Optional[str], supplied: Optional[str]) -> str:
    if supplied:
        return supplied
    if content:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ""


@router.post("/books/resolve", response_model=BookResolutionResponse)
def resolve_book(request: BookResolutionRequest):
    return profile_service.resolve_book(request)


@router.post("/books/{book_id}/editions", response_model=EditionRecord)
def create_edition(
    book_id: str,
    request: EditionCreateRequest,
    x_book_bible_client_key: Optional[str] = Header(default=None),
):
    _require_trusted_client(x_book_bible_client_key)
    try:
        return profile_service.create_edition(book_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.post(
    "/editions/{edition_id}/chapters/{local_chapter}/mapping",
    response_model=ChapterMapping,
)
def put_chapter_mapping(
    edition_id: str,
    local_chapter: int,
    request: ChapterMappingRequest,
    x_book_bible_client_key: Optional[str] = Header(default=None),
):
    _require_trusted_client(x_book_bible_client_key)
    if request.local_chapter_index != local_chapter:
        raise HTTPException(status_code=400, detail="Path and payload chapter indexes differ.")
    try:
        return profile_service.put_mapping(edition_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


async def _run_raw_extraction(submission_id: str, content: str) -> None:
    try:
        submission = profile_service.get_submission(submission_id)
        if not submission:
            return
        known_names = profile_service.known_names_index(submission.book_id)
        llm_client = create_llm_client()
        delta = await llm_client.extract_book_bible_delta(content, known_names)
        profile_service.process_legacy_delta(submission_id, delta)
    except Exception as exc:
        profile_service.fail_submission(submission_id, "extraction_failed", str(exc))


@router.post(
    "/editions/{edition_id}/chapters/{local_chapter}/submissions",
    response_model=SubmissionStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_chapter(
    edition_id: str,
    local_chapter: int,
    payload: ChapterSubmissionRequest,
    request: Request,
    x_idempotency_key: Optional[str] = Header(default=None),
    x_book_bible_client_key: Optional[str] = Header(default=None),
):
    _require_trusted_client(x_book_bible_client_key)
    if payload.local_chapter_index != local_chapter:
        raise HTTPException(status_code=400, detail="Path and payload chapter indexes differ.")
    edition = profile_service.get_edition(edition_id)
    if not edition:
        raise HTTPException(status_code=404, detail="edition_not_found")
    idempotency_key = x_idempotency_key or hashlib.sha256(
        f"{edition_id}:{local_chapter}:{_content_fingerprint(payload.content, payload.content_fingerprint)}".encode(
            "utf-8"
        )
    ).hexdigest()
    fingerprint = _content_fingerprint(payload.content, payload.content_fingerprint)
    if payload.input_type == "chapter_text" and not fingerprint:
        raise HTTPException(status_code=400, detail="content_fingerprint or content is required.")
    try:
        submission = profile_service.submit(
            book_id=edition.book_id,
            edition_id=edition_id,
            idempotency_key=idempotency_key,
            local_chapter_index=local_chapter,
            input_type=payload.input_type,
            content_fingerprint=fingerprint,
            source_label=payload.source_label,
            content=payload.content,
            candidates=payload.events,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
    if payload.input_type == "chapter_text" and submission.status == "queued":
        asyncio.create_task(_run_raw_extraction(submission.submission_id, payload.content or ""))
    return submission


@router.get("/submissions/{submission_id}", response_model=SubmissionStatusResponse)
def get_submission(submission_id: str):
    submission = profile_service.get_submission(submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="submission_not_found")
    return submission


@router.get(
    "/editions/{edition_id}/chapters/{local_chapter}/snapshot",
    response_model=CharacterSnapshotResponse,
)
def get_snapshot(edition_id: str, local_chapter: int):
    try:
        return profile_service.snapshot(edition_id, local_chapter)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc


@router.get(
    "/editions/{edition_id}/chapters/{local_chapter}/characters/{character_id}/timeline",
    response_model=list[CharacterEvent],
)
def get_character_timeline(edition_id: str, local_chapter: int, character_id: str):
    try:
        return profile_service.timeline(edition_id, local_chapter, character_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc

