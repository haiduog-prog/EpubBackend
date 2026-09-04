from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import String, Integer, Text, Boolean, DateTime, ForeignKey, Index, text
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
    current_epub_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    desired_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    built_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_structural_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    layout_standardized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dirty_chapters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    chapters: Mapped[List['ChapterModel']] = relationship(
        'ChapterModel',
        back_populates='novel',
        cascade='all, delete-orphan',
        order_by='ChapterModel.chapter_index',
    )
    build_jobs: Mapped[List['EpubBuildJobModel']] = relationship(
        'EpubBuildJobModel',
        back_populates='novel',
        cascade='all, delete-orphan',
        order_by='EpubBuildJobModel.created_at.desc()',
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
    review_status: Mapped[str] = mapped_column(String, nullable=False, default='pending')
    review_issues: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    reviewer_model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    novel: Mapped['NovelModel'] = relationship('NovelModel', back_populates='chapters')

    __table_args__ = (
        Index('idx_chapters_novel_chapter_id', 'novel_id', 'chapter_id', unique=True),
    )


class EpubBuildJobModel(Base, TimestampMixin):
    __tablename__ = 'epub_build_jobs'

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    novel_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('novels.novel_id', ondelete='CASCADE'),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default='queued')  # queued, processing, completed, failed
    strategy: Mapped[str] = mapped_column(String, nullable=False, default='fast_patch')  # fast_patch, full_rebuild
    dirty_chapters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    claimed_dirty_chapters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    is_structural: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    target_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    built_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    epub_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_step: Mapped[Optional[str]] = mapped_column(String, nullable=True, default='')
    current_chapter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    total_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    novel: Mapped['NovelModel'] = relationship('NovelModel', back_populates='build_jobs')

    __table_args__ = (
        Index('idx_epub_build_jobs_status_created', 'status', 'created_at'),
        Index('idx_epub_build_jobs_novel_status', 'novel_id', 'status'),
    )

