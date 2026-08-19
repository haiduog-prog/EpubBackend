from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import (
    String,
    Integer,
    Float,
    Text,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

JSONType = JSON().with_variant(JSONB, 'postgresql')


class ProfileBookModel(Base, TimestampMixin):
    __tablename__ = 'profile_books'

    book_id: Mapped[str] = mapped_column(String, primary_key=True)
    novel_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey('novels.novel_id', ondelete='SET NULL'),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String, nullable=False, default='')
    author: Mapped[str] = mapped_column(String, nullable=False, default='')
    language: Mapped[str] = mapped_column(String, nullable=False, default='')
    publisher: Mapped[str] = mapped_column(String, nullable=False, default='')
    identifier: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title_key: Mapped[str] = mapped_column(String, nullable=False, default='')
    author_key: Mapped[str] = mapped_column(String, nullable=False, default='')
    sampled_chapters: Mapped[list] = mapped_column(JSONType, nullable=False, default=list)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    editions: Mapped[List['ProfileEditionModel']] = relationship(
        'ProfileEditionModel',
        back_populates='book',
        cascade='all, delete-orphan',
    )
    submissions: Mapped[List['ProfileSubmissionModel']] = relationship(
        'ProfileSubmissionModel',
        back_populates='book',
        cascade='all, delete-orphan',
    )
    events: Mapped[List['ProfileEventModel']] = relationship(
        'ProfileEventModel',
        back_populates='book',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        Index('idx_profile_books_title_author_key', 'title_key', 'author_key'),
    )


class ProfileEditionModel(Base):
    __tablename__ = 'profile_editions'

    edition_id: Mapped[str] = mapped_column(String, primary_key=True)
    book_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_books.book_id', ondelete='CASCADE'),
        nullable=False,
    )
    metadata_payload: Mapped[dict] = mapped_column('metadata', JSONType, nullable=False, default=dict)
    fingerprints: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    chapter_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mapping_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    book: Mapped['ProfileBookModel'] = relationship('ProfileBookModel', back_populates='editions')
    mappings: Mapped[List['ProfileChapterMappingModel']] = relationship(
        'ProfileChapterMappingModel',
        back_populates='edition',
        cascade='all, delete-orphan',
    )
    submissions: Mapped[List['ProfileSubmissionModel']] = relationship(
        'ProfileSubmissionModel',
        back_populates='edition',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        Index('idx_profile_editions_book_id', 'book_id'),
    )


class ProfileChapterMappingModel(Base):
    __tablename__ = 'profile_chapter_mappings'

    edition_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_editions.edition_id', ondelete='CASCADE'),
        primary_key=True,
    )
    local_chapter_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_chapter_start: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_chapter_end: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String, nullable=False, default='metadata')
    mapping_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    edition: Mapped['ProfileEditionModel'] = relationship('ProfileEditionModel', back_populates='mappings')

    __table_args__ = (
        CheckConstraint('canonical_chapter_end >= canonical_chapter_start', name='chk_mapping_canonical_range'),
    )


class ProfileSubmissionModel(Base):
    __tablename__ = 'profile_submissions'

    submission_id: Mapped[str] = mapped_column(String, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    book_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_books.book_id', ondelete='CASCADE'),
        nullable=False,
    )
    edition_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_editions.edition_id', ondelete='CASCADE'),
        nullable=False,
    )
    local_chapter_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    local_chapter_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    canonical_chapter_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    canonical_chapter_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_type: Mapped[str] = mapped_column(String, nullable=False, default='events')
    chapter_fingerprint: Mapped[str] = mapped_column(String, nullable=False, default='')
    source_group_id: Mapped[str] = mapped_column(String, nullable=False, default='')
    source_type: Mapped[str] = mapped_column(String, nullable=False, default='user')
    status: Mapped[str] = mapped_column(String, nullable=False, default='completed')
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    book: Mapped['ProfileBookModel'] = relationship('ProfileBookModel', back_populates='submissions')
    edition: Mapped['ProfileEditionModel'] = relationship('ProfileEditionModel', back_populates='submissions')
    events: Mapped[List['ProfileEventModel']] = relationship(
        'ProfileEventModel',
        back_populates='submission',
        cascade='all, delete-orphan',
    )
    evidence: Mapped[List['ProfileEvidenceModel']] = relationship(
        'ProfileEvidenceModel',
        back_populates='submission',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        Index('idx_profile_submissions_book_id', 'book_id'),
        Index('idx_profile_submissions_edition_id', 'edition_id'),
    )


class ProfileEventModel(Base):
    __tablename__ = 'profile_events'

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    book_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_books.book_id', ondelete='CASCADE'),
        nullable=False,
    )
    character_id: Mapped[str] = mapped_column(String, nullable=False)
    character_original_name: Mapped[str] = mapped_column(String, nullable=False, default='')
    canonical_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    attribute_key: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False, default='set')
    value: Mapped[Optional[dict]] = mapped_column(JSONType, nullable=True)
    certainty: Mapped[str] = mapped_column(String, nullable=False, default='observed')
    status: Mapped[str] = mapped_column(String, nullable=False, default='pending')
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default='')
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_group_id: Mapped[str] = mapped_column(String, nullable=False, default='')
    source_submission_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_submissions.submission_id', ondelete='CASCADE'),
        nullable=False,
    )
    supersedes_event_id: Mapped[Optional[str]] = mapped_column(
        String,
        ForeignKey('profile_events.event_id', ondelete='SET NULL'),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    book: Mapped['ProfileBookModel'] = relationship('ProfileBookModel', back_populates='events')
    submission: Mapped['ProfileSubmissionModel'] = relationship('ProfileSubmissionModel', back_populates='events')
    evidence_items: Mapped[List['ProfileEvidenceModel']] = relationship(
        'ProfileEvidenceModel',
        back_populates='event',
        cascade='all, delete-orphan',
    )

    __table_args__ = (
        Index('idx_profile_events_book_status_chapter', 'book_id', 'status', 'canonical_chapter'),
        Index('idx_profile_events_book_char_chapter', 'book_id', 'character_id', 'canonical_chapter'),
        Index('idx_profile_events_source_submission', 'source_submission_id'),
    )


class ProfileEvidenceModel(Base):
    __tablename__ = 'profile_evidence'

    evidence_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_events.event_id', ondelete='CASCADE'),
        nullable=False,
    )
    event_key: Mapped[str] = mapped_column(String, nullable=False)
    source_group_id: Mapped[str] = mapped_column(String, nullable=False)
    submission_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('profile_submissions.submission_id', ondelete='CASCADE'),
        nullable=False,
    )
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, default='')
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    event: Mapped['ProfileEventModel'] = relationship('ProfileEventModel', back_populates='evidence_items')
    submission: Mapped['ProfileSubmissionModel'] = relationship('ProfileSubmissionModel', back_populates='evidence')

    __table_args__ = (
        UniqueConstraint('event_id', 'source_group_id', 'submission_id', name='uq_profile_evidence_unique_entry'),
        Index('idx_profile_evidence_event_id', 'event_id'),
    )


class ProfileSettingsModel(Base):
    __tablename__ = 'profile_settings'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    auto_approve: Mapped[bool] = mapped_column(default=True, nullable=False)
    min_independent_sources: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
