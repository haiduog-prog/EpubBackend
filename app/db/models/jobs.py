from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Integer, Float, Text, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JSONType = JSON().with_variant(JSONB, 'postgresql')


class TranslationJobModel(Base):
    __tablename__ = 'translation_jobs'

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    novel_id: Mapped[str] = mapped_column(String, nullable=False, default='')
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapter_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    filename: Mapped[str] = mapped_column(String, nullable=False, default='')
    input_type: Mapped[str] = mapped_column(String, nullable=False, default='txt')
    status: Mapped[str] = mapped_column(String, nullable=False, default='pending')
    progress_percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_step: Mapped[str] = mapped_column(String, nullable=False, default='')
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    translated_file_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    r2_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider: Mapped[str] = mapped_column(String, nullable=False, default='gemini')
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('idx_translation_jobs_status_created', 'status', 'created_at'),
    )


class ImportJobModel(Base):
    __tablename__ = 'import_jobs'

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    novel_id: Mapped[str] = mapped_column(String, nullable=False, default='')
    title: Mapped[str] = mapped_column(String, nullable=False, default='')
    status: Mapped[str] = mapped_column(String, nullable=False, default='pending')
    current_step: Mapped[str] = mapped_column(String, nullable=False, default='')
    current_chapter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('idx_import_jobs_status_created', 'status', 'created_at'),
    )
