from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.book_bible.persistence.legacy_models import BookBibleModel
from app.schemas.book_bible import BookBible, BookBibleDelta


class BookBibleRepository:
    @staticmethod
    def _overlay_explicit_metadata(target: BookBible, incoming: BookBible) -> BookBible:
        """Apply non-default full-snapshot metadata without clearing old values."""
        target_style = target.style_guide
        incoming_style = incoming.style_guide
        default_style = type(incoming_style)()
        for field in type(incoming_style).model_fields:
            value = getattr(incoming_style, field)
            if value != getattr(default_style, field):
                setattr(target_style, field, value)

        target_source = target.source_profile
        incoming_source = incoming.source_profile
        default_source = type(incoming_source)()
        for field in type(incoming_source).model_fields:
            value = getattr(incoming_source, field)
            if value != getattr(default_source, field):
                setattr(target_source, field, value)
        return target

    @staticmethod
    def _overlay_snapshot_entities(target: BookBible, incoming: BookBible) -> BookBible:
        """Make entities present in a full snapshot authoritative, without deleting others."""
        from app.modules.book_bible.application.facade import BookBibleService

        def overlay(items, incoming_items, id_field):
            for incoming_item in incoming_items:
                incoming_id = getattr(incoming_item, id_field, None)
                incoming_original = BookBibleService._key(getattr(incoming_item, "original_name", ""))
                incoming_vi = BookBibleService._key(getattr(incoming_item, "vi_name", ""))
                for index, current_item in enumerate(items):
                    current_id = getattr(current_item, id_field, None)
                    current_original = BookBibleService._key(getattr(current_item, "original_name", ""))
                    current_vi = BookBibleService._key(getattr(current_item, "vi_name", ""))
                    if (
                        (incoming_id and incoming_id == current_id)
                        or (incoming_original and incoming_original == current_original)
                        or (incoming_vi and incoming_vi == current_vi)
                    ):
                        replacement = incoming_item.model_copy(deep=True)
                        if id_field in type(replacement).model_fields and not getattr(replacement, id_field, None):
                            setattr(replacement, id_field, current_id)
                        items[index] = replacement
                        break

        overlay(target.characters, incoming.characters, "character_id")
        overlay(target.places, incoming.places, "place_id")
        overlay(target.terms, incoming.terms, "term_id")
        return target

    @staticmethod
    def _begin_write_transaction(session: Session) -> None:
        """Serialize read/merge/write on SQLite, where FOR UPDATE is ignored."""
        if session.bind and session.bind.dialect.name == "sqlite":
            connection = session.connection()
            dbapi_connection = connection.connection
            dbapi_connection = getattr(dbapi_connection, "driver_connection", dbapi_connection)
            if not dbapi_connection.in_transaction:
                # SQLAlchemy may have opened a logical transaction while the
                # SQLite driver is still in its deferred state.  Begin the
                # driver transaction explicitly before the read/merge.
                dbapi_connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _merge_full_snapshot(
        existing: BookBible,
        incoming: BookBible,
        *,
        allow_authoritative_updates: bool = True,
    ) -> BookBible:
        from app.modules.book_bible.application.facade import BookBibleService

        target = existing.model_copy(deep=True)
        if target.novel_id == "default" and incoming.novel_id != "default":
            target.novel_id = incoming.novel_id
        observation_index = {item.observation_id: i for i, item in enumerate(target.address_observations)}
        for item in incoming.address_observations:
            if item.observation_id in observation_index:
                if allow_authoritative_updates:
                    target.address_observations[observation_index[item.observation_id]] = item.model_copy(deep=True)
            else:
                target.address_observations.append(item.model_copy(deep=True))
                observation_index[item.observation_id] = len(target.address_observations) - 1
        delta = BookBibleDelta(
            new_characters=incoming.characters,
            new_places=incoming.places,
            new_terms=incoming.terms,
            # Full snapshots already merge materialized observations above.
            # BookBibleDelta carries extraction candidates instead, whose
            # schema is intentionally different from AddressObservation.
            style_guide=incoming.style_guide,
            source_profile=incoming.source_profile,
        )
        target = BookBibleService.merge_delta(target, delta)
        if allow_authoritative_updates:
            target = BookBibleRepository._overlay_snapshot_entities(target, incoming)
            target = BookBibleRepository._overlay_explicit_metadata(target, incoming)
        pending_index = {item.change_id: i for i, item in enumerate(target.pending_changes)}
        for item in incoming.pending_changes:
            if item.change_id in pending_index:
                if allow_authoritative_updates:
                    target.pending_changes[pending_index[item.change_id]] = item.model_copy(deep=True)
            else:
                target.pending_changes.append(item.model_copy(deep=True))
        if allow_authoritative_updates:
            target.scan_state.update(incoming.scan_state or {})
        target.schema_version = max(target.schema_version, incoming.schema_version)
        return BookBibleService.ensure_timeline(target)

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
    def save_book_bible(cls, session: Session, bible: BookBible, *, replace: bool = False) -> BookBible:
        cls._begin_write_transaction(session)
        model = session.get(BookBibleModel, bible.novel_id)
        now = datetime.now(timezone.utc)

        if not model:
            from app.modules.book_bible.application.facade import BookBibleService
            committed = bible.model_copy(deep=True)
            committed.bible_revision = max(1, committed.bible_revision)
            committed = BookBibleService.ensure_timeline(committed)
            payload = committed.model_dump(mode="json")
            model = BookBibleModel(
                novel_id=bible.novel_id,
                schema_version=committed.schema_version,
                bible_revision=committed.bible_revision,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            session.add(model)
        else:
            current_payload = dict(model.payload or {})
            current_payload.update({"novel_id": model.novel_id, "schema_version": model.schema_version, "bible_revision": model.bible_revision})
            current = BookBible.model_validate(current_payload)
            # A caller may hand us a snapshot loaded before another worker
            # committed.  Such a snapshot can contribute genuinely new
            # entities, but must not replace newer canonical names, review
            # state, or observations.  A strictly newer revision is the only
            # snapshot allowed to make existing values authoritative.
            snapshot_is_newer = bible.bible_revision > (model.bible_revision or 0)
            committed = (
                bible.model_copy(deep=True)
                if replace
                else cls._merge_full_snapshot(
                    current,
                    bible,
                    allow_authoritative_updates=snapshot_is_newer,
                )
            )
            committed.bible_revision = max((model.bible_revision or 0) + 1, committed.bible_revision)
            payload = committed.model_dump(mode="json")
            model.schema_version = committed.schema_version
            model.bible_revision = committed.bible_revision
            model.payload = payload
            model.updated_at = now

        session.flush()
        return cls.get_book_bible(session, bible.novel_id)

    @classmethod
    def merge_delta_transactional(cls, session: Session, novel_id: str, delta) -> BookBible:
        from app.modules.book_bible.application.facade import BookBibleService
        cls._begin_write_transaction(session)
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
        merged.bible_revision = (model.bible_revision if model else 0) + 1

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
        return cls.get_book_bible(session, novel_id)

    @classmethod
    def delete_book_bible(cls, session: Session, novel_id: str) -> bool:
        model = session.get(BookBibleModel, novel_id)
        if not model:
            return False
        session.delete(model)
        session.flush()
        return True

