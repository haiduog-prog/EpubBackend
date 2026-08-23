from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.book_bible.persistence.legacy_models import BookBibleModel
from app.schemas.book_bible import BookBible


class BookBibleRepository:
    @classmethod
    def get_book_bible(cls, session: Session, novel_id: str) -> Optional[BookBible]:
        stmt = select(BookBibleModel).where(BookBibleModel.novel_id == novel_id)
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        
        payload = dict(model.payload or {})
        payload['novel_id'] = model.novel_id
        payload['schema_version'] = model.schema_version
        payload['bible_revision'] = model.bible_revision
        return BookBible.model_validate(payload)

    @classmethod
    def lock_book_bible_for_update(cls, session: Session, novel_id: str) -> Optional[BookBibleModel]:
        # SQLite doesn't support with_for_update, but postgres does
        stmt = select(BookBibleModel).where(BookBibleModel.novel_id == novel_id)
        try:
            stmt = stmt.with_for_update()
        except Exception:
            pass
        return session.execute(stmt).scalar_one_or_none()

    @classmethod
    def save_book_bible(cls, session: Session, bible: BookBible) -> BookBible:
        model = session.get(BookBibleModel, bible.novel_id)
        now = datetime.now(timezone.utc)
        payload = bible.model_dump(mode='json')

        if not model:
            model = BookBibleModel(
                novel_id=bible.novel_id,
                schema_version=bible.schema_version,
                bible_revision=bible.bible_revision,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            session.add(model)
        else:
            model.schema_version = bible.schema_version
            model.bible_revision = bible.bible_revision
            model.payload = payload
            model.updated_at = now

        session.flush()
        return cls.get_book_bible(session, bible.novel_id)

    @classmethod
    def merge_delta_transactional(cls, session: Session, novel_id: str, delta) -> BookBible:
        from app.modules.book_bible.application.facade import BookBibleService
        stmt = select(BookBibleModel).where(BookBibleModel.novel_id == novel_id)
        if session.bind and session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        model = session.execute(stmt).scalar_one_or_none()

        if model and model.payload:
            payload = dict(model.payload or {})
            payload['novel_id'] = model.novel_id
            payload['schema_version'] = model.schema_version
            payload['bible_revision'] = model.bible_revision
            existing = BookBibleService.ensure_timeline(BookBible.model_validate(payload))
        else:
            existing = BookBible(novel_id=novel_id)

        target = existing.model_copy(deep=True)
        merged = BookBibleService.merge_delta(target, delta)

        now = datetime.now(timezone.utc)
        payload = merged.model_dump(mode='json')

        if not model:
            model = BookBibleModel(
                novel_id=novel_id,
                schema_version=merged.schema_version,
                bible_revision=merged.bible_revision,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            session.add(model)
        else:
            model.schema_version = merged.schema_version
            model.bible_revision = merged.bible_revision
            model.payload = payload
            model.updated_at = now

        session.flush()
        return merged

    @classmethod
    def delete_book_bible(cls, session: Session, novel_id: str) -> bool:
        model = session.get(BookBibleModel, novel_id)
        if not model:
            return False
        session.delete(model)
        session.flush()
        return True

