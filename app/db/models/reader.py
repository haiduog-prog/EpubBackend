from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

JSONType = JSON().with_variant(JSONB, "postgresql")


class ReaderUserSettingsModel(Base, TimestampMixin):
    __tablename__ = "reader_user_settings"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    preferences: Mapped[Dict] = mapped_column(JSONType, nullable=False, default=dict)
    local_migrated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ReaderProgressModel(Base, TimestampMixin):
    __tablename__ = "reader_progress"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    novel_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("novels.novel_id", ondelete="CASCADE"),
        primary_key=True,
    )
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    scroll_top: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_reader_progress_user_updated", "user_id", "updated_at"),
    )


__all__ = ["ReaderProgressModel", "ReaderUserSettingsModel"]