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
import os
from copy import deepcopy
from datetime import datetime
from datetime import timezone, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import settings
from app.db.session import db_session
from app.repositories.character_profile_repository import CharacterProfileRepository
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


def _slugify(text: str) -> str:
    import re
    import unicodedata
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "d")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def _clean_title(text: Optional[str]) -> str:
    """Clean book title by removing bracketed tags like [AI], [Dịch], parentheses, and punctuation."""
    import re
    if not text:
        return ""
    # Strip bracketed content like [AI], [Dịch], [Convert], (Bản dịch)...
    cleaned = re.sub(r"\[.*?\]|\(.*?\)", " ", text)
    # Replace colons, hyphens, pipes, dashes with spaces
    cleaned = re.sub(r"[:\-|–—]", " ", cleaned)
    return " ".join(cleaned.split()).strip()


def _token_similarity(s1: Optional[str], s2: Optional[str]) -> float:
    """Calculate token Jaccard similarity between two Vietnamese titles."""
    if not s1 or not s2:
        return 0.0
    slug1 = _slugify(_clean_title(s1))
    slug2 = _slugify(_clean_title(s2))
    t1 = set(slug1.split("-"))
    t2 = set(slug2.split("-"))
    # Remove common Vietnamese stop-words
    stop_words = {"o", "tai", "va", "cua", "la", "tap", "quyen", "ai", "dich", "bo", "phan", "hoi", "chuong"}
    t1 = {w for w in t1 if w and w not in stop_words}
    t2 = {w for w in t2 if w and w not in stop_words}
    if not t1 or not t2:
        return 0.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union


