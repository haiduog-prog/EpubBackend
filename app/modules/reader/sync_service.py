from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.reader import ReaderProgressModel, ReaderUserSettingsModel
from app.modules.reader.schemas import ReaderBookDetail
from app.modules.reader.service import reader_service
from app.modules.reader.sync_schemas import ReaderLocalMigrationPayload, ReaderStateResponse

_ALLOWED_PREFERENCE_KEYS = {"theme", "fontSize", "lineHeight", "readingWidth"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_preferences(value: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key in _ALLOWED_PREFERENCE_KEYS:
        if key not in value:
            continue
        raw = value[key]
        if key == "theme":
            if raw in {"day", "sepia", "night"}:
                result[key] = raw
        elif key == "fontSize":
            try:
                number = int(raw)
                if 16 <= number <= 26:
                    result[key] = number
            except (TypeError, ValueError):
                pass
        elif key == "lineHeight":
            try:
                number = float(raw)
                if 1.5 <= number <= 2.3:
                    result[key] = number
            except (TypeError, ValueError):
                pass
        elif key == "readingWidth":
            try:
                number = int(raw)
                if 600 <= number <= 900:
                    result[key] = number
            except (TypeError, ValueError):
                pass
    return result


class ReaderSyncService:
    def state(self, db: Session, user_id: str) -> ReaderStateResponse:
        settings_row = db.get(ReaderUserSettingsModel, user_id)
        progress_rows = list(
            db.scalars(
                select(ReaderProgressModel)
                .where(ReaderProgressModel.user_id == user_id)
                .order_by(ReaderProgressModel.updated_at.desc())
            )
        )
        return ReaderStateResponse(
            user_id=user_id,
            preferences=dict(settings_row.preferences or {}) if settings_row else {},
            local_migrated_at=settings_row.local_migrated_at if settings_row else None,
            progress=[
                {
                    "novel_id": row.novel_id,
                    "chapter_index": row.chapter_index,
                    "scroll_top": row.scroll_top,
                    "updated_at": row.updated_at,
                }
                for row in progress_rows
            ],
        )

    def migrate_local(self, db: Session, user_id: str, payload: ReaderLocalMigrationPayload) -> ReaderStateResponse:
        row = db.get(ReaderUserSettingsModel, user_id)
        if row and row.local_migrated_at:
            return self.state(db, user_id)

        if row is None:
            row = ReaderUserSettingsModel(user_id=user_id, preferences=sanitize_preferences(payload.preferences))
            db.add(row)
        else:
            row.preferences = sanitize_preferences(payload.preferences)
        row.local_migrated_at = _now()

        for item in payload.progress:
            if not reader_service._NOVEL_ID_PATTERN.fullmatch(item.novel_id):
                continue
            if not reader_service._library.get_novel(item.novel_id):
                continue
            existing = db.get(ReaderProgressModel, (user_id, item.novel_id))
            if existing is None:
                db.add(
                    ReaderProgressModel(
                        user_id=user_id,
                        novel_id=item.novel_id,
                        chapter_index=item.chapter_index,
                        scroll_top=item.scroll_top,
                    )
                )
        try:
            db.commit()
        except IntegrityError:
            # Another tab may have completed the one-time migration first.
            db.rollback()
        return self.state(db, user_id)

    def update_preferences(self, db: Session, user_id: str, preferences: Dict[str, Any]) -> ReaderStateResponse:
        row = db.get(ReaderUserSettingsModel, user_id)
        if row is None:
            row = ReaderUserSettingsModel(user_id=user_id, preferences=sanitize_preferences(preferences))
            db.add(row)
        else:
            row.preferences = sanitize_preferences(preferences)
        db.commit()
        return self.state(db, user_id)

    def update_progress(self, db: Session, user_id: str, novel_id: str, chapter_index: int, scroll_top: int) -> ReaderStateResponse:
        if not reader_service._NOVEL_ID_PATTERN.fullmatch(novel_id or ""):
            raise HTTPException(status_code=422, detail="novel_id không hợp lệ.")
        if chapter_index < 1 or scroll_top < 0:
            raise HTTPException(status_code=422, detail="Tiến độ đọc không hợp lệ.")
        metadata = reader_service._library.get_novel(novel_id)
        if not metadata:
            raise HTTPException(status_code=404, detail="Không tìm thấy bộ truyện này.")
        if not any(ch.chapter_index == chapter_index for ch in metadata.chapters):
            raise HTTPException(status_code=404, detail="Không tìm thấy chương này.")
        row = db.get(ReaderProgressModel, (user_id, novel_id))
        if row is None:
            db.add(ReaderProgressModel(user_id=user_id, novel_id=novel_id, chapter_index=chapter_index, scroll_top=scroll_top))
        else:
            row.chapter_index = chapter_index
            row.scroll_top = scroll_top
        db.commit()
        return self.state(db, user_id)


reader_sync_service = ReaderSyncService()