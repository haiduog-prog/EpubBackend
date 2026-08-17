"""Schemas for the shared, chapter-aware character profile Book Bible."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


Certainty = Literal[
    "observed",
    "stated",
    "rumor",
    "inferred",
    "contradicted",
]

EventStatus = Literal["pending", "approved", "superseded", "rejected"]


class FingerprintBundle(BaseModel):
    file: Optional[str] = None
    edition: Optional[str] = None
    structure: Optional[str] = None
    sampled_chapters: List[str] = Field(default_factory=list)


class BookMetadata(BaseModel):
    title: str = ""
    author: str = ""
    language: str = ""
    publisher: str = ""
    identifier: Optional[str] = None


class BookResolutionRequest(BaseModel):
    metadata: BookMetadata
    fingerprints: FingerprintBundle = Field(default_factory=FingerprintBundle)
    book_id: Optional[str] = None
    create_if_missing: bool = True


class BookMatchCandidate(BaseModel):
    book_id: str
    title: str
    author: str = ""
    score: float = 0.0
    reasons: List[str] = Field(default_factory=list)


class BookResolutionResponse(BaseModel):
    status: Literal["matched", "confirmation_required", "new_book"]
    book_id: Optional[str] = None
    candidates: List[BookMatchCandidate] = Field(default_factory=list)


class EditionCreateRequest(BaseModel):
    metadata: BookMetadata = Field(default_factory=BookMetadata)
    fingerprints: FingerprintBundle = Field(default_factory=FingerprintBundle)
    chapter_count: Optional[int] = Field(default=None, ge=0)


class EditionRecord(BaseModel):
    edition_id: str
    book_id: str
    metadata: BookMetadata
    fingerprints: FingerprintBundle = Field(default_factory=FingerprintBundle)
    chapter_count: Optional[int] = None
    mapping_revision: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChapterMappingRequest(BaseModel):
    local_chapter_index: int = Field(ge=0)
    canonical_chapter_start: int = Field(ge=0)
    canonical_chapter_end: Optional[int] = Field(default=None, ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: Literal["metadata", "fingerprint", "user", "legacy"] = "metadata"

    @field_validator("canonical_chapter_end")
    @classmethod
    def end_not_before_start(cls, value: Optional[int], info):
        start = info.data.get("canonical_chapter_start")
        if value is not None and start is not None and value < start:
            raise ValueError("canonical_chapter_end must not be before start")
        return value


class ChapterMapping(BaseModel):
    edition_id: str
    local_chapter_index: int
    canonical_chapter_start: int
    canonical_chapter_end: int
    confidence: float = 1.0
    source: str = "metadata"
    mapping_revision: int = 1


class CharacterEventCandidate(BaseModel):
    """A candidate emitted by an extractor or trusted structured client."""

    character_original_name: str
    character_id: Optional[str] = None
    category: str
    attribute_key: str
    operation: Literal[
        "set",
        "add",
        "remove",
        "increase",
        "decrease",
        "link",
        "unlink",
        "correct",
    ] = "set"
    value: Any = None
    certainty: Certainty = "observed"
    evidence: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    explicit_transition: bool = False


class CharacterEvent(BaseModel):
    event_id: str
    book_id: str
    character_id: str
    character_original_name: str
    canonical_chapter: int = Field(ge=0)
    category: str
    attribute_key: str
    operation: str
    value: Any = None
    certainty: Certainty = "observed"
    status: EventStatus = "pending"
    evidence: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_group_id: str
    source_submission_id: str
    supersedes_event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    schema_version: int = 1


class EventEvidence(BaseModel):
    evidence_id: str
    event_key: str
    source_group_id: str
    submission_id: str
    excerpt: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChapterSubmissionRequest(BaseModel):
    local_chapter_index: int = Field(ge=0)
    chapter_id: Optional[str] = None
    input_type: Literal["chapter_text", "structured_events"]
    content: Optional[str] = None
    content_fingerprint: Optional[str] = None
    events: List[CharacterEventCandidate] = Field(default_factory=list)
    source_label: Optional[str] = None

    @field_validator("content")
    @classmethod
    def content_required_for_text(cls, value: Optional[str], info):
        input_type = info.data.get("input_type")
        if input_type == "chapter_text" and not (value or "").strip():
            raise ValueError("content is required for chapter_text submissions")
        return value


class SubmissionRecord(BaseModel):
    submission_id: str
    idempotency_key: str
    book_id: str
    edition_id: str
    local_chapter_index: int
    canonical_chapter_start: int
    canonical_chapter_end: int
    input_type: Literal["chapter_text", "structured_events"]
    content_fingerprint: str
    source_group_id: str
    source_label: Optional[str] = None
    status: Literal[
        "queued",
        "processing",
        "reviewing",
        "completed",
        "failed",
    ] = "queued"
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    event_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class CharacterSnapshot(BaseModel):
    character_id: str
    original_name: str
    attributes: Dict[str, Any] = Field(default_factory=dict)
    last_changed_chapter: Optional[int] = None


class CharacterSnapshotResponse(BaseModel):
    book_id: str
    edition_id: str
    requested_chapter: int
    canonical_chapter: int
    book_revision: int = 0
    projection_revision: int = 0
    projection_status: Literal["ready", "stale"] = "ready"
    snapshot_status: Literal["complete", "partial"] = "complete"
    complete_through_chapter: Optional[int] = None
    pending_chapters: List[int] = Field(default_factory=list)
    characters: List[CharacterSnapshot] = Field(default_factory=list)


class SubmissionStatusResponse(SubmissionRecord):
    pass

