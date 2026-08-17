"""Chapter-aware shared character profile engine.

The legacy BookBibleService remains responsible for translation terminology and
address rules. This service owns the new append-only character event stream.
It deliberately has a small repository-like surface so it can use memory in
tests/development and Firestore when configured by the API process.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.schemas.book_bible import BookBible, BookBibleDelta
from app.schemas.character_profile import (
    BookMatchCandidate,
    BookMetadata,
    BookResolutionRequest,
    BookResolutionResponse,
    ChapterMapping,
    ChapterMappingRequest,
    CharacterEvent,
    CharacterEventCandidate,
    CharacterSnapshot,
    CharacterSnapshotResponse,
    EditionCreateRequest,
    EditionRecord,
    EventEvidence,
    FingerprintBundle,
    SubmissionRecord,
)

logger = logging.getLogger("EpubBackend.CharacterProfileService")


def _norm(value: Optional[str]) -> str:
    return " ".join((value or "").casefold().split())


def _hash(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


class CharacterProfileService:
    """In-process canonical event store with optional Firestore mirroring."""

    def __init__(self, firestore_db=None, min_independent_sources: int = 2):
        self.firestore_db = firestore_db
        self.min_independent_sources = max(1, min_independent_sources)
        self._lock = threading.RLock()
        self.books: Dict[str, Dict[str, Any]] = {}
        self.editions: Dict[str, EditionRecord] = {}
        self.mappings: Dict[Tuple[str, int], ChapterMapping] = {}
        self.submissions: Dict[str, SubmissionRecord] = {}
        self.submissions_by_key: Dict[str, str] = {}
        self.events: Dict[str, CharacterEvent] = {}
        self.evidence: Dict[str, EventEvidence] = {}
        self._event_keys: Dict[str, str] = {}
        self._event_evidence_groups: Dict[str, set[str]] = {}
        self._processed_chapters: Dict[str, set[int]] = {}
        self._book_revisions: Dict[str, int] = {}
        self._snapshot_cache: Dict[Tuple[str, int, int], CharacterSnapshotResponse] = {}

    # ------------------------------------------------------------------
    # Book and edition identity
    # ------------------------------------------------------------------
    @staticmethod
    def book_id_for(metadata: BookMetadata) -> str:
        raw = f"{_norm(metadata.title)}|{_norm(metadata.author)}|{_norm(metadata.language)}"
        return f"book-{_hash(raw, 24)}"

    @staticmethod
    def edition_id_for(book_id: str, metadata: BookMetadata, fingerprints: FingerprintBundle) -> str:
        raw = json.dumps(
            {
                "book": book_id,
                "metadata": metadata.model_dump(mode="json"),
                "fingerprints": fingerprints.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return f"edition-{_hash(raw, 24)}"

    def _load_book_from_firestore(self, book_id: str) -> None:
        if not self.firestore_db:
            return
        try:
            if book_id not in self.books:
                snapshot = self.firestore_db.collection("profile_books").document(book_id).get()
                if snapshot.exists:
                    raw = snapshot.to_dict() or {}
                    metadata = BookMetadata.model_validate(raw.get("metadata", {}))
                    self.books[book_id] = {
                        "book_id": book_id,
                        "metadata": metadata,
                        "title_key": _norm(metadata.title),
                        "author_key": _norm(metadata.author),
                        "sampled_chapters": raw.get("sampled_chapters", []),
                        "created_at": raw.get("created_at"),
                    }
                    self._book_revisions.setdefault(book_id, 0)
            for doc in self.firestore_db.collection("profile_editions").where("book_id", "==", book_id).stream():
                raw = doc.to_dict() or {}
                if raw.get("book_id") == book_id and doc.id not in self.editions:
                    self.editions[doc.id] = EditionRecord.model_validate(raw)
            for doc in self.firestore_db.collection("profile_events").where("book_id", "==", book_id).stream():
                raw = doc.to_dict() or {}
                if raw.get("book_id") != book_id or doc.id in self.events:
                    continue
                event = CharacterEvent.model_validate(raw)
                self.events[event.event_id] = event
                candidate = CharacterEventCandidate(
                    character_original_name=event.character_original_name,
                    character_id=event.character_id,
                    category=event.category,
                    attribute_key=event.attribute_key,
                    operation=event.operation,
                    value=event.value,
                    certainty=event.certainty,
                    evidence=event.evidence,
                    confidence=event.confidence,
                )
                key = self._event_key(book_id, event.character_id, event.canonical_chapter, candidate)
                self._event_keys[key] = event.event_id
                self._event_evidence_groups.setdefault(key, set()).add(event.source_group_id)
                self._book_revisions[book_id] = max(self._book_revisions.get(book_id, 0), 1 if event.status == "approved" else 0)
        except Exception as exc:
            logger.warning("Book profile Firestore hydration failed book=%s: %s", book_id, exc)

    def get_edition(self, edition_id: str) -> Optional[EditionRecord]:
        edition = self.editions.get(edition_id)
        if edition:
            return edition.model_copy(deep=True)
        if self.firestore_db:
            try:
                snapshot = self.firestore_db.collection("profile_editions").document(edition_id).get()
                if snapshot.exists:
                    edition = EditionRecord.model_validate(snapshot.to_dict())
                    self.editions[edition_id] = edition
                    self._load_book_from_firestore(edition.book_id)
                    return edition.model_copy(deep=True)
            except Exception as exc:
                logger.warning("Book profile edition hydration failed edition=%s: %s", edition_id, exc)
        return None

    def resolve_book(self, request: BookResolutionRequest) -> BookResolutionResponse:
        with self._lock:
            if self.firestore_db and not self.books:
                for doc in self.firestore_db.collection("profile_books").stream():
                    self._load_book_from_firestore(doc.id)
            if request.book_id:
                if request.book_id not in self.books:
                    self._create_book(request.book_id, request.metadata, request.fingerprints)
                return BookResolutionResponse(status="matched", book_id=request.book_id)

            title_key = _norm(request.metadata.title)
            author_key = _norm(request.metadata.author)
            candidates: List[BookMatchCandidate] = []
            for book_id, book in self.books.items():
                score = 0.0
                reasons: List[str] = []
                if title_key and title_key == book["title_key"]:
                    score += 0.55
                    reasons.append("normalized_title")
                elif title_key and title_key in book["title_key"]:
                    score += 0.30
                    reasons.append("title_contains")
                if author_key and author_key == book["author_key"]:
                    score += 0.30
                    reasons.append("normalized_author")
                incoming_samples = set(request.fingerprints.sampled_chapters)
                if incoming_samples & set(book.get("sampled_chapters", [])):
                    score += 0.15
                    reasons.append("sample_fingerprint")
                if score:
                    candidates.append(
                        BookMatchCandidate(
                            book_id=book_id,
                            title=book["metadata"].title,
                            author=book["metadata"].author,
                            score=round(score, 4),
                            reasons=reasons,
                        )
                    )

            candidates.sort(key=lambda item: item.score, reverse=True)
            if candidates and candidates[0].score >= 0.80:
                return BookResolutionResponse(status="matched", book_id=candidates[0].book_id)
            if candidates and candidates[0].score >= 0.35:
                return BookResolutionResponse(
                    status="confirmation_required", candidates=candidates[:5]
                )
            if not request.create_if_missing:
                return BookResolutionResponse(status="confirmation_required", candidates=candidates[:5])

            book_id = self.book_id_for(request.metadata)
            self._create_book(book_id, request.metadata, request.fingerprints)
            return BookResolutionResponse(status="new_book", book_id=book_id)

    def _create_book(self, book_id: str, metadata: BookMetadata, fingerprints: FingerprintBundle) -> None:
        if book_id in self.books:
            return
        self.books[book_id] = {
            "book_id": book_id,
            "metadata": metadata.model_copy(deep=True),
            "title_key": _norm(metadata.title),
            "author_key": _norm(metadata.author),
            "sampled_chapters": list(fingerprints.sampled_chapters),
            "created_at": datetime.utcnow(),
        }
        self._book_revisions.setdefault(book_id, 0)
        self._persist("profile_books", book_id, self.books[book_id])

    def create_edition(self, book_id: str, request: EditionCreateRequest) -> EditionRecord:
        with self._lock:
            self._load_book_from_firestore(book_id)
            if book_id not in self.books:
                self._create_book(book_id, request.metadata, request.fingerprints)
            edition_id = self.edition_id_for(book_id, request.metadata, request.fingerprints)
            existing = self.editions.get(edition_id)
            if existing:
                return existing.model_copy(deep=True)
            edition = EditionRecord(
                edition_id=edition_id,
                book_id=book_id,
                metadata=request.metadata.model_copy(deep=True),
                fingerprints=request.fingerprints.model_copy(deep=True),
                chapter_count=request.chapter_count,
            )
            self.editions[edition_id] = edition
            self._persist("profile_editions", edition_id, edition)
            return edition.model_copy(deep=True)

    def put_mapping(
        self, edition_id: str, request: ChapterMappingRequest
    ) -> ChapterMapping:
        with self._lock:
            edition = self.get_edition(edition_id)
            if not edition:
                raise KeyError("edition_not_found")
            end = request.canonical_chapter_end
            if end is None:
                end = request.canonical_chapter_start
            mapping = ChapterMapping(
                edition_id=edition_id,
                local_chapter_index=request.local_chapter_index,
                canonical_chapter_start=request.canonical_chapter_start,
                canonical_chapter_end=end,
                confidence=request.confidence,
                source=request.source,
                mapping_revision=edition.mapping_revision,
            )
            self.mappings[(edition_id, request.local_chapter_index)] = mapping
            self._persist(
                "profile_chapter_mappings",
                f"{edition_id}-{request.local_chapter_index}",
                mapping,
            )
            return mapping.model_copy(deep=True)

    def get_mapping(self, edition_id: str, local_chapter_index: int) -> ChapterMapping:
        mapping = self.mappings.get((edition_id, local_chapter_index))
        if mapping:
            return mapping.model_copy(deep=True)
        if self.firestore_db:
            try:
                snapshot = self.firestore_db.collection("profile_chapter_mappings").document(f"{edition_id}-{local_chapter_index}").get()
                if snapshot.exists:
                    mapping = ChapterMapping.model_validate(snapshot.to_dict())
                    self.mappings[(edition_id, local_chapter_index)] = mapping
                    return mapping.model_copy(deep=True)
            except Exception as exc:
                logger.warning("Book profile mapping hydration failed edition=%s chapter=%s: %s", edition_id, local_chapter_index, exc)
        if edition_id not in self.editions:
            raise KeyError("edition_not_found")
        # A new edition starts with an identity mapping. Explicit mappings can
        # replace it when a source splits or combines chapters.
        return ChapterMapping(
            edition_id=edition_id,
            local_chapter_index=local_chapter_index,
            canonical_chapter_start=local_chapter_index,
            canonical_chapter_end=local_chapter_index,
            confidence=0.5,
            source="implicit",
            mapping_revision=self.editions[edition_id].mapping_revision,
        )

    # ------------------------------------------------------------------
    # Submissions and event review
    # ------------------------------------------------------------------
    def submit(
        self,
        book_id: str,
        edition_id: str,
        idempotency_key: str,
        local_chapter_index: int,
        input_type: str,
        content_fingerprint: str,
        source_label: Optional[str] = None,
        content: Optional[str] = None,
        candidates: Optional[Iterable[CharacterEventCandidate]] = None,
    ) -> SubmissionRecord:
        with self._lock:
            if edition_id not in self.editions or self.editions[edition_id].book_id != book_id:
                raise KeyError("edition_not_found")
            existing_id = self.submissions_by_key.get(idempotency_key)
            if existing_id:
                return self.submissions[existing_id].model_copy(deep=True)
            mapping = self.get_mapping(edition_id, local_chapter_index)
            if not content_fingerprint:
                source = content or json.dumps(
                    [item.model_dump(mode="json") for item in (candidates or [])],
                    ensure_ascii=False,
                    sort_keys=True,
                )
                content_fingerprint = _hash(source, 64)
            source_group_id = _hash(f"{edition_id}:{content_fingerprint}", 32)
            submission = SubmissionRecord(
                submission_id=f"submission-{uuid.uuid4().hex}",
                idempotency_key=idempotency_key,
                book_id=book_id,
                edition_id=edition_id,
                local_chapter_index=local_chapter_index,
                canonical_chapter_start=mapping.canonical_chapter_start,
                canonical_chapter_end=mapping.canonical_chapter_end,
                input_type=input_type,
                content_fingerprint=content_fingerprint,
                source_group_id=source_group_id,
                source_label=source_label,
                status="processing" if input_type == "structured_events" else "queued",
            )
            self.submissions[submission.submission_id] = submission
            self.submissions_by_key[idempotency_key] = submission.submission_id
            self._persist("profile_submissions", submission.submission_id, submission)
            if input_type == "structured_events":
                self.process_candidates(submission.submission_id, list(candidates or []))
            return submission.model_copy(deep=True)

    def process_candidates(
        self,
        submission_id: str,
        candidates: Iterable[CharacterEventCandidate],
    ) -> SubmissionRecord:
        with self._lock:
            submission = self.submissions.get(submission_id)
            if not submission:
                raise KeyError("submission_not_found")
            submission.status = "reviewing"
            ids: List[str] = []
            try:
                for candidate in candidates:
                    event_id = self._ingest_candidate(submission, candidate)
                    if event_id:
                        ids.append(event_id)
                submission.event_ids = ids
                submission.status = "completed"
                submission.completed_at = datetime.utcnow()
                self._processed_chapters.setdefault(submission.book_id, set()).add(
                    submission.canonical_chapter_end
                )
            except Exception as exc:
                submission.status = "failed"
                submission.error_code = "candidate_processing_failed"
                submission.error_message = str(exc)
                logger.exception("Character event processing failed for %s", submission_id)
            self._persist("profile_submissions", submission.submission_id, submission)
            return submission.model_copy(deep=True)

    def process_legacy_delta(
        self,
        submission_id: str,
        delta: BookBibleDelta,
    ) -> SubmissionRecord:
        """Convert current LLM delta output into chapter-aware candidates."""
        submission = self.submissions.get(submission_id)
        if not submission:
            raise KeyError("submission_not_found")
        candidates: List[CharacterEventCandidate] = []
        for character in delta.new_characters:
            candidates.append(
                CharacterEventCandidate(
                    character_original_name=character.original_name,
                    character_id=character.character_id or None,
                    category="identity",
                    attribute_key="profile",
                    operation="set",
                    value={
                        "vi_name": character.vi_name,
                        "role": character.role,
                        "voice_notes": character.voice_notes,
                        "aliases": character.aliases,
                    },
                    certainty="observed",
                    confidence=0.8,
                )
            )
        for item in delta.new_address_terms_for_existing:
            for term in item.address_terms:
                candidates.append(
                    CharacterEventCandidate(
                        character_original_name=item.character_original_name,
                        category="relationship",
                        attribute_key="address_terms",
                        operation="add",
                        value=term.model_dump(by_alias=True),
                        certainty="observed",
                        confidence=0.8,
                    )
                )
        for item in delta.address_observations:
            candidates.append(
                CharacterEventCandidate(
                    character_original_name=item.character_original_name,
                    category="relationship",
                    attribute_key="address_terms",
                    operation="add",
                    value={
                        "counterpart_original_name": item.counterpart_original_name,
                        "counterpart_text": item.counterpart_text,
                        "self_term": item.self_term,
                        "other_term": item.other_term,
                        "context": item.context,
                        "change_type": item.change_type,
                        "explicit_transition": item.explicit_transition,
                    },
                    certainty="observed" if item.explicit_transition else "inferred",
                    evidence=item.evidence,
                    confidence=item.confidence,
                )
            )
        for raw_event in getattr(delta, "character_events", []):
            if not isinstance(raw_event, dict):
                continue
            try:
                candidates.append(CharacterEventCandidate.model_validate(raw_event))
            except Exception:
                logger.warning("Skipping malformed generic character event")
        return self.process_candidates(submission_id, candidates)

    def _character_id(self, book_id: str, candidate: CharacterEventCandidate, chapter: Optional[int] = None) -> str:
        if candidate.character_id:
            return candidate.character_id
        base_id = f"char-{_hash(f"{book_id}:{_norm(candidate.character_original_name)}", 24)}"
        if chapter is None:
            return base_id
        for event in self._approved_events(book_id, chapter):
            if event.character_id != base_id or event.category != "identity" or event.operation != "link":
                continue
            if isinstance(event.value, dict):
                target_id = event.value.get("target_character_id")
                target_name = event.value.get("target_original_name")
                if target_id:
                    return str(target_id)
                if target_name:
                    return f"char-{_hash(f"{book_id}:{_norm(str(target_name))}", 24)}"
        return base_id

    def _event_key(
        self,
        book_id: str,
        character_id: str,
        chapter: int,
        candidate: CharacterEventCandidate,
    ) -> str:
        value = json.dumps(candidate.value, ensure_ascii=False, sort_keys=True, default=str)
        return _hash(
            "|".join(
                [
                    book_id,
                    character_id,
                    str(chapter),
                    _norm(candidate.category),
                    _norm(candidate.attribute_key),
                    candidate.operation,
                    value,
                ]
            ),
            40,
        )

    def _ingest_candidate(
        self,
        submission: SubmissionRecord,
        candidate: CharacterEventCandidate,
    ) -> Optional[str]:
        character_id = self._character_id(submission.book_id, candidate, submission.canonical_chapter_end)
        event_key = self._event_key(
            submission.book_id,
            character_id,
            submission.canonical_chapter_end,
            candidate,
        )
        existing_id = self._event_keys.get(event_key)
        if existing_id:
            event = self.events[existing_id]
            self._add_evidence(event_key, submission, candidate)
            self._maybe_approve(event)
            return existing_id

        event_id = f"event-{uuid.uuid4().hex}"
        event = CharacterEvent(
            event_id=event_id,
            book_id=submission.book_id,
            character_id=character_id,
            character_original_name=candidate.character_original_name,
            canonical_chapter=submission.canonical_chapter_end,
            category=candidate.category,
            attribute_key=candidate.attribute_key,
            operation=candidate.operation,
            value=deepcopy(candidate.value),
            certainty=candidate.certainty,
            status="pending",
            evidence=candidate.evidence[:1000],
            confidence=candidate.confidence,
            source_group_id=submission.source_group_id,
            source_submission_id=submission.submission_id,
        )
        self.events[event_id] = event
        self._event_keys[event_key] = event_id
        self._event_evidence_groups[event_key] = set()
        self._add_evidence(event_key, submission, candidate)
        self._maybe_approve(event)
        self._persist("profile_events", event_id, event)
        return event_id

    def _add_evidence(
        self,
        event_key: str,
        submission: SubmissionRecord,
        candidate: CharacterEventCandidate,
    ) -> None:
        evidence_id = f"evidence-{_hash(f'{event_key}:{submission.source_group_id}', 32)}"
        if evidence_id in self.evidence:
            return
        evidence = EventEvidence(
            evidence_id=evidence_id,
            event_key=event_key,
            source_group_id=submission.source_group_id,
            submission_id=submission.submission_id,
            excerpt=(candidate.evidence or "")[:1000],
            confidence=candidate.confidence,
        )
        self.evidence[evidence_id] = evidence
        self._event_evidence_groups.setdefault(event_key, set()).add(submission.source_group_id)
        self._persist("profile_evidence", evidence_id, evidence)

    def _maybe_approve(self, event: CharacterEvent) -> None:
        event_key = next((key for key, value in self._event_keys.items() if value == event.event_id), None)
        if not event_key or event.status in {"superseded", "rejected"}:
            return
        groups = self._event_evidence_groups.get(event_key, set())
        if len(groups) < self.min_independent_sources:
            event.status = "pending"
            return
        if event.confidence < 0.5:
            event.status = "pending"
            return
        # A different approved value set for the same attribute and chapter is
        # a conflict. Keep the candidate pending instead of silently replacing it.
        for other in self.events.values():
            if other.event_id == event.event_id:
                continue
            if (
                other.book_id == event.book_id
                and other.character_id == event.character_id
                and other.canonical_chapter == event.canonical_chapter
                and other.category == event.category
                and other.attribute_key == event.attribute_key
                and other.status == "approved"
                and other.value != event.value
            ):
                event.status = "pending"
                return
        was_approved = event.status == "approved"
        event.status = "approved"
        event.reviewed_at = datetime.utcnow()
        if not was_approved:
            self._book_revisions[event.book_id] = self._book_revisions.get(event.book_id, 0) + 1
            self._snapshot_cache = {
                key: value for key, value in self._snapshot_cache.items() if key[0] != event.book_id
            }
        self._persist("profile_events", event.event_id, event)

    # ------------------------------------------------------------------
    # Snapshot and timeline reads
    # ------------------------------------------------------------------
    def _approved_events(self, book_id: str, through: int) -> List[CharacterEvent]:
        return sorted(
            [
                event
                for event in self.events.values()
                if event.book_id == book_id
                and event.status == "approved"
                and event.canonical_chapter <= through
            ],
            key=lambda event: (event.canonical_chapter, event.created_at, event.event_id),
        )

    @staticmethod
    def _apply_event(states: Dict[str, CharacterSnapshot], event: CharacterEvent) -> None:
        if event.category == "identity" and event.operation == "link" and isinstance(event.value, dict):
            target_id = event.value.get("target_character_id")
            if not target_id and event.value.get("target_original_name"):
                target_id = f"char-{_hash(f"{event.book_id}:{_norm(str(event.value["target_original_name"]))}", 24)}"
            if target_id:
                source = states.get(event.character_id)
                target = states.setdefault(str(target_id), CharacterSnapshot(character_id=str(target_id), original_name=str(event.value.get("target_original_name") or event.character_original_name)))
                if source:
                    for key, value in source.attributes.items():
                        if key not in target.attributes:
                            target.attributes[key] = deepcopy(value)
                        elif isinstance(target.attributes[key], list) and isinstance(value, list):
                            target.attributes[key].extend(item for item in value if item not in target.attributes[key])
                    target.last_changed_chapter = event.canonical_chapter
                    states.pop(event.character_id, None)
                return
        state = states.get(event.character_id)
        if not state:
            state = CharacterSnapshot(
                character_id=event.character_id,
                original_name=event.character_original_name,
            )
            states[event.character_id] = state
        state.last_changed_chapter = event.canonical_chapter
        if event.operation in {"set", "correct"}:
            state.attributes[event.attribute_key] = deepcopy(event.value)
        elif event.operation == "add":
            values = state.attributes.setdefault(event.attribute_key, [])
            if not isinstance(values, list):
                values = [values]
                state.attributes[event.attribute_key] = values
            if event.value not in values:
                values.append(deepcopy(event.value))
        elif event.operation == "remove":
            values = state.attributes.get(event.attribute_key)
            if isinstance(values, list):
                state.attributes[event.attribute_key] = [item for item in values if item != event.value]
            elif values == event.value:
                state.attributes.pop(event.attribute_key, None)
        elif event.operation in {"increase", "decrease"}:
            current = state.attributes.get(event.attribute_key, 0)
            try:
                amount = float(event.value)
                next_value = current + amount if event.operation == "increase" else current - amount
                state.attributes[event.attribute_key] = int(next_value) if next_value.is_integer() else next_value
            except (TypeError, ValueError):
                state.attributes[event.attribute_key] = deepcopy(event.value)
        elif event.operation in {"link", "unlink"}:
            values = state.attributes.setdefault(event.attribute_key, [])
            if not isinstance(values, list):
                values = [values]
                state.attributes[event.attribute_key] = values
            if event.operation == "link" and event.value not in values:
                values.append(deepcopy(event.value))
            if event.operation == "unlink":
                state.attributes[event.attribute_key] = [item for item in values if item != event.value]

    def snapshot(
        self,
        edition_id: str,
        local_chapter_index: int,
    ) -> CharacterSnapshotResponse:
        with self._lock:
            edition = self.get_edition(edition_id)
            if not edition:
                raise KeyError("edition_not_found")
            mapping = self.get_mapping(edition_id, local_chapter_index)
            through = mapping.canonical_chapter_end
            revision = self._book_revisions.get(edition.book_id, 0)
            cache_key = (edition.book_id, through, revision)
            cached = self._snapshot_cache.get(cache_key)
            if cached:
                result = cached.model_copy(deep=True)
                result.edition_id = edition_id
                result.requested_chapter = local_chapter_index
                return result

            states: Dict[str, CharacterSnapshot] = {}
            for event in self._approved_events(edition.book_id, through):
                self._apply_event(states, event)
            processed = self._processed_chapters.get(edition.book_id, set())
            complete_through = max((chapter for chapter in processed if chapter <= through), default=None)
            pending = sorted(
                chapter for chapter in {
                    item.canonical_chapter_end
                    for item in self.submissions.values()
                    if item.book_id == edition.book_id and item.status in {"queued", "processing", "reviewing"}
                }
                if chapter <= through
            )
            result = CharacterSnapshotResponse(
                book_id=edition.book_id,
                edition_id=edition_id,
                requested_chapter=local_chapter_index,
                canonical_chapter=through,
                book_revision=revision,
                projection_revision=revision,
                projection_status="ready",
                snapshot_status="complete" if complete_through is not None and complete_through >= through else "partial",
                complete_through_chapter=complete_through,
                pending_chapters=pending,
                characters=list(states.values()),
            )
            self._snapshot_cache[cache_key] = result.model_copy(deep=True)
            return result

    def timeline(
        self,
        edition_id: str,
        local_chapter_index: int,
        character_id: str,
    ) -> List[CharacterEvent]:
        with self._lock:
            edition = self.get_edition(edition_id)
            if not edition:
                raise KeyError("edition_not_found")
            mapping = self.get_mapping(edition_id, local_chapter_index)
            return [
                event.model_copy(deep=True)
                for event in self._approved_events(edition.book_id, mapping.canonical_chapter_end)
                if event.character_id == character_id
            ]

    def get_submission(self, submission_id: str) -> Optional[SubmissionRecord]:
        submission = self.submissions.get(submission_id)
        return submission.model_copy(deep=True) if submission else None

    def known_names_index(self, book_id: str) -> str:
        names = set()
        for event in self.events.values():
            if event.book_id != book_id:
                continue
            names.add(event.character_original_name)
            if isinstance(event.value, dict):
                aliases = event.value.get("aliases", [])
                names.update(str(alias) for alias in aliases if alias)
        return "\n".join(f"{name} -> {name}" for name in sorted(names)) or "(empty)"

    def fail_submission(self, submission_id: str, error_code: str, message: str) -> None:
        submission = self.submissions.get(submission_id)
        if not submission:
            return
        submission.status = "failed"
        submission.error_code = error_code
        submission.error_message = message[:500]
        self._persist("profile_submissions", submission_id, submission)

    def _persist(self, collection: str, document_id: str, value: Any) -> None:
        if not self.firestore_db:
            return
        try:
            def jsonable(item):
                if hasattr(item, "model_dump"):
                    return item.model_dump(mode="json")
                if isinstance(item, dict):
                    return {key: jsonable(value) for key, value in item.items()}
                if isinstance(item, list):
                    return [jsonable(value) for value in item]
                return item
            payload = jsonable(value)
            self.firestore_db.collection(collection).document(document_id).set(payload, merge=True)
        except Exception as exc:
            logger.warning("Book profile Firestore mirror failed collection=%s: %s", collection, exc)


def candidates_from_legacy_bible(bible: BookBible) -> List[CharacterEventCandidate]:
    """Build candidates from an existing legacy bible for migration tools."""
    candidates: List[CharacterEventCandidate] = []
    for character in bible.characters:
        candidates.append(
            CharacterEventCandidate(
                character_original_name=character.original_name,
                character_id=character.character_id or None,
                category="identity",
                attribute_key="profile",
                operation="set",
                value={
                    "vi_name": character.vi_name,
                    "role": character.role,
                    "voice_notes": character.voice_notes,
                    "aliases": character.aliases,
                },
                certainty="observed",
                confidence=1.0,
            )
        )
    return candidates