class CharacterProfileService:
    """In-process canonical event store with optional Firestore mirroring."""

    def __init__(
        self,
        firestore_db=None,
        storage_repo=None,
        min_independent_sources: Optional[int] = None,
        auto_approve: Optional[bool] = None,
    ):
        self.firestore_db = firestore_db
        self.storage_repo = storage_repo
        env_sources = int(os.getenv("BOOK_BIBLE_MIN_SOURCES", "2") or 2)
        self.min_independent_sources = max(1, min_independent_sources if min_independent_sources is not None else env_sources)
        env_auto = os.getenv("BOOK_BIBLE_AUTO_APPROVE", "false").lower() in {"1", "true", "yes"}
        self.auto_approve = auto_approve if auto_approve is not None else env_auto
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
        self._hydrated_from_storage: bool = False
        self._hydrate_all_from_storage()

    @staticmethod
    def book_id_for(metadata: BookMetadata) -> str:
        slug = _slugify(metadata.title)
        if slug and slug != "novel":
            return slug
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
        if self.firestore_db:
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

    def _hydrate_all_from_storage(self, force: bool = False) -> None:
        if getattr(self, "_hydrated_from_storage", False) and not force:
            return
        if os.environ.get("PYTEST_CURRENT_TEST") and not force:
            return
        self._hydrated_from_storage = True


        # 0. Hydrate from PostgreSQL if configured
        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    # Books
                    for b in CharacterProfileRepository.list_books(session):
                        if b.book_id not in self.books:
                            book_row = CharacterProfileRepository.get_book(session, b.book_id)
                            if book_row:
                                meta = BookMetadata(
                                    title=book_row["title"],
                                    author=book_row["author"],
                                    language=book_row["language"],
                                    publisher=book_row.get("publisher", ""),
                                    identifier=book_row.get("identifier"),
                                )
                                self.books[b.book_id] = {
                                    "book_id": b.book_id,
                                    "metadata": meta,
                                    "title_key": book_row["title_key"],
                                    "author_key": book_row["author_key"],
                                    "sampled_chapters": book_row.get("sampled_chapters", []),
                                    "created_at": book_row.get("created_at"),
                                }
                                self._book_revisions[b.book_id] = book_row.get("revision", 0)

                    # Editions
                    for ed in CharacterProfileRepository.list_all_editions(session):
                        if ed.edition_id not in self.editions:
                            self.editions[ed.edition_id] = ed

                    # Mappings
                    for m in CharacterProfileRepository.list_all_mappings(session):
                        self.mappings[(m.edition_id, m.local_chapter_index)] = m

                    # Submissions
                    for sub in CharacterProfileRepository.list_all_submissions(session):
                        if sub.submission_id not in self.submissions:
                            self.submissions[sub.submission_id] = sub
                            if sub.idempotency_key:
                                self.submissions_by_key[sub.idempotency_key] = sub.submission_id

                    # Events
                    for ev in CharacterProfileRepository.list_all_events(session):
                        if ev.event_id not in self.events:
                            self.events[ev.event_id] = ev
                            candidate = CharacterEventCandidate(
                                character_original_name=ev.character_original_name,
                                character_id=ev.character_id,
                                category=ev.category,
                                attribute_key=ev.attribute_key,
                                operation=ev.operation,
                                value=ev.value,
                                certainty=ev.certainty,
                                evidence=ev.evidence,
                                confidence=ev.confidence,
                            )
                            key = self._event_key(ev.book_id, ev.character_id, ev.canonical_chapter, candidate)
                            self._event_keys[key] = ev.event_id
                            self._event_evidence_groups.setdefault(key, set()).add(ev.source_group_id)
                            self._book_revisions[ev.book_id] = max(self._book_revisions.get(ev.book_id, 0), 1 if ev.status == "approved" else 0)

                    # Evidence
                    for evi in CharacterProfileRepository.list_all_evidence(session):
                        if evi.evidence_id not in self.evidence:
                            self.evidence[evi.evidence_id] = evi
                            ev_key = evi.event_key
                            if ev_key.startswith("ev-") and ev_key[3:] in self.events:
                                mapped_key = next((k for k, v in self._event_keys.items() if v == ev_key[3:]), None)
                                if mapped_key:
                                    ev_key = mapped_key
                            self._event_evidence_groups.setdefault(ev_key, set()).add(evi.source_group_id)


                    # Settings
                    st = CharacterProfileRepository.get_settings(session)
                    self.auto_approve = st.auto_approve
                    self.min_independent_sources = st.min_independent_sources

            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    logger.error("Hydration from PostgreSQL failed in postgres mode: %s", exc)
                    raise exc
                logger.warning("Hydration from PostgreSQL failed: %s", exc)
            return

        # 1. Hydrate from Cloudflare R2
        if self.storage_repo and getattr(self.storage_repo, "is_r2_active", False):
            try:
                all_r2_items = (
                    self.storage_repo._r2_list_json_objects("novels/")
                    + self.storage_repo._r2_list_json_objects("data/profile_books/")
                    + self.storage_repo._r2_list_json_objects("data/profile_editions/")
                    + self.storage_repo._r2_list_json_objects("data/profile_events/")
                    + self.storage_repo._r2_list_json_objects("data/profile_submissions/")
                )
                for item in all_r2_items:
                    if not isinstance(item, dict):
                        continue
                    # Books
                    if "book_id" in item and "metadata" in item and "sampled_chapters" in item:
                        book_id = item.get("book_id")
                        if book_id and book_id not in self.books:
                            meta = BookMetadata.model_validate(item["metadata"]) if isinstance(item.get("metadata"), dict) else BookMetadata(title=str(item.get("metadata", book_id)))
                            self.books[book_id] = {
                                "book_id": book_id,
                                "metadata": meta,
                                "title_key": _norm(meta.title),
                                "author_key": _norm(meta.author),
                                "sampled_chapters": item.get("sampled_chapters", []),
                                "created_at": item.get("created_at"),
                            }
                            self._book_revisions.setdefault(book_id, 0)
                    # Editions
                    elif "edition_id" in item and "chapter_count" in item:
                        ed_id = item.get("edition_id")
                        if ed_id and ed_id not in self.editions:
                            self.editions[ed_id] = EditionRecord.model_validate(item)
                    # Events
                    elif "event_id" in item and "category" in item and "character_id" in item:
                        ev_id = item.get("event_id")
                        if ev_id and ev_id not in self.events:
                            event = CharacterEvent.model_validate(item)
                            self.events[ev_id] = event
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
                            key = self._event_key(event.book_id, event.character_id, event.canonical_chapter, candidate)
                            self._event_keys[key] = ev_id
                            self._event_evidence_groups.setdefault(key, set()).add(event.source_group_id)
                            self._book_revisions[event.book_id] = max(self._book_revisions.get(event.book_id, 0), 1 if event.status == "approved" else 0)
                    # Submissions
                    elif "submission_id" in item and "input_text_fingerprint" in item:
                        sub_id = item.get("submission_id")
                        if sub_id and sub_id not in self.submissions:
                            sub = SubmissionRecord.model_validate(item)
                            self.submissions[sub_id] = sub
                            if sub.idempotency_key:
                                self.submissions_by_key[sub.idempotency_key] = sub_id
            except Exception as exc:
                logger.warning("R2 global hydration failed: %s", exc)

        # 2. Hydrate from Local Disk (scanning storage/novels/ and legacy data/)
        try:
            root_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            local_json_files = []

            storage_novels_dir = os.path.join(root_dir, "storage", "novels")
            if os.path.exists(storage_novels_dir):
                for root, _, files in os.walk(storage_novels_dir):
                    if "profile" in root:
                        for fn in files:
                            if fn.endswith(".json"):
                                local_json_files.append(os.path.join(root, fn))

            legacy_data_dir = os.path.join(root_dir, "data")
            if os.path.exists(legacy_data_dir):
                for root, _, files in os.walk(legacy_data_dir):
                    for fn in files:
                        if fn.endswith(".json"):
                            local_json_files.append(os.path.join(root, fn))

            for file_path in local_json_files:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        item = json.load(f)
                    if not isinstance(item, dict):
                        continue
                    if "book_id" in item and "metadata" in item and "sampled_chapters" in item:
                        book_id = item.get("book_id")
                        if book_id and book_id not in self.books:
                            meta = BookMetadata.model_validate(item["metadata"]) if isinstance(item.get("metadata"), dict) else BookMetadata(title=str(item.get("metadata", book_id)))
                            self.books[book_id] = {
                                "book_id": book_id,
                                "metadata": meta,
                                "title_key": _norm(meta.title),
                                "author_key": _norm(meta.author),
                                "sampled_chapters": item.get("sampled_chapters", []),
                                "created_at": item.get("created_at"),
                            }
                            self._book_revisions.setdefault(book_id, 0)
                    elif "edition_id" in item and "chapter_count" in item:
                        ed_id = item.get("edition_id")
                        if ed_id and ed_id not in self.editions:
                            self.editions[ed_id] = EditionRecord.model_validate(item)
                    elif "event_id" in item and "category" in item and "character_id" in item:
                        ev_id = item.get("event_id")
                        if ev_id and ev_id not in self.events:
                            event = CharacterEvent.model_validate(item)
                            self.events[ev_id] = event
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
                            key = self._event_key(event.book_id, event.character_id, event.canonical_chapter, candidate)
                            self._event_keys[key] = ev_id
                            self._event_evidence_groups.setdefault(key, set()).add(event.source_group_id)
                            self._book_revisions[event.book_id] = max(self._book_revisions.get(event.book_id, 0), 1 if event.status == "approved" else 0)
                    elif "submission_id" in item and "input_text_fingerprint" in item:
                        sub_id = item.get("submission_id")
                        if sub_id and sub_id not in self.submissions:
                            sub = SubmissionRecord.model_validate(item)
                            self.submissions[sub_id] = sub
                            if sub.idempotency_key:
                                self.submissions_by_key[sub.idempotency_key] = sub_id
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Local disk global hydration skipped: %s", exc)


    def get_edition(
        self,
        edition_id: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Optional[EditionRecord]:
        edition = self.editions.get(edition_id)
        if edition:
            # If a real title is provided and current book has placeholder title, update it
            if title and edition.book_id in self.books:
                curr_title = self.books[edition.book_id]["metadata"].title
                if curr_title.startswith("Book (edition-") or not curr_title:
                    self.books[edition.book_id]["metadata"].title = title
                    if author:
                        self.books[edition.book_id]["metadata"].author = author
                    self.books[edition.book_id]["title_key"] = _norm(title)
                    self._persist("profile_books", edition.book_id, self.books[edition.book_id])
                    edition.metadata.title = title
                    if author:
                        edition.metadata.author = author
                    self._persist("profile_editions", edition_id, edition)
            return edition.model_copy(deep=True)

        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_ed = CharacterProfileRepository.get_edition(session, edition_id)
                    if db_ed:
                        self.editions[edition_id] = db_ed
                        return db_ed.model_copy(deep=True)
            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    raise exc
                logger.warning("Failed to get edition from DB: %s", exc)

        if self.firestore_db and settings.structured_storage_backend in ("legacy", "dual"):
            try:
                snapshot = self.firestore_db.collection("profile_editions").document(edition_id).get()
                if snapshot.exists:
                    edition = EditionRecord.model_validate(snapshot.to_dict())
                    self.editions[edition_id] = edition
                    self._load_book_from_firestore(edition.book_id)
                    return edition.model_copy(deep=True)
            except Exception as exc:
                logger.warning("Book profile edition hydration failed edition=%s: %s", edition_id, exc)

        # Fallback 1: If edition_id is directly a book_id
        if edition_id in self.books:
            for ed in self.editions.values():
                if ed.book_id == edition_id:
                    return ed.model_copy(deep=True)
            meta = self.books[edition_id]["metadata"]
            new_ed = EditionRecord(
                edition_id=f"edition-{_hash(f'{edition_id}:default', 24)}",
                book_id=edition_id,
                metadata=meta.model_copy(deep=True) if hasattr(meta, "model_copy") else BookMetadata(title=str(meta)),
                fingerprints=FingerprintBundle(),
                chapter_count=1000,
            )
            self.editions[new_ed.edition_id] = new_ed
            self._persist("profile_editions", new_ed.edition_id, new_ed)
            return new_ed.model_copy(deep=True)

        # Fallback 2: If title is provided, search existing books for a title match
        if title:
            norm_title = _norm(title)
            clean_title_norm = _norm(_clean_title(title))
            matched_book_id = None
            best_match_score = 0.0
            for b_id, b_data in self.books.items():
                b_meta = b_data.get("metadata")
                b_title = b_meta.title if hasattr(b_meta, "title") else str(b_meta or "")
                b_title_key = b_data.get("title_key", "")
                b_clean_norm = _norm(_clean_title(b_title))

                if norm_title == b_title_key or (clean_title_norm and clean_title_norm == b_clean_norm):
                    matched_book_id = b_id
                    break
                if (norm_title and norm_title in b_title_key) or (b_title_key and b_title_key in norm_title):
                    matched_book_id = b_id
                    break
                if (clean_title_norm and clean_title_norm in b_clean_norm) or (b_clean_norm and b_clean_norm in clean_title_norm):
                    matched_book_id = b_id
                    break
                tok_sim = _token_similarity(title, b_title)
                if author and _norm(author) == b_data.get("author_key", "") and tok_sim >= 0.60:
                    if tok_sim > best_match_score:
                        best_match_score = tok_sim
                        matched_book_id = b_id

            if matched_book_id:
                new_ed = EditionRecord(
                    edition_id=edition_id,
                    book_id=matched_book_id,
                    metadata=self.books[matched_book_id]["metadata"].model_copy(deep=True),
                    fingerprints=FingerprintBundle(),
                    chapter_count=2000,
                )
                self.editions[edition_id] = new_ed
                self._persist("profile_editions", edition_id, new_ed)
                return new_ed.model_copy(deep=True)
            else:
                # Create a new book with real metadata sent by client
                new_meta = BookMetadata(title=title, author=author or "", language="vi")
                slug_candidate = _slugify(title)
                new_book_id = slug_candidate if (slug_candidate and slug_candidate != "novel") else self.book_id_for(new_meta)
                self._create_book(new_book_id, new_meta, FingerprintBundle())
                new_ed = EditionRecord(
                    edition_id=edition_id,
                    book_id=new_book_id,
                    metadata=new_meta.model_copy(deep=True),
                    fingerprints=FingerprintBundle(),
                    chapter_count=2000,
                )
                self.editions[edition_id] = new_ed
                self._persist("profile_editions", edition_id, new_ed)
                return new_ed.model_copy(deep=True)

        # Fallback 3: Try detecting novel from content if provided
        if content and len(content) > 50:
            content_lower = content.casefold()
            best_match_id = None
            max_hits = 0
            for b_id, b_data in self.books.items():
                hits = 0
                title_key = b_data.get("title_key", "")
                if title_key and title_key in content_lower:
                    hits += 3
                # check existing characters in this book
                for ev in self.events.values():
                    if ev.book_id == b_id and ev.character_original_name and ev.character_original_name.casefold() in content_lower:
                        hits += 2
                if hits > max_hits and hits >= 2:
                    max_hits = hits
                    best_match_id = b_id

            if best_match_id:
                new_ed = EditionRecord(
                    edition_id=edition_id,
                    book_id=best_match_id,
                    metadata=self.books[best_match_id]["metadata"].model_copy(deep=True),
                    fingerprints=FingerprintBundle(),
                    chapter_count=2000,
                )
                self.editions[edition_id] = new_ed
                self._persist("profile_editions", edition_id, new_ed)
                return new_ed.model_copy(deep=True)

        # Fallback 4: Auto-create an ad-hoc edition
        if edition_id.startswith("edition-") or len(edition_id) >= 10:
            adhoc_book_id = f"book-{_hash(edition_id, 24)}"
            if adhoc_book_id not in self.books:
                self._create_book(
                    adhoc_book_id,
                    BookMetadata(title=title or f"Book ({edition_id[:16]})", author=author or "", language="vi"),
                    FingerprintBundle(),
                )
            new_ed = EditionRecord(
                edition_id=edition_id,
                book_id=adhoc_book_id,
                metadata=BookMetadata(title=title or f"Edition ({edition_id[:16]})", author=author or "", language="vi"),
                fingerprints=FingerprintBundle(),
                chapter_count=2000,
            )
            self.editions[edition_id] = new_ed
            self._persist("profile_editions", edition_id, new_ed)
            return new_ed.model_copy(deep=True)

        return None

    def resolve_book(self, request: BookResolutionRequest) -> BookResolutionResponse:
        with self._lock:
            if self.firestore_db and not self.books and settings.structured_storage_backend in ("legacy", "dual"):
                for doc in self.firestore_db.collection("profile_books").stream():
                    self._load_book_from_firestore(doc.id)
            if request.book_id:
                if request.book_id not in self.books:
                    self._create_book(request.book_id, request.metadata, request.fingerprints)
                return BookResolutionResponse(status="matched", book_id=request.book_id)

            req_title = request.metadata.title or ""
            req_author = request.metadata.author or ""
            title_key = _norm(req_title)
            author_key = _norm(req_author)
            clean_req_title_norm = _norm(_clean_title(req_title))

            candidates: List[BookMatchCandidate] = []
            for book_id, book in self.books.items():
                score = 0.0
                reasons: List[str] = []
                b_meta = book.get("metadata")
                b_title = b_meta.title if hasattr(b_meta, "title") else str(b_meta or "")
                b_title_key = book.get("title_key", "")
                b_author_key = book.get("author_key", "")
                b_clean_title_norm = _norm(_clean_title(b_title))

                # Title match scoring
                if title_key and title_key == b_title_key:
                    score += 0.55
                    reasons.append("normalized_title")
                elif clean_req_title_norm and clean_req_title_norm == b_clean_title_norm:
                    score += 0.50
                    reasons.append("cleaned_title_exact")
                elif title_key and (title_key in b_title_key or b_title_key in title_key):
                    score += 0.30
                    reasons.append("title_contains")
                elif clean_req_title_norm and (clean_req_title_norm in b_clean_title_norm or b_clean_title_norm in clean_req_title_norm):
                    score += 0.30
                    reasons.append("cleaned_title_contains")
                else:
                    tok_sim = _token_similarity(req_title, b_title)
                    if tok_sim >= 0.60:
                        score += 0.30 + round(0.25 * tok_sim, 4)
                        reasons.append(f"token_similarity_{round(tok_sim, 2)}")

                # Author match scoring
                if author_key and author_key == b_author_key:
                    score += 0.30
                    reasons.append("normalized_author")
                elif author_key and (author_key in b_author_key or b_author_key in author_key):
                    score += 0.15
                    reasons.append("author_partial")

                incoming_samples = set(request.fingerprints.sampled_chapters)
                if incoming_samples & set(book.get("sampled_chapters", [])):
                    score += 0.15
                    reasons.append("sample_fingerprint")

                if score:
                    candidates.append(
                        BookMatchCandidate(
                            book_id=book_id,
                            title=book["metadata"].title if hasattr(book["metadata"], "title") else str(book["metadata"]),
                            author=book["metadata"].author if hasattr(book["metadata"], "author") else "",
                            score=round(min(score, 1.0), 4),
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

    def update_book(self, book_id: str, request: BookUpdateRequest) -> Dict[str, Any]:
        with self._lock:
            self._load_book_from_firestore(book_id)
            if book_id not in self.books:
                raise KeyError("book_not_found")
            book = self.books[book_id]
            if request.title:
                book["metadata"].title = request.title.strip()
                book["title_key"] = _norm(request.title)
            if request.author is not None:
                book["metadata"].author = request.author.strip()
                book["author_key"] = _norm(request.author)
            self._persist("profile_books", book_id, book)
            for ed in self.editions.values():
                if ed.book_id == book_id:
                    if request.title:
                        ed.metadata.title = request.title.strip()
                    if request.author is not None:
                        ed.metadata.author = request.author.strip()
                    self._persist("profile_editions", ed.edition_id, ed)
            return {
                "book_id": book_id,
                "title": book["metadata"].title,
                "author": book["metadata"].author,
                "language": book["metadata"].language,
            }

    def _create_book(self, book_id: str, metadata: BookMetadata, fingerprints: FingerprintBundle) -> None:
        if book_id in self.books:
            return
        self.books[book_id] = {
            "book_id": book_id,
            "metadata": metadata.model_copy(deep=True),
            "title_key": _norm(metadata.title),
            "author_key": _norm(metadata.author),
            "sampled_chapters": list(fingerprints.sampled_chapters),
            "created_at": datetime.now(timezone.utc),
        }
        self._book_revisions.setdefault(book_id, 0)
        self._persist("profile_books", book_id, self.books[book_id])

        # Auto-create default edition
        default_edition_id = f"edition-{_hash(f'{book_id}:default', 24)}"
        if default_edition_id not in self.editions:
            edition = EditionRecord(
                edition_id=default_edition_id,
                book_id=book_id,
                metadata=metadata.model_copy(deep=True),
                fingerprints=fingerprints.model_copy(deep=True),
                chapter_count=1000,
            )
            self.editions[default_edition_id] = edition
            self._persist("profile_editions", default_edition_id, edition)

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
        if self.firestore_db and settings.structured_storage_backend in ("legacy", "dual"):
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
                submission.completed_at = datetime.now(timezone.utc)
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
        # 1. Save parent event first so foreign key is satisfied in DB
        self._persist("profile_events", event_id, event, event_key=event_key)
        # 2. Save child evidence
        self._add_evidence(event_key, submission, candidate, event_id=event_id)
        # 3. Check auto approve
        old_status = event.status
        self._maybe_approve(event)
        if event.status != old_status:
            self._persist("profile_events", event_id, event, event_key=event_key)
        return event_id

    def _add_evidence(
        self,
        event_key: str,
        submission: SubmissionRecord,
        candidate: CharacterEventCandidate,
        event_id: str = "",
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
        target_event_id = event_id or self._event_keys.get(event_key, "")
        self._persist("profile_evidence", evidence_id, evidence, event_id=target_event_id)

    def _maybe_approve(self, event: CharacterEvent) -> None:
        event_key = next((key for key, value in self._event_keys.items() if value == event.event_id), None)
        if not event_key or event.status in {"superseded", "rejected"}:
            return
        groups = self._event_evidence_groups.get(event_key, set())
        if not self.auto_approve and len(groups) < self.min_independent_sources:
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
        event.reviewed_at = datetime.now(timezone.utc)
        if not was_approved:
            self._book_revisions[event.book_id] = self._book_revisions.get(event.book_id, 0) + 1
            self._snapshot_cache = {
                key: value for key, value in self._snapshot_cache.items() if key[0] != event.book_id
            }
        self._persist("profile_events", event.event_id, event)

    def delete_book(self, book_id: str) -> bool:
        """Xóa hoàn toàn một bộ truyện và toàn bộ ấn bản, sự kiện, submission liên quan."""
        with self._lock:
            if settings.structured_storage_backend in ("dual", "postgres"):
                try:
                    with db_session() as session:
                        CharacterProfileRepository.delete_book(session, book_id)
                        session.commit()
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        logger.error("Failed to delete book %s from DB in postgres mode: %s", book_id, exc)
                        raise exc
                    logger.warning("Failed to delete book %s from DB in dual mode: %s", book_id, exc)

            self.books.pop(book_id, None)
            self._book_revisions.pop(book_id, None)

            ed_ids_to_del = [ed_id for ed_id, ed in self.editions.items() if ed.book_id == book_id]
            for ed_id in ed_ids_to_del:
                self.editions.pop(ed_id, None)

            ev_ids_to_del = [ev_id for ev_id, ev in self.events.items() if ev.book_id == book_id]
            for ev_id in ev_ids_to_del:
                self.events.pop(ev_id, None)

            sub_ids_to_del = [sub_id for sub_id, sub in self.submissions.items() if sub.book_id == book_id]
            for sub_id in sub_ids_to_del:
                self.submissions.pop(sub_id, None)

            if self.firestore_db and settings.structured_storage_backend in ("legacy", "dual"):
                try:
                    self.firestore_db.collection("profile_books").document(book_id).delete()
                    for ed_id in ed_ids_to_del:
                        self.firestore_db.collection("profile_editions").document(ed_id).delete()
                    for ev_id in ev_ids_to_del:
                        self.firestore_db.collection("profile_events").document(ev_id).delete()
                    for sub_id in sub_ids_to_del:
                        self.firestore_db.collection("profile_submissions").document(sub_id).delete()
                except Exception as exc:
                    logger.warning("Failed to delete book from Firestore: %s", exc)

            if self.storage_repo and getattr(self.storage_repo, "is_r2_active", False) and settings.structured_storage_backend in ("legacy", "dual"):
                try:
                    client = self.storage_repo.r2_client
                    bucket = settings.cloudflare_r2_bucket_name
                    paginator = client.get_paginator("list_objects_v2")
                    for prefix in [f"novels/{book_id}/profile/", f"data/profile_books/{book_id}.json"]:
                        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                            to_del = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                            if to_del:
                                client.delete_objects(Bucket=bucket, Delete={"Objects": to_del})
                except Exception as exc:
                    logger.warning("Failed to delete book from R2: %s", exc)

            if settings.structured_storage_backend in ("legacy", "dual"):
                try:
                    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                    local_dir = os.path.join(root_dir, "storage", "novels", book_id, "profile")
                    if os.path.exists(local_dir):
                        import shutil
                        shutil.rmtree(local_dir, ignore_errors=True)
                except Exception:
                    pass

            return True

    def merge_books(self, source_book_id: str, target_book_id: str) -> bool:
        """Gộp toàn bộ ấn bản, sự kiện, submission từ source_book_id sang target_book_id và xóa source_book_id."""
        with self._lock:
            if source_book_id == target_book_id:
                return True

            db_success = False
            if settings.structured_storage_backend in ("dual", "postgres"):
                try:
                    with db_session() as session:
                        db_success = CharacterProfileRepository.merge_books(session, source_book_id, target_book_id)
                        session.commit()
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        logger.error("Failed to merge books %s -> %s in DB: %s", source_book_id, target_book_id, exc)
                        raise exc
                    logger.warning("Failed to merge books %s -> %s in DB (dual mode): %s", source_book_id, target_book_id, exc)

            if not db_success and target_book_id not in self.books and source_book_id not in self.books:
                return False

            # In-memory updates:
            for ed in self.editions.values():
                if ed.book_id == source_book_id:
                    ed.book_id = target_book_id
                    self._persist("profile_editions", ed.edition_id, ed)

            for sub in self.submissions.values():
                if sub.book_id == source_book_id:
                    sub.book_id = target_book_id
                    self._persist("profile_submissions", sub.submission_id, sub)

            for ev in self.events.values():
                if ev.book_id == source_book_id:
                    ev.book_id = target_book_id
                    self._persist("profile_events", ev.event_id, ev)

            self.books.pop(source_book_id, None)
            self._book_revisions.pop(source_book_id, None)

            self._snapshot_cache = {
                k: v for k, v in self._snapshot_cache.items()
                if k[0] != source_book_id and k[0] != target_book_id
            }
            if target_book_id in self.books:
                self._book_revisions[target_book_id] = self._book_revisions.get(target_book_id, 0) + 1

            if self.firestore_db and settings.structured_storage_backend in ("legacy", "dual"):
                try:
                    self.firestore_db.collection("profile_books").document(source_book_id).delete()
                except Exception as exc:
                    logger.warning("Failed to delete source book from Firestore: %s", exc)

            return True

    def list_books(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._hydrate_all_from_storage()
            if self.firestore_db and not self.books and settings.structured_storage_backend in ("legacy", "dual"):
                for doc in self.firestore_db.collection("profile_books").stream():
                    self._load_book_from_firestore(doc.id)

            # Auto-sync existing novels from library_service into profile_books
            try:
                from app.services.library_service import library_service
                active_novels = library_service.list_novels()
                for nov in active_novels:
                    title = nov.title or nov.novel_id
                    bid = self.book_id_for(BookMetadata(title=title, author=nov.author or "", language="vi"))
                    if bid not in self.books:
                        self._create_book(
                            bid,
                            BookMetadata(title=title, author=nov.author or "", language="vi"),
                            FingerprintBundle(),
                        )
            except Exception as exc:
                logger.debug("Auto-sync library novels to profile_books skipped: %s", exc)

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

            # Group & Deduplicate by normalized slug to avoid "Dị Năng Giáo Sư" vs "di-nang-giao-su"
            import unicodedata, re

            def _get_slug(text: str) -> str:
                s = unicodedata.normalize('NFKD', text or '')
                s = ''.join(c for c in s if not unicodedata.combining(c))
                s = re.sub(r'[^a-zA-Z0-9]+', ' ', s).strip().lower()
                return re.sub(r'\s+', '-', s)

            slug_groups: Dict[str, List[Dict[str, Any]]] = {}

            for book_id, book in self.books.items():
                meta = book.get("metadata")
                title = meta.title if hasattr(meta, "title") else (meta.get("title", "") if isinstance(meta, dict) else "")
                author = meta.author if hasattr(meta, "author") else (meta.get("author", "") if isinstance(meta, dict) else "")
                language = meta.language if hasattr(meta, "language") else (meta.get("language", "") if isinstance(meta, dict) else "")
                
                # Bỏ qua các sách test / rác cũ
                title_lower = (title or book_id).lower()
                book_id_lower = book_id.lower()
                if any(pat in title_lower or pat in book_id_lower for pat in test_patterns):
                    continue

                item_slug = _get_slug(title or book_id)
                if not item_slug:
                    continue

                item_data = {
                    "book_id": book_id,
                    "title": title or book_id,
                    "author": author,
                    "language": language,
                    "revision": self._book_revisions.get(book_id, 0),
                    "edition_count": sum(1 for e in self.editions.values() if e.book_id == book_id),
                    "event_count": sum(1 for e in self.events.values() if e.book_id == book_id),
                    "pending_event_count": sum(1 for e in self.events.values() if e.book_id == book_id and e.status == "pending"),
                }
                slug_groups.setdefault(item_slug, []).append(item_data)

            res = []
            for slug, group in slug_groups.items():
                if len(group) == 1:
                    res.append(group[0])
                else:
                    def score_book(b):
                        score = 0
                        if b["event_count"] > 0:
                            score += 100 * b["event_count"]
                        if any(c.isupper() for c in b["title"]):
                            score += 10
                        if " " in b["title"]:
                            score += 10
                        if b["title"] != slug:
                            score += 5
                        return score

                    best_book = max(group, key=score_book)
                    best_book["event_count"] = sum(b["event_count"] for b in group)
                    best_book["pending_event_count"] = sum(b["pending_event_count"] for b in group)
                    best_book["edition_count"] = sum(b["edition_count"] for b in group)
                    best_book["revision"] = max(b["revision"] for b in group)
                    res.append(best_book)

            return sorted(res, key=lambda b: b["title"])



    def list_events(
        self,
        book_id: Optional[str] = None,
        status: Optional[str] = None,
        canonical_chapter: Optional[int] = None,
    ) -> List[CharacterEvent]:
        with self._lock:
            if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
                try:
                    with db_session() as session:
                        if book_id:
                            db_events = CharacterProfileRepository.list_events(
                                session,
                                book_id=book_id,
                                status=status,
                                max_canonical_chapter=canonical_chapter,
                            )
                        else:
                            # No book_id filter — query all books
                            db_events = CharacterProfileRepository.list_all_events(
                                session,
                                status=status,
                                max_canonical_chapter=canonical_chapter,
                            )
                        return sorted(
                            db_events,
                            key=lambda e: (e.canonical_chapter, e.created_at),
                            reverse=True,
                        )
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        raise exc
                    logger.warning("Failed to list events from DB: %s", exc)

            self._hydrate_all_from_storage()
            if self.firestore_db and book_id and settings.structured_storage_backend in ("legacy", "dual"):
                self._load_book_from_firestore(book_id)
            results = []
            for event in self.events.values():
                if book_id and event.book_id != book_id:
                    continue
                if status and event.status != status:
                    continue
                if canonical_chapter is not None and event.canonical_chapter != canonical_chapter:
                    continue
                results.append(event.model_copy(deep=True))
            return sorted(results, key=lambda e: (e.canonical_chapter, e.created_at), reverse=True)

    def approve_event(
        self,
        event_id: str,
        evidence: Optional[str] = None,
        value: Optional[Any] = None,
    ) -> CharacterEvent:
        with self._lock:
            event = self.events.get(event_id)
            if not event:
                raise KeyError("event_not_found")
            if evidence is not None:
                event.evidence = evidence[:1000]
                self._add_manual_evidence(event, evidence)
            if value is not None:
                event.value = deepcopy(value)
            was_approved = event.status == "approved"
            event.status = "approved"
            event.reviewed_at = datetime.now(timezone.utc)
            if not was_approved or value is not None:
                self._book_revisions[event.book_id] = self._book_revisions.get(event.book_id, 0) + 1
                self._snapshot_cache = {
                    key: value for key, value in self._snapshot_cache.items() if key[0] != event.book_id
                }
            self._persist("profile_events", event.event_id, event)
            return event.model_copy(deep=True)

    def update_event(
        self,
        event_id: str,
        evidence: Optional[str] = None,
        value: Optional[Any] = None,
        confidence: Optional[float] = None,
    ) -> CharacterEvent:
        with self._lock:
            event = self.events.get(event_id)
            if not event:
                raise KeyError("event_not_found")
            if evidence is not None:
                event.evidence = evidence[:1000]
                self._add_manual_evidence(event, evidence)
            if value is not None:
                event.value = deepcopy(value)
            if confidence is not None:
                event.confidence = max(0.0, min(1.0, confidence))
            if event.status == "approved" and value is not None:
                self._book_revisions[event.book_id] = self._book_revisions.get(event.book_id, 0) + 1
                self._snapshot_cache = {
                    key: value for key, value in self._snapshot_cache.items() if key[0] != event.book_id
                }
            self._persist("profile_events", event.event_id, event)
            return event.model_copy(deep=True)

    def _add_manual_evidence(self, event: CharacterEvent, evidence_text: str) -> None:
        evidence_id = f"evidence-manual-{uuid.uuid4().hex[:16]}"
        ev_key = next((k for k, v in self._event_keys.items() if v == event.event_id), None) or f"ev-{event.event_id}"
        evidence = EventEvidence(
            evidence_id=evidence_id,
            event_key=ev_key,
            source_group_id="manual-review",
            submission_id=event.source_submission_id or "manual",
            excerpt=evidence_text[:1000],
            confidence=1.0,
            created_at=datetime.now(timezone.utc),
        )
        self.evidence[evidence_id] = evidence
        self._event_evidence_groups.setdefault(ev_key, set()).add("manual-review")
        self._persist("profile_evidence", evidence_id, evidence, event_key=ev_key, event_id=event.event_id)


    def reject_event(self, event_id: str) -> CharacterEvent:
        with self._lock:
            event = self.events.get(event_id)
            if not event:
                raise KeyError("event_not_found")
            was_approved = event.status == "approved"
            event.status = "rejected"
            event.reviewed_at = datetime.now(timezone.utc)
            if was_approved:
                self._book_revisions[event.book_id] = self._book_revisions.get(event.book_id, 0) + 1
                self._snapshot_cache = {
                    key: value for key, value in self._snapshot_cache.items() if key[0] != event.book_id
                }
            self._persist("profile_events", event.event_id, event)
            return event.model_copy(deep=True)

    def approve_all_pending(
        self, book_id: Optional[str] = None, canonical_chapter: Optional[int] = None
    ) -> List[CharacterEvent]:
        with self._lock:
            approved = []
            for event in self.events.values():
                if event.status != "pending":
                    continue
                if book_id and event.book_id != book_id:
                    continue
                if canonical_chapter is not None and event.canonical_chapter != canonical_chapter:
                    continue
                event.status = "approved"
                event.reviewed_at = datetime.now(timezone.utc)
                self._book_revisions[event.book_id] = self._book_revisions.get(event.book_id, 0) + 1
                self._persist("profile_events", event.event_id, event)
                approved.append(event.model_copy(deep=True))
            if approved:
                affected_books = {e.book_id for e in approved}
                self._snapshot_cache = {
                    key: value for key, value in self._snapshot_cache.items() if key[0] not in affected_books
                }
            return approved

    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
                try:
                    with db_session() as session:
                        st = CharacterProfileRepository.get_settings(session)
                        self.auto_approve = st.auto_approve
                        self.min_independent_sources = st.min_independent_sources
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        raise exc
            return {
                "auto_approve": self.auto_approve,
                "min_independent_sources": self.min_independent_sources,
            }

    def update_settings(
        self, auto_approve: Optional[bool] = None, min_sources: Optional[int] = None
    ) -> Dict[str, Any]:
        with self._lock:
            if auto_approve is not None:
                self.auto_approve = auto_approve
            if min_sources is not None:
                self.min_independent_sources = max(1, min_sources)

            if settings.structured_storage_backend in ("dual", "postgres"):
                try:
                    with db_session() as session:
                        CharacterProfileRepository.update_settings(
                            session,
                            auto_approve=self.auto_approve,
                            min_independent_sources=self.min_independent_sources,
                        )
                        session.commit()
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        raise exc
                    logger.warning("Failed to update settings in DB: %s", exc)

            return self.get_settings()

    # ------------------------------------------------------------------
    # Snapshot and timeline reads
    # ------------------------------------------------------------------
    def _approved_events(self, book_id: str, through: int) -> List[CharacterEvent]:
        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_events = CharacterProfileRepository.list_events(
                        session,
                        book_id=book_id,
                        status="approved",
                        max_canonical_chapter=through,
                    )
                    return sorted(
                        db_events,
                        key=lambda event: (event.canonical_chapter, event.created_at, event.event_id),
                    )
            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    raise exc
                logger.warning("Failed to get approved events from DB: %s", exc)

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

    def _resolve_novel_folder(self, book_id_or_folder: Optional[str], payload: Optional[dict] = None) -> str:
        if not book_id_or_folder:
            return "global"
        
        # 1. If already a clean slug not starting with 'book-'
        if not book_id_or_folder.startswith("book-"):
            return book_id_or_folder

        # 2. Check title from memory books
        title = ""
        book_data = self.books.get(book_id_or_folder)
        if book_data:
            meta = book_data.get("metadata")
            if hasattr(meta, "title"):
                title = meta.title
            elif isinstance(meta, dict):
                title = meta.get("title", "")

        if not title and payload:
            title = payload.get("metadata", {}).get("title") or payload.get("title", "")

        if title:
            slug = _slugify(title)
            if slug and slug != "novel":
                return slug

        # 3. Check storage_repo novels
        if self.storage_repo and book_data and book_data.get("title_key"):
            try:
                novels = self.storage_repo.list_novels()
                for n in novels:
                    if _norm(n.title) == book_data.get("title_key"):
                        return n.id
            except Exception:
                pass

        return book_id_or_folder

    def _persist(self, collection: str, document_id: str, value: Any, event_key: str = "", event_id: str = "") -> None:
        try:
            def jsonable(item):
                if hasattr(item, "model_dump"):
                    return item.model_dump(mode="json")
                if isinstance(item, datetime):
                    return item.isoformat()
                if isinstance(item, dict):
                    return {key: jsonable(v) for key, v in item.items()}
                if isinstance(item, (list, set, tuple)):
                    return [jsonable(v) for v in item]
                return item

            payload = jsonable(value)

            # Determine novel_id / book_id to store in per-novel folder
            novel_folder = None
            if collection in ("profile_books", "books"):
                novel_folder = document_id
            elif isinstance(value, dict) and value.get("book_id"):
                novel_folder = value["book_id"]
            elif hasattr(value, "book_id") and getattr(value, "book_id", None):
                novel_folder = getattr(value, "book_id")
            elif isinstance(payload, dict) and payload.get("book_id"):
                novel_folder = payload["book_id"]

            novel_folder = self._resolve_novel_folder(novel_folder, payload)

            # 0. Sync to PostgreSQL Database if backend is dual or postgres
            if settings.structured_storage_backend in ("dual", "postgres"):
                try:
                    with db_session() as session:
                        if collection in ("profile_books", "books"):
                            book_dict = {
                                "book_id": document_id,
                                "title": payload.get("metadata", {}).get("title") or payload.get("title", ""),
                                "author": payload.get("metadata", {}).get("author") or payload.get("author", ""),
                                "language": payload.get("metadata", {}).get("language") or payload.get("language", ""),
                                "publisher": payload.get("metadata", {}).get("publisher") or payload.get("publisher", ""),
                                "identifier": payload.get("metadata", {}).get("identifier") or payload.get("identifier"),
                                "title_key": payload.get("title_key", ""),
                                "author_key": payload.get("author_key", ""),
                                "sampled_chapters": payload.get("sampled_chapters", []),
                                "revision": payload.get("revision", 0),
                            }
                            CharacterProfileRepository.save_book(session, book_dict)
                        elif collection in ("profile_editions", "editions"):
                            ed = EditionRecord.model_validate(payload)
                            CharacterProfileRepository.save_edition(session, ed)
                        elif collection in ("profile_chapter_mappings", "mappings"):
                            mapping = ChapterMapping.model_validate(payload)
                            CharacterProfileRepository.save_chapter_mapping(session, mapping)
                        elif collection in ("profile_submissions", "submissions"):
                            sub = SubmissionRecord.model_validate(payload)
                            CharacterProfileRepository.save_submission(session, sub)
                        elif collection in ("profile_events", "events"):
                            event = CharacterEvent.model_validate(payload)
                            ev_key = event_key or payload.get("event_key") or f"ev-{document_id}"
                            CharacterProfileRepository.save_event(session, event, ev_key)
                        elif collection in ("profile_evidence", "evidence"):
                            ev = EventEvidence.model_validate(payload)
                            ev_id = event_id or payload.get("event_id") or ""
                            CharacterProfileRepository.save_evidence(session, ev, ev_id)
                        session.commit()
                except Exception as exc:
                    if settings.structured_storage_backend == "postgres":
                        logger.error("Book profile Database write FAILED in postgres mode collection=%s doc=%s: %s", collection, document_id, exc)
                        raise exc
                    logger.warning("Book profile Database mirror failed in dual mode collection=%s doc=%s: %s", collection, document_id, exc)

            # 1. Sync to Firebase Firestore if active and not postgres-only
            if self.firestore_db and settings.structured_storage_backend in ("legacy", "dual"):
                try:
                    self.firestore_db.collection(collection).document(document_id).set(payload, merge=True)
                except Exception as exc:
                    logger.warning("Book profile Firestore mirror failed collection=%s doc=%s: %s", collection, document_id, exc)

            # 2. Sync to Cloudflare R2 bucket if active (only if legacy or dual)
            if settings.structured_storage_backend in ("legacy", "dual") and self.storage_repo and getattr(self.storage_repo, "is_r2_active", False):
                try:
                    r2_key = f"novels/{novel_folder}/profile/{collection}/{document_id}.json"
                    self.storage_repo._r2_put_json(r2_key, payload)
                except Exception as exc:
                    logger.warning("Book profile R2 mirror failed novel=%s collection=%s doc=%s: %s", novel_folder, collection, document_id, exc)

            # 3. Persist to local disk cache (only if legacy or dual)
            if settings.structured_storage_backend in ("legacy", "dual"):
                try:
                    base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "novels", novel_folder, "profile", collection)
                    os.makedirs(base_dir, exist_ok=True)
                    file_path = os.path.join(base_dir, f"{document_id}.json")
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                except Exception as exc:
                    logger.debug("Local disk persistence skipped: %s", exc)

        except Exception as exc:
            if settings.structured_storage_backend == "postgres":
                raise exc
            logger.warning("Book profile _persist failed collection=%s doc=%s: %s", collection, document_id, exc)




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

