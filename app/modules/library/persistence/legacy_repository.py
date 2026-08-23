from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select, delete, desc
from sqlalchemy.orm import Session, selectinload

from app.modules.library.persistence.legacy_models import NovelModel, ChapterModel
from app.db.models.jobs import ImportJobModel, TranslationJobModel
from app.schemas.library import (
    NovelMetadata,
    NovelSummary,
    NovelStatus,
    ChapterItem,
    ChapterStatus,
    NovelCreateRequest,
    NovelUpdateRequest,
    ImportJobStatus,
)
from app.schemas.translation import TranslationJob, JobStatusEnum, InputType


class LibraryRepository:
    @staticmethod
    def _model_to_chapter_item(model: ChapterModel) -> ChapterItem:
        return ChapterItem(
            chapter_index=model.chapter_index,
            chapter_id=model.chapter_id,
            chapter_title=model.chapter_title,
            status=ChapterStatus(model.status) if model.status in ChapterStatus._value2member_map_ else ChapterStatus.NOT_TRANSLATED,
            word_count=model.word_count,
            original_text_preview=model.original_text_preview,
            translated_text_preview=model.translated_text_preview,
            updated_at=model.updated_at.isoformat() if model.updated_at else datetime.now(timezone.utc).isoformat(),
            r2_original_key=model.original_r2_key or '',
            r2_translated_key=model.translated_r2_key or '',
            r2_translated_url=model.translated_r2_url,
        )

    @staticmethod
    def _model_to_novel_summary(model: NovelModel) -> NovelSummary:
        return NovelSummary(
            novel_id=model.novel_id,
            title=model.title,
            original_title=model.original_title,
            author=model.author,
            genre=model.genre or [],
            description=model.description,
            cover_url=model.cover_r2_key,
            status=NovelStatus(model.status) if model.status in NovelStatus._value2member_map_ else NovelStatus.ONGOING,
            total_chapters=model.total_chapters,
            translated_chapters=model.translated_chapters,
            created_at=model.created_at.isoformat() if model.created_at else '',
            updated_at=model.updated_at.isoformat() if model.updated_at else '',
        )

    @classmethod
    def _model_to_novel_metadata(cls, model: NovelModel) -> NovelMetadata:
        summary = cls._model_to_novel_summary(model)
        chapters = [cls._model_to_chapter_item(c) for c in (model.chapters or [])]
        return NovelMetadata(
            **summary.model_dump(),
            chapters=chapters,
        )

    @classmethod
    def get_novel(cls, session: Session, novel_id: str) -> Optional[NovelMetadata]:
        stmt = (
            select(NovelModel)
            .where(NovelModel.novel_id == novel_id)
            .options(selectinload(NovelModel.chapters))
        )
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        return cls._model_to_novel_metadata(model)

    @classmethod
    def list_novels(cls, session: Session) -> List[NovelSummary]:
        stmt = select(NovelModel).order_by(NovelModel.updated_at.desc())
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_novel_summary(m) for m in models]

    @classmethod
    def create_novel(cls, session: Session, novel_id: str, req: NovelCreateRequest) -> NovelMetadata:
        now = datetime.now(timezone.utc)
        model = NovelModel(
            novel_id=novel_id,
            title=req.title,
            original_title=req.original_title,
            author=req.author,
            genre=req.genre,
            description=req.description,
            status=NovelStatus.ONGOING.value,
            total_chapters=0,
            translated_chapters=0,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        session.add(model)
        session.flush()
        return cls.get_novel(session, novel_id)

    @classmethod
    def save_novel(cls, session: Session, meta: NovelMetadata) -> NovelMetadata:
        model = session.get(NovelModel, meta.novel_id)
        now = datetime.now(timezone.utc)
        if not model:
            model = NovelModel(
                novel_id=meta.novel_id,
                created_at=now,
            )
            session.add(model)

        model.title = meta.title
        model.original_title = meta.original_title
        model.author = meta.author
        model.genre = meta.genre
        model.description = meta.description
        model.cover_r2_key = meta.cover_url
        model.status = meta.status.value if isinstance(meta.status, NovelStatus) else str(meta.status)
        # Counts are derived from persisted chapter rows below so a stale
        # aggregate cannot lower the totals after another worker adds data.
        model.updated_at = now
        model.revision = model.revision + 1 if model.revision is not None else 1

        existing_chapters = {c.chapter_index: c for c in (model.chapters or [])}
        for ch in meta.chapters:
            ch_model = existing_chapters.get(ch.chapter_index)
            if not ch_model:
                ch_model = ChapterModel(
                    novel_id=meta.novel_id,
                    chapter_index=ch.chapter_index,
                )
                ch_model.novel = model
                session.add(ch_model)

            ch_model.chapter_id = ch.chapter_id
            ch_model.chapter_title = ch.chapter_title
            ch_model.status = ch.status.value if isinstance(ch.status, ChapterStatus) else str(ch.status)
            ch_model.word_count = ch.word_count
            ch_model.original_text_preview = ch.original_text_preview
            ch_model.translated_text_preview = ch.translated_text_preview
            ch_model.original_r2_key = ch.r2_original_key
            ch_model.translated_r2_key = ch.r2_translated_key
            ch_model.translated_r2_url = ch.r2_translated_url
            ch_model.updated_at = now

        session.flush()
        model.total_chapters = len(model.chapters or [])
        model.translated_chapters = sum(
            1 for chapter in (model.chapters or []) if chapter.status == ChapterStatus.COMPLETED.value
        )
        session.flush()
        return cls._model_to_novel_metadata(model)

    @classmethod
    def update_novel(cls, session: Session, novel_id: str, req: NovelUpdateRequest) -> Optional[NovelMetadata]:
        model = session.get(NovelModel, novel_id)
        if not model:
            return None

        if req.title is not None:
            model.title = req.title
        if req.original_title is not None:
            model.original_title = req.original_title
        if req.author is not None:
            model.author = req.author
        if req.genre is not None:
            model.genre = req.genre
        if req.description is not None:
            model.description = req.description
        if req.status is not None:
            model.status = req.status.value if isinstance(req.status, NovelStatus) else str(req.status)

        model.updated_at = datetime.now(timezone.utc)
        model.revision = (model.revision or 0) + 1
        session.flush()
        return cls._model_to_novel_metadata(model)

    @classmethod
    def delete_novel(cls, session: Session, novel_id: str) -> bool:
        model = session.get(NovelModel, novel_id)
        if not model:
            return False
        session.delete(model)
        session.flush()
        return True

    @classmethod
    def get_chapter(cls, session: Session, novel_id: str, chapter_index: int) -> Optional[ChapterItem]:
        stmt = select(ChapterModel).where(
            ChapterModel.novel_id == novel_id,
            ChapterModel.chapter_index == chapter_index,
        )
        model = session.execute(stmt).scalar_one_or_none()
        if not model:
            return None
        return cls._model_to_chapter_item(model)

    @classmethod
    def list_chapters(cls, session: Session, novel_id: str) -> List[ChapterItem]:
        stmt = select(ChapterModel).where(ChapterModel.novel_id == novel_id).order_by(ChapterModel.chapter_index)
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_chapter_item(m) for m in models]

    @staticmethod
    def _model_to_import_job(model: ImportJobModel) -> ImportJobStatus:
        return ImportJobStatus(
            job_id=model.job_id,
            novel_id=model.novel_id,
            title=model.title,
            status=model.status,
            current_step=model.current_step,
            current_chapter=model.current_chapter,
            total_chapters=model.total_chapters,
            added_chapters=model.added_chapters,
            skipped_chapters=model.skipped_chapters,
            updated_chapters=model.updated_chapters,
            progress_percentage=model.progress_percentage,
            error_message=model.error_message,
            created_at=model.created_at.isoformat() if model.created_at else '',
            completed_at=model.completed_at.isoformat() if model.completed_at else None,
        )

    @classmethod
    def save_import_job(cls, session: Session, job: ImportJobStatus) -> ImportJobStatus:
        model = session.get(ImportJobModel, job.job_id)
        if not model:
            model = ImportJobModel(job_id=job.job_id)
            session.add(model)

        model.novel_id = job.novel_id
        model.title = job.title
        model.status = job.status
        model.current_step = job.current_step
        model.current_chapter = job.current_chapter
        model.total_chapters = job.total_chapters
        model.added_chapters = job.added_chapters
        model.skipped_chapters = job.skipped_chapters
        model.updated_chapters = job.updated_chapters
        model.progress_percentage = job.progress_percentage
        model.error_message = job.error_message

        if job.completed_at:
            try:
                model.completed_at = datetime.fromisoformat(job.completed_at)
            except Exception:
                model.completed_at = datetime.now(timezone.utc)

        session.flush()
        return cls._model_to_import_job(model)

    @classmethod
    def get_import_job(cls, session: Session, job_id: str) -> Optional[ImportJobStatus]:
        model = session.get(ImportJobModel, job_id)
        if not model:
            return None
        return cls._model_to_import_job(model)

    @classmethod
    def list_import_jobs(cls, session: Session) -> List[ImportJobStatus]:
        stmt = select(ImportJobModel).order_by(ImportJobModel.created_at.desc())
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_import_job(m) for m in models]

    @staticmethod
    def _model_to_translation_job(model: TranslationJobModel) -> TranslationJob:
        return TranslationJob(
            job_id=model.job_id,
            filename=model.filename,
            input_type=InputType(model.input_type) if model.input_type in InputType._value2member_map_ else InputType.TXT,
            status=JobStatusEnum(model.status) if model.status in JobStatusEnum._value2member_map_ else JobStatusEnum.PENDING,
            progress_percentage=model.progress_percentage,
            current_step=model.current_step,
            error_message=model.error_message,
            translated_file_path=model.translated_file_path,
            r2_url=model.r2_url,
            provider=model.provider,
            model=model.model,
            novel_id=model.novel_id,
            chapter_index=model.chapter_index,
            chapter_id=model.chapter_id,
            created_at=model.created_at.isoformat() if model.created_at else '',
            completed_at=model.completed_at.isoformat() if model.completed_at else None,
        )

    @classmethod
    def save_translation_job(cls, session: Session, job: TranslationJob) -> TranslationJob:
        model = session.get(TranslationJobModel, job.job_id)
        if not model:
            model = TranslationJobModel(job_id=job.job_id)
            session.add(model)

        model.filename = job.filename
        model.input_type = job.input_type.value if isinstance(job.input_type, InputType) else str(job.input_type)
        model.status = job.status.value if isinstance(job.status, JobStatusEnum) else str(job.status)
        model.progress_percentage = float(job.progress_percentage)
        model.current_step = job.current_step
        model.error_message = job.error_message
        model.translated_file_path = job.translated_file_path
        model.r2_url = job.r2_url
        model.provider = job.provider
        model.model = job.model
        model.novel_id = job.novel_id
        model.chapter_index = job.chapter_index
        model.chapter_id = job.chapter_id

        if job.completed_at:
            try:
                model.completed_at = datetime.fromisoformat(job.completed_at)
            except Exception:
                model.completed_at = datetime.now(timezone.utc)

        session.flush()
        return cls._model_to_translation_job(model)

    @classmethod
    def get_translation_job(cls, session: Session, job_id: str) -> Optional[TranslationJob]:
        model = session.get(TranslationJobModel, job_id)
        if not model:
            return None
        return cls._model_to_translation_job(model)

    @classmethod
    def list_translation_jobs(cls, session: Session) -> List[TranslationJob]:
        stmt = select(TranslationJobModel).order_by(TranslationJobModel.created_at.desc())
        models = session.execute(stmt).scalars().all()
        return [cls._model_to_translation_job(m) for m in models]
