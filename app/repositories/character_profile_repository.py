from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select, update, delete, desc, func, and_, or_
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db.models.character_profile import (
    ProfileBookModel,
    ProfileEditionModel,
    ProfileChapterMappingModel,
    ProfileSubmissionModel,
    ProfileEventModel,
    ProfileEvidenceModel,
    ProfileSettingsModel,
)
from app.schemas.character_profile import (
    BookMetadata,
    FingerprintBundle,
    EditionRecord,
    ChapterMapping,
    SubmissionRecord,
    CharacterEvent,
    EventEvidence,
    ProfileSettingsResponse,
    BookListItem,
)


class CharacterProfileRepository:
    # ------------------ BOOKS ------------------
    @staticmethod
    def _model_to_book_dict(model: ProfileBookModel) -> Dict[str, Any]:
        return {
            'book_id': model.book_id,
            'novel_id': model.novel_id,
            'title': model.title,
            'author': model.author,
            'language': model.language,
            'publisher': model.publisher,
            'identifier': model.identifier,
            'title_key': model.title_key,
            'author_key': model.author_key,
            'sampled_chapters': model.sampled_chapters or [],
            'revision': model.revision or 0,
            'created_at': model.created_at,
            'updated_at': model.updated_at,
        }

    @classmethod
    def get_book(cls, session: Session, book_id: str) -> Optional[Dict[str, Any]]:
        model = session.get(ProfileBookModel, book_id)
        if not model:
            return None
        return cls._model_to_book_dict(model)

    @classmethod
    def find_book_by_keys(cls, session: Session, title_key: str, author_key: str) -> Optional[Dict[str, Any]]:
        stmt = select(ProfileBookModel).where(
            ProfileBookModel.title_key == title_key,
            ProfileBookModel.author_key == author_key,
        )
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        return cls._model_to_book_dict(model)

    @classmethod
    def list_books(cls, session: Session) -> List[BookListItem]:
        stmt = select(ProfileBookModel).options(
            selectinload(ProfileBookModel.editions),
            selectinload(ProfileBookModel.events),
        )
        models = session.execute(stmt).scalars().all()
        result = []
        for m in models:
            events = m.events or []
            pending_cnt = sum(1 for e in events if e.status == 'pending')
            result.append(
                BookListItem(
                    book_id=m.book_id,
                    title=m.title,
                    author=m.author,
                    language=m.language,
                    revision=m.revision or 0,
                    edition_count=len(m.editions or []),
                    event_count=len(events),
                    pending_event_count=pending_cnt,
                )
            )
        return result

    @classmethod
    def save_book(cls, session: Session, book_data: Dict[str, Any]) -> Dict[str, Any]:
        book_id = book_data['book_id']
        model = session.get(ProfileBookModel, book_id)
        now = datetime.now(timezone.utc)
        if not model:
            model = ProfileBookModel(
                book_id=book_id,
                novel_id=book_data.get('novel_id'),
                title=book_data.get('title', ''),
                author=book_data.get('author', ''),
                language=book_data.get('language', ''),
                publisher=book_data.get('publisher', ''),
                identifier=book_data.get('identifier'),
                title_key=book_data.get('title_key', ''),
                author_key=book_data.get('author_key', ''),
                sampled_chapters=book_data.get('sampled_chapters', []),
                revision=book_data.get('revision', 0),
                created_at=book_data.get('created_at') or now,
                updated_at=now,
            )
            session.add(model)
        else:
            if 'title' in book_data: model.title = book_data['title']
            if 'author' in book_data: model.author = book_data['author']
            if 'language' in book_data: model.language = book_data['language']
            if 'publisher' in book_data: model.publisher = book_data['publisher']
            if 'identifier' in book_data: model.identifier = book_data['identifier']
            if 'title_key' in book_data: model.title_key = book_data['title_key']
            if 'author_key' in book_data: model.author_key = book_data['author_key']
            if 'sampled_chapters' in book_data: model.sampled_chapters = book_data['sampled_chapters']
            if 'revision' in book_data: model.revision = book_data['revision']
            model.updated_at = now

        session.flush()
        return cls._model_to_book_dict(model)

    @classmethod
    def increment_book_revision(cls, session: Session, book_id: str) -> int:
        model = session.get(ProfileBookModel, book_id)
        if not model:
            return 0
        model.revision = (model.revision or 0) + 1
        model.updated_at = datetime.now(timezone.utc)
        session.flush()
        return model.revision

    @classmethod
    def delete_book(cls, session: Session, book_id: str) -> bool:
        model = session.get(ProfileBookModel, book_id)
        if not model:
            return False
        session.delete(model)
        session.flush()
        return True

    @classmethod
    def merge_books(cls, session: Session, source_book_id: str, target_book_id: str) -> bool:
        source = session.get(ProfileBookModel, source_book_id)
        target = session.get(ProfileBookModel, target_book_id)
        if not source or not target:
            return False

        # 1. Update editions
        session.execute(
            update(ProfileEditionModel)
            .where(ProfileEditionModel.book_id == source_book_id)
            .values(book_id=target_book_id)
        )

        # 2. Update submissions
        session.execute(
            update(ProfileSubmissionModel)
            .where(ProfileSubmissionModel.book_id == source_book_id)
            .values(book_id=target_book_id)
        )

        # 3. Update events
        session.execute(
            update(ProfileEventModel)
            .where(ProfileEventModel.book_id == source_book_id)
            .values(book_id=target_book_id)
        )

        # 4. Delete source book
        session.delete(source)
        session.flush()
        return True

    # ------------------ EDITIONS ------------------
    @staticmethod
    def _model_to_edition(model: ProfileEditionModel) -> EditionRecord:
        return EditionRecord(
            edition_id=model.edition_id,
            book_id=model.book_id,
            metadata=BookMetadata.model_validate(model.metadata_payload or {}),
            fingerprints=FingerprintBundle.model_validate(model.fingerprints or {}),
            chapter_count=model.chapter_count,
            mapping_revision=model.mapping_revision or 1,
            created_at=model.created_at,
        )

    @classmethod
    def get_edition(cls, session: Session, edition_id: str) -> Optional[EditionRecord]:
        model = session.get(ProfileEditionModel, edition_id)
        if not model:
            return None
        return cls._model_to_edition(model)

    @classmethod
    def list_editions(cls, session: Session, book_id: str) -> List[EditionRecord]:
        stmt = select(ProfileEditionModel).where(ProfileEditionModel.book_id == book_id)
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_edition(m) for m in models]

    @classmethod
    def save_edition(cls, session: Session, edition: EditionRecord) -> EditionRecord:
        model = session.get(ProfileEditionModel, edition.edition_id)
        now = datetime.now(timezone.utc)
        if not model:
            model = ProfileEditionModel(
                edition_id=edition.edition_id,
                book_id=edition.book_id,
                metadata_payload=edition.metadata.model_dump(mode='json'),
                fingerprints=edition.fingerprints.model_dump(mode='json'),
                chapter_count=edition.chapter_count,
                mapping_revision=edition.mapping_revision,
                created_at=edition.created_at or now,
            )
            session.add(model)
        else:
            model.metadata_payload = edition.metadata.model_dump(mode='json')
            model.fingerprints = edition.fingerprints.model_dump(mode='json')
            model.chapter_count = edition.chapter_count
            model.mapping_revision = edition.mapping_revision

        session.flush()
        return cls._model_to_edition(model)

    # ------------------ MAPPINGS ------------------
    @staticmethod
    def _model_to_mapping(model: ProfileChapterMappingModel) -> ChapterMapping:
        return ChapterMapping(
            edition_id=model.edition_id,
            local_chapter_index=model.local_chapter_index,
            canonical_chapter_start=model.canonical_chapter_start,
            canonical_chapter_end=model.canonical_chapter_end,
            confidence=model.confidence,
            source=model.source,
            mapping_revision=model.mapping_revision,
        )

    @classmethod
    def get_chapter_mappings(cls, session: Session, edition_id: str) -> List[ChapterMapping]:
        stmt = (
            select(ProfileChapterMappingModel)
            .where(ProfileChapterMappingModel.edition_id == edition_id)
            .order_by(ProfileChapterMappingModel.local_chapter_index)
        )
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_mapping(m) for m in models]

    @classmethod
    def get_single_mapping(cls, session: Session, edition_id: str, local_chapter: int) -> Optional[ChapterMapping]:
        stmt = select(ProfileChapterMappingModel).where(
            ProfileChapterMappingModel.edition_id == edition_id,
            ProfileChapterMappingModel.local_chapter_index == local_chapter,
        )
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        return cls._model_to_mapping(model)

    @classmethod
    def save_chapter_mapping(cls, session: Session, mapping: ChapterMapping) -> ChapterMapping:
        stmt = select(ProfileChapterMappingModel).where(
            ProfileChapterMappingModel.edition_id == mapping.edition_id,
            ProfileChapterMappingModel.local_chapter_index == mapping.local_chapter_index,
        )
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            model = ProfileChapterMappingModel(
                edition_id=mapping.edition_id,
                local_chapter_index=mapping.local_chapter_index,
                canonical_chapter_start=mapping.canonical_chapter_start,
                canonical_chapter_end=mapping.canonical_chapter_end,
                confidence=mapping.confidence,
                source=mapping.source,
                mapping_revision=mapping.mapping_revision,
            )
            session.add(model)
        else:
            model.canonical_chapter_start = mapping.canonical_chapter_start
            model.canonical_chapter_end = mapping.canonical_chapter_end
            model.confidence = mapping.confidence
            model.source = mapping.source
            model.mapping_revision = mapping.mapping_revision

        session.flush()
        return cls._model_to_mapping(model)

    # ------------------ SUBMISSIONS ------------------
    @staticmethod
    def _model_to_submission(model: ProfileSubmissionModel) -> SubmissionRecord:
        event_ids = [e.event_id for e in (model.events or [])]
        return SubmissionRecord(
            submission_id=model.submission_id,
            idempotency_key=model.idempotency_key,
            book_id=model.book_id,
            edition_id=model.edition_id,
            local_chapter_index=model.local_chapter_start,
            canonical_chapter_start=model.canonical_chapter_start,
            canonical_chapter_end=model.canonical_chapter_end,
            input_type='chapter_text' if model.input_type == 'chapter_text' else 'structured_events',
            content_fingerprint=model.chapter_fingerprint,
            source_group_id=model.source_group_id,
            source_label=model.source_type,
            status=model.status,
            error_message=model.error_message,
            event_ids=event_ids,
            created_at=model.created_at,
            completed_at=model.completed_at,
        )

    @classmethod
    def get_submission(cls, session: Session, submission_id: str) -> Optional[SubmissionRecord]:
        stmt = (
            select(ProfileSubmissionModel)
            .where(ProfileSubmissionModel.submission_id == submission_id)
            .options(selectinload(ProfileSubmissionModel.events))
        )
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        return cls._model_to_submission(model)

    @classmethod
    def get_submission_by_idempotency_key(cls, session: Session, key: str) -> Optional[SubmissionRecord]:
        stmt = (
            select(ProfileSubmissionModel)
            .where(ProfileSubmissionModel.idempotency_key == key)
            .options(selectinload(ProfileSubmissionModel.events))
        )
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        return cls._model_to_submission(model)

    @classmethod
    def save_submission(cls, session: Session, sub: SubmissionRecord) -> SubmissionRecord:
        model = session.get(ProfileSubmissionModel, sub.submission_id)
        now = datetime.now(timezone.utc)
        if not model:
            model = ProfileSubmissionModel(
                submission_id=sub.submission_id,
                idempotency_key=sub.idempotency_key,
                book_id=sub.book_id,
                edition_id=sub.edition_id,
                local_chapter_start=sub.local_chapter_index,
                local_chapter_end=sub.local_chapter_index,
                canonical_chapter_start=sub.canonical_chapter_start,
                canonical_chapter_end=sub.canonical_chapter_end,
                input_type=sub.input_type,
                chapter_fingerprint=sub.content_fingerprint,
                source_group_id=sub.source_group_id,
                source_type=sub.source_label or 'user',
                status=sub.status,
                error_message=sub.error_message,
                created_at=sub.created_at or now,
                completed_at=sub.completed_at,
            )
            session.add(model)
        else:
            model.status = sub.status
            model.error_message = sub.error_message
            model.completed_at = sub.completed_at

        session.flush()
        return cls._model_to_submission(model)

    # ------------------ EVENTS & EVIDENCE ------------------
    @staticmethod
    def _model_to_event(model: ProfileEventModel) -> CharacterEvent:
        val = model.value
        if isinstance(val, dict) and 'val' in val and len(val) == 1:
            val = val['val']
        return CharacterEvent(
            event_id=model.event_id,
            book_id=model.book_id,
            character_id=model.character_id,
            character_original_name=model.character_original_name,
            canonical_chapter=model.canonical_chapter,
            category=model.category,
            attribute_key=model.attribute_key,
            operation=model.operation,
            value=val,
            certainty=model.certainty,
            status=model.status,
            evidence=model.evidence,
            confidence=model.confidence,
            source_group_id=model.source_group_id,
            source_submission_id=model.source_submission_id,
            supersedes_event_id=model.supersedes_event_id,
            created_at=model.created_at,
            reviewed_at=model.reviewed_at,
            schema_version=model.schema_version,
        )

    @classmethod
    def get_event(cls, session: Session, event_id: str) -> Optional[CharacterEvent]:
        model = session.get(ProfileEventModel, event_id)
        if not model:
            return None
        return cls._model_to_event(model)

    @classmethod
    def get_event_by_key(cls, session: Session, event_key: str) -> Optional[CharacterEvent]:
        stmt = select(ProfileEventModel).where(ProfileEventModel.event_key == event_key)
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        return cls._model_to_event(model)

    @classmethod
    def list_events(
        cls,
        session: Session,
        book_id: str,
        status: Optional[str] = None,
        character_id: Optional[str] = None,
        max_canonical_chapter: Optional[int] = None,
    ) -> List[CharacterEvent]:
        stmt = select(ProfileEventModel).where(ProfileEventModel.book_id == book_id)
        if status:
            stmt = stmt.where(ProfileEventModel.status == status)
        if character_id:
            stmt = stmt.where(ProfileEventModel.character_id == character_id)
        if max_canonical_chapter is not None:
            stmt = stmt.where(ProfileEventModel.canonical_chapter <= max_canonical_chapter)
        
        stmt = stmt.order_by(ProfileEventModel.canonical_chapter.asc(), ProfileEventModel.created_at.asc())
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_event(m) for m in models]

    @classmethod
    def list_all_events(
        cls,
        session: Session,
        status: Optional[str] = None,
        max_canonical_chapter: Optional[int] = None,
    ) -> List[CharacterEvent]:
        """Query events across all books (no book_id filter)."""
        stmt = select(ProfileEventModel)
        if status:
            stmt = stmt.where(ProfileEventModel.status == status)
        if max_canonical_chapter is not None:
            stmt = stmt.where(ProfileEventModel.canonical_chapter <= max_canonical_chapter)
        stmt = stmt.order_by(ProfileEventModel.canonical_chapter.asc(), ProfileEventModel.created_at.asc())
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_event(m) for m in models]

    @classmethod
    def save_event(cls, session: Session, event: CharacterEvent, event_key: str) -> CharacterEvent:
        model = session.get(ProfileEventModel, event.event_id)
        now = datetime.now(timezone.utc)
        val_to_save = event.value if isinstance(event.value, (dict, list)) else {'val': event.value}

        if not model:
            model = ProfileEventModel(
                event_id=event.event_id,
                event_key=event_key,
                book_id=event.book_id,
                character_id=event.character_id,
                character_original_name=event.character_original_name,
                canonical_chapter=event.canonical_chapter,
                category=event.category,
                attribute_key=event.attribute_key,
                operation=event.operation,
                value=val_to_save,
                certainty=event.certainty,
                status=event.status,
                evidence=event.evidence,
                confidence=event.confidence,
                source_group_id=event.source_group_id,
                source_submission_id=event.source_submission_id,
                supersedes_event_id=event.supersedes_event_id,
                created_at=event.created_at or now,
                reviewed_at=event.reviewed_at,
                schema_version=event.schema_version,
            )
            session.add(model)
        else:
            model.character_original_name = event.character_original_name
            model.canonical_chapter = event.canonical_chapter
            model.category = event.category
            model.attribute_key = event.attribute_key
            model.operation = event.operation
            model.value = val_to_save
            model.certainty = event.certainty
            model.status = event.status
            model.evidence = event.evidence
            model.confidence = event.confidence
            model.reviewed_at = event.reviewed_at
            model.supersedes_event_id = event.supersedes_event_id

        session.flush()
        return cls._model_to_event(model)

    @classmethod
    def update_event_status(
        cls,
        session: Session,
        event_id: str,
        status: str,
        reviewed_at: Optional[datetime] = None,
        value: Optional[Any] = None,
        evidence: Optional[str] = None,
    ) -> Optional[CharacterEvent]:
        model = session.get(ProfileEventModel, event_id)
        if not model:
            return None
        model.status = status
        model.reviewed_at = reviewed_at or datetime.now(timezone.utc)
        if value is not None:
            model.value = value if isinstance(value, (dict, list)) else {'val': value}
        if evidence is not None:
            model.evidence = evidence
        session.flush()
        return cls._model_to_event(model)

    @classmethod
    def list_all_editions(cls, session: Session) -> List[EditionRecord]:
        stmt = select(ProfileEditionModel)
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_edition(m) for m in models]

    @classmethod
    def list_all_mappings(cls, session: Session) -> List[ChapterMapping]:
        stmt = select(ProfileChapterMappingModel)
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_mapping(m) for m in models]

    @classmethod
    def list_all_submissions(cls, session: Session) -> List[SubmissionRecord]:
        stmt = select(ProfileSubmissionModel).options(selectinload(ProfileSubmissionModel.events))
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_submission(m) for m in models]

    @classmethod
    def list_all_evidence(cls, session: Session) -> List[EventEvidence]:

        stmt = select(ProfileEvidenceModel)
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_evidence(m) for m in models]

    # ------------------ EVIDENCE ------------------
    @staticmethod
    def _model_to_evidence(model: ProfileEvidenceModel) -> EventEvidence:
        return EventEvidence(
            evidence_id=model.evidence_id,
            event_key=model.event_key,
            source_group_id=model.source_group_id,
            submission_id=model.submission_id,
            excerpt=model.excerpt,
            confidence=model.confidence,
            created_at=model.created_at,
        )

    @classmethod
    def save_evidence(cls, session: Session, ev: EventEvidence, event_id: str = "") -> Optional[EventEvidence]:
        if not event_id and ev.event_key:
            stmt = select(ProfileEventModel.event_id).where(ProfileEventModel.event_key == ev.event_key)
            event_id = session.execute(stmt).scalar_one_or_none() or ""

        if not event_id:
            return None

        model = session.get(ProfileEvidenceModel, ev.evidence_id)
        if not model:
            stmt = select(ProfileEvidenceModel).where(
                ProfileEvidenceModel.event_id == event_id,
                ProfileEvidenceModel.source_group_id == ev.source_group_id,
                ProfileEvidenceModel.submission_id == ev.submission_id,
            )
            model = session.execute(stmt).scalar_one_or_none()

        now = datetime.now(timezone.utc)
        if not model:
            model = ProfileEvidenceModel(
                evidence_id=ev.evidence_id,
                event_id=event_id,
                event_key=ev.event_key,
                source_group_id=ev.source_group_id,
                submission_id=ev.submission_id,
                excerpt=ev.excerpt,
                confidence=ev.confidence,
                created_at=ev.created_at or now,
            )
            session.add(model)
        else:
            model.event_id = event_id
            model.excerpt = ev.excerpt
            model.confidence = ev.confidence

        session.flush()
        return cls._model_to_evidence(model)


    @classmethod
    def list_evidence_by_event(cls, session: Session, event_id: str) -> List[EventEvidence]:
        stmt = select(ProfileEvidenceModel).where(ProfileEvidenceModel.event_id == event_id)
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_evidence(m) for m in models]

    @classmethod
    def get_settings(cls, session: Session) -> ProfileSettingsResponse:
        model = session.get(ProfileSettingsModel, 1)
        if not model:
            default_auto_approve = getattr(settings, "book_bible_auto_approve", False)
            default_sources = getattr(settings, "book_bible_min_independent_sources", 2)
            model = ProfileSettingsModel(id=1, auto_approve=default_auto_approve, min_independent_sources=default_sources)
            session.add(model)
            session.flush()
        return ProfileSettingsResponse(
            auto_approve=model.auto_approve,
            min_independent_sources=model.min_independent_sources,
        )


    @classmethod
    def update_settings(
        cls,
        session: Session,
        auto_approve: Optional[bool] = None,
        min_independent_sources: Optional[int] = None,
    ) -> ProfileSettingsResponse:
        model = session.get(ProfileSettingsModel, 1)
        if not model:
            model = ProfileSettingsModel(id=1)
            session.add(model)

        if auto_approve is not None:
            model.auto_approve = auto_approve
        if min_independent_sources is not None:
            model.min_independent_sources = min_independent_sources
        model.updated_at = datetime.now(timezone.utc)
        session.flush()
        return ProfileSettingsResponse(
            auto_approve=model.auto_approve,
            min_independent_sources=model.min_independent_sources,
        )
