from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.api.dependencies import require_write_access
from app.infrastructure.storage.facade import storage_repo
from app.modules.book_bible.domain.schema_migration import migrate_book_bible_to_v3
from app.modules.book_bible.legacy_service import LegacyBookBibleService
from app.schemas.book_bible import (
    BookBible,
    CharacterEntry,
    PendingBibleChange,
    SourceProfile,
    StyleGuide,
    TermEntry,
)

router = APIRouter(prefix="/book-bible", tags=["Book Bible"])


class PendingReviewRequest(BaseModel):
    reviewed_by: Optional[str] = None


class StyleGuideUpdateRequest(BaseModel):
    style_guide: StyleGuide
    source_profile: Optional[SourceProfile] = None


@router.get("/{novel_id}/pending", response_model=List[PendingBibleChange])
def list_pending_changes(novel_id: str):
    bible = storage_repo.get_bible(novel_id)
    if not bible:
        raise HTTPException(status_code=404, detail="Chưa có Book Bible cho truyện này.")
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
        raise HTTPException(status_code=404, detail="Không tìm thấy pending change.")
    return migrate_book_bible_to_v3(bible)


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
        raise HTTPException(status_code=404, detail="Không tìm thấy pending change.")
    return migrate_book_bible_to_v3(bible)


@router.get("/{novel_id}/export/json")
def export_book_bible_json(novel_id: str):
    bible = storage_repo.get_bible(novel_id)
    if not bible:
        raise HTTPException(status_code=404, detail="Chưa có Book Bible cho truyện này.")
    bible_v3 = migrate_book_bible_to_v3(bible)
    content = bible_v3.model_dump_json(indent=2, by_alias=True)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="book_bible_{novel_id}.json"'
        },
    )


@router.post(
    "/{novel_id}/import/json",
    response_model=BookBible,
)
def import_book_bible_json(
    novel_id: str,
    payload: Dict[str, Any],
    _: None = Depends(require_write_access),
):
    payload["novel_id"] = novel_id
    migrated = migrate_book_bible_to_v3(payload)
    migrated.bible_revision += 1
    storage_repo.save_bible(novel_id, migrated)
    return migrated


@router.put(
    "/{novel_id}/terms",
    response_model=BookBible,
)
def upsert_term(
    novel_id: str,
    term: TermEntry,
    _: None = Depends(require_write_access),
):
    bible = storage_repo.get_bible(novel_id)
    if not bible:
        bible = BookBible(novel_id=novel_id)
    bible_v3 = migrate_book_bible_to_v3(bible)

    term_key = LegacyBookBibleService._key(term.original_name)
    vi_key = LegacyBookBibleService._key(term.vi_name)

    found = False
    for i, existing in enumerate(bible_v3.terms):
        if (
            LegacyBookBibleService._key(existing.original_name) == term_key
            or (existing.vi_name and LegacyBookBibleService._key(existing.vi_name) == vi_key)
        ):
            # Merge fields into existing to prevent losing metadata/aliases
            existing.vi_name = term.vi_name or existing.vi_name
            if term.category:
                existing.category = term.category
            if term.family:
                existing.family = term.family
            if term.rank_order is not None:
                existing.rank_order = term.rank_order
            existing.locked = term.locked
            for v in term.forbidden_variants:
                if v and v not in existing.forbidden_variants:
                    existing.forbidden_variants.append(v)
            for a in term.aliases:
                if a and a not in existing.aliases:
                    existing.aliases.append(a)
            if term.evidence:
                existing.evidence = term.evidence
            if term.notes:
                existing.notes = term.notes
            found = True
            break
    if not found:
        bible_v3.terms.append(term)

    bible_v3.bible_revision += 1
    storage_repo.save_bible(novel_id, bible_v3)
    return bible_v3


@router.put(
    "/{novel_id}/characters",
    response_model=BookBible,
)
def upsert_character(
    novel_id: str,
    character: CharacterEntry,
    _: None = Depends(require_write_access),
):
    bible = storage_repo.get_bible(novel_id)
    if not bible:
        bible = BookBible(novel_id=novel_id)
    bible_v3 = migrate_book_bible_to_v3(bible)

    char_key = LegacyBookBibleService._key(character.original_name)
    vi_key = LegacyBookBibleService._key(character.vi_name)

    found = False
    for i, existing in enumerate(bible_v3.characters):
        if (
            LegacyBookBibleService._key(existing.original_name) == char_key
            or (existing.vi_name and LegacyBookBibleService._key(existing.vi_name) == vi_key)
        ):
            # Merge fields into existing to avoid losing address_terms, character_id, aliases
            existing.vi_name = character.vi_name or existing.vi_name
            if character.role:
                existing.role = character.role
            if character.narrative_term:
                existing.narrative_term = character.narrative_term
            if character.voice_notes:
                existing.voice_notes = character.voice_notes
            existing.locked = character.locked
            for v in character.forbidden_variants:
                if v and v not in existing.forbidden_variants:
                    existing.forbidden_variants.append(v)
            for a in character.aliases:
                if a and a not in existing.aliases:
                    existing.aliases.append(a)
            if character.address_terms:
                existing.address_terms = character.address_terms
            if getattr(character, "evidence", None):
                existing.evidence = character.evidence
            if getattr(character, "notes", None):
                existing.notes = character.notes
            found = True
            break
    if not found:
        bible_v3.characters.append(character)

    bible_v3.bible_revision += 1
    storage_repo.save_bible(novel_id, bible_v3)
    return bible_v3


@router.put(
    "/{novel_id}/style-guide",
    response_model=BookBible,
)
def update_style_guide(
    novel_id: str,
    update: StyleGuideUpdateRequest,
    _: None = Depends(require_write_access),
):
    bible = storage_repo.get_bible(novel_id)
    if not bible:
        bible = BookBible(novel_id=novel_id)
    bible_v3 = migrate_book_bible_to_v3(bible)

    if update.style_guide:
        sg_data = update.style_guide.model_dump(exclude_unset=True)
        for k, v in sg_data.items():
            if v is not None and (not isinstance(v, (str, list)) or v):
                setattr(bible_v3.style_guide, k, v)
    if update.source_profile:
        sp_data = update.source_profile.model_dump(exclude_unset=True)
        for k, v in sp_data.items():
            if v is not None and (not isinstance(v, (str, list)) or v):
                setattr(bible_v3.source_profile, k, v)

    bible_v3.bible_revision += 1
    storage_repo.save_bible(novel_id, bible_v3)
    return bible_v3


@router.get("/{job_id}", response_model=BookBible)
def get_book_bible(job_id: str):
    bible = storage_repo.get_bible(job_id)
    if not bible:
        raise HTTPException(status_code=404, detail="Chưa có Book Bible cho ID này.")
    return migrate_book_bible_to_v3(bible)
