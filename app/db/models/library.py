from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

JSONType = JSON().with_variant(JSONB, 'postgresql')


class NovelModel(Base, TimestampMixin):
    __tablename__ = 'novels'

    novel_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    original_title: Mapped[str] = mapped_column(String, nullable=False, default='')
    author: Mapped[str] = mapped_column(String, nullable=False, default='')
    genre: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default='')
    cover_r2_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default='ongoing')
    total_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    translated_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    chapters: Mapped[List['ChapterModel']] = relationship(
        'ChapterModel',
        back_populates='novel',
        cascade='all, delete-orphan',
        order_by='ChapterModel.chapter_index',
    )

    __table_args__ = (
        Index('idx_novels_title_lower', text('lower(title)')),
        Index('idx_novels_updated_at', 'updated_at'),
    )


class ChapterModel(Base):
    __tablename__ = 'chapters'

    novel_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('novels.novel_id', ondelete='CASCADE'),
        primary_key=True,
    )
    chapter_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[str] = mapped_column(String, nullable=False)
    chapter_title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default='not_translated')
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_text_preview: Mapped[str] = mapped_column(Text, nullable=False, default='')
    translated_text_preview: Mapped[str] = mapped_column(Text, nullable=False, default='')
    original_r2_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    translated_r2_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    translated_r2_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    novel: Mapped['NovelModel'] = relationship('NovelModel', back_populates='chapters')

    __table_args__ = (
        Index('idx_chapters_novel_chapter_id', 'novel_id', 'chapter_id', unique=True),
    )
