import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Set, Any, Dict
from sqlalchemy import select, delete, desc, or_, and_, text, case
from sqlalchemy.orm import Session, selectinload

from app.config import settings

logger = logging.getLogger("EpubBackend.LibraryRepository")


from app.modules.library.persistence.legacy_models import NovelModel, ChapterModel, EpubBuildJobModel
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
    EpubBuildJobResponse,
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
            updated_at=model.updated_at.isoformat() if model.updated_at else None,
            r2_original_key=model.original_r2_key or '',
            r2_translated_key=model.translated_r2_key or '',
            r2_translated_url=model.translated_r2_url,
            review_status=model.review_status or "pending",
            review_issues=model.review_issues or [],
            reviewer_model=model.reviewer_model,
            reviewed_at=model.reviewed_at.isoformat() if model.reviewed_at else None,
            review_error=model.review_error,
        )

    @classmethod
    def _normalize_dirty_chapters(cls, data: Any) -> List[int]:
        if not data:
            return []
        if isinstance(data, dict):
            res = []
            for k in data.keys():
                try:
                    res.append(int(k))
                except (ValueError, TypeError):
                    pass
            return sorted(res)
        if isinstance(data, (list, tuple, set)):
            res = []
            for item in data:
                try:
                    res.append(int(item))
                except (ValueError, TypeError):
                    pass
            return sorted(res)
        return []

    @classmethod
    def _model_to_novel_summary(cls, model: NovelModel) -> NovelSummary:
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
            current_epub_key=model.current_epub_key,
            desired_revision=model.desired_revision or 0,
            built_revision=model.built_revision or 0,
            is_structural_dirty=bool(model.is_structural_dirty),
            dirty_chapters=cls._normalize_dirty_chapters(model.dirty_chapters),
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
        if meta.current_epub_key:
            if not model.current_epub_key or (meta.built_revision or 0) >= (model.built_revision or 0):
                model.current_epub_key = meta.current_epub_key
        if meta.desired_revision:
            model.desired_revision = max(model.desired_revision or 0, meta.desired_revision)
        if meta.built_revision:
            model.built_revision = max(model.built_revision or 0, meta.built_revision)
        if meta.is_structural_dirty is not None:
            model.is_structural_dirty = model.is_structural_dirty or bool(meta.is_structural_dirty)
        if meta.dirty_chapters is not None:
            raw_existing = model.dirty_chapters
            if isinstance(raw_existing, dict):
                merged = {str(k): int(v) for k, v in raw_existing.items()}
            elif isinstance(raw_existing, (list, tuple, set)):
                merged = {str(c): model.desired_revision or 1 for c in raw_existing}
            else:
                merged = {}

            if isinstance(meta.dirty_chapters, dict):
                merged.update({str(k): int(v) for k, v in meta.dirty_chapters.items()})
            elif isinstance(meta.dirty_chapters, (list, tuple, set)):
                for c in meta.dirty_chapters:
                    if str(c) not in merged:
                        merged[str(c)] = model.desired_revision or 1

            model.dirty_chapters = merged


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
            ch_model.review_status = ch.review_status or "pending"
            ch_model.review_issues = [issue.model_dump() if hasattr(issue, "model_dump") else issue for issue in (ch.review_issues or [])]
            ch_model.reviewer_model = ch.reviewer_model
            ch_model.reviewed_at = datetime.fromisoformat(ch.reviewed_at) if ch.reviewed_at else None
            ch_model.review_error = ch.review_error
            if ch.updated_at:
                try:
                    dt = datetime.fromisoformat(ch.updated_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ch_model.updated_at = dt
                except Exception:
                    ch_model.updated_at = ch_model.updated_at or now
            elif not ch_model.updated_at:
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

        structural_changed = False
        if req.title is not None and req.title != model.title:
            model.title = req.title
            structural_changed = True
        if req.original_title is not None and req.original_title != model.original_title:
            model.original_title = req.original_title
            structural_changed = True
        if req.author is not None and req.author != model.author:
            model.author = req.author
            structural_changed = True
        if req.genre is not None and req.genre != model.genre:
            model.genre = req.genre
            structural_changed = True
        if req.description is not None and req.description != model.description:
            model.description = req.description
            structural_changed = True
        if req.status is not None:
            model.status = req.status.value if isinstance(req.status, NovelStatus) else str(req.status)

        if structural_changed:
            model.is_structural_dirty = True
            model.desired_revision = (model.desired_revision or 0) + 1

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

    # ------------------------------------------------------------------
    # EPUB Fast Patch & Build Job Operations
    # ------------------------------------------------------------------
    @classmethod
    def _model_to_epub_build_job(cls, model: EpubBuildJobModel) -> EpubBuildJobResponse:
        return EpubBuildJobResponse(
            job_id=model.job_id,
            novel_id=model.novel_id,
            status=model.status,
            strategy=model.strategy,
            dirty_chapters=cls._normalize_dirty_chapters(model.dirty_chapters),
            is_structural=bool(model.is_structural),
            target_revision=model.target_revision or 0,
            built_revision=model.built_revision,
            epub_key=model.epub_key,
            attempts=model.attempts or 0,
            error_message=model.error_message,
            current_step=model.current_step or "",
            current_chapter=model.current_chapter,
            total_chapters=model.total_chapters or 0,
            processed_chapters=model.processed_chapters or 0,
            progress_percentage=model.progress_percentage or 0,
            created_at=model.created_at.isoformat() if model.created_at else "",
            started_at=model.started_at.isoformat() if model.started_at else None,
            completed_at=model.completed_at.isoformat() if model.completed_at else None,
        )

    @classmethod
    def mark_dirty_and_enqueue_job(
        cls,
        session: Session,
        novel_id: str,
        dirty_indexes: Optional[List[int]] = None,
        is_structural: bool = False,
        force_rebuild: bool = False,
    ) -> EpubBuildJobResponse:
        """
        Mark novel state dirty and coalesce/enqueue an EPUB build job atomically with row-level locking
        and per-chapter revision tracking.
        """
        stmt_novel = (
            select(NovelModel)
            .where(NovelModel.novel_id == novel_id)
        )
        try:
            novel = session.execute(stmt_novel.with_for_update()).scalar_one_or_none()
        except Exception:
            novel = session.execute(stmt_novel).scalar_one_or_none()

        if not novel:
            from app.modules.library.application.facade import library_service
            meta = library_service.get_novel(novel_id)
            if meta:
                novel = NovelModel(
                    novel_id=meta.novel_id,
                    title=meta.title,
                    author=meta.author,
                    status=meta.status.value if hasattr(meta.status, "value") else str(meta.status),
                    current_epub_key=meta.current_epub_key,
                    built_revision=meta.built_revision or 0,
                    desired_revision=meta.built_revision or 0,
                )
                session.add(novel)
                session.flush()
            else:
                raise ValueError(f"Không tìm thấy bộ truyện '{novel_id}'")

        if is_structural:
            novel.is_structural_dirty = True

        novel.desired_revision = (novel.desired_revision or 0) + 1
        current_rev = novel.desired_revision

        # Map {str(ch): rev}
        raw_dirty = novel.dirty_chapters
        if isinstance(raw_dirty, dict):
            dirty_dict = {str(k): int(v) for k, v in raw_dirty.items()}
        elif isinstance(raw_dirty, (list, tuple, set)):
            dirty_dict = {str(c): current_rev for c in raw_dirty}
        else:
            dirty_dict = {}

        if dirty_indexes:
            for idx in dirty_indexes:
                dirty_dict[str(idx)] = current_rev

        novel.dirty_chapters = dirty_dict

        # Look for existing queued job to coalesce with row-level lock
        stmt_job = (
            select(EpubBuildJobModel)
            .where(
                EpubBuildJobModel.novel_id == novel_id,
                EpubBuildJobModel.status == "queued",
            )
            .order_by(EpubBuildJobModel.created_at.desc())
        )
        try:
            existing_job = session.execute(stmt_job.with_for_update()).scalars().first()
        except Exception:
            existing_job = session.execute(stmt_job).scalars().first()

        # Detect existing base EPUB (either versioned key or legacy full.epub on storage)
        from app.infrastructure.storage.facade import storage_repo
        legacy_candidates = [
            f"novels/{novel_id}/full.epub",
            f"{novel_id}/full.epub",
        ]
        found_base_key = novel.current_epub_key
        if found_base_key and not storage_repo.file_exists(found_base_key):
            found_base_key = None

        if not found_base_key:
            for cand in legacy_candidates:
                if storage_repo.file_exists(cand):
                    found_base_key = cand
                    novel.current_epub_key = cand
                    break

        if not found_base_key:
            try:
                import re
                def _rev_num(fn: str) -> int:
                    try:
                        m = re.match(r"^r(\d+)", os.path.basename(fn))
                        if m:
                            return int(m.group(1))
                    except Exception:
                        pass
                    return -1
                exports_files = storage_repo.list_files(f"novels/{novel_id}/exports/")
                sorted_exports = sorted(exports_files, key=_rev_num, reverse=True)
                for cand in sorted_exports:
                    if storage_repo.file_exists(cand):
                        found_base_key = cand
                        novel.current_epub_key = cand
                        break
            except Exception:
                pass

        has_existing_base = bool(found_base_key)
        is_scoped_range = bool(dirty_indexes and not force_rebuild)

        if existing_job:
            # Coalesce into existing queued job
            raw_job_dirty = existing_job.dirty_chapters
            if isinstance(raw_job_dirty, dict):
                job_dict = {str(k): int(v) for k, v in raw_job_dirty.items()}
            elif isinstance(raw_job_dirty, (list, tuple, set)):
                job_dict = {str(c): existing_job.target_revision or current_rev for c in raw_job_dirty}
            else:
                job_dict = {}

            job_dict.update(dirty_dict)
            existing_job.dirty_chapters = job_dict
            # Never downgrade an existing structural or full_rebuild job!
            if existing_job.is_structural or existing_job.strategy == "full_rebuild":
                existing_job.is_structural = True
                existing_job.strategy = "full_rebuild"
            elif (
                is_structural
                or force_rebuild
                or (novel.is_structural_dirty and not is_scoped_range)
                or not has_existing_base
                or not settings.epub_fast_patch_enabled
            ):
                existing_job.is_structural = True
                existing_job.strategy = "full_rebuild"
            else:
                existing_job.is_structural = False
                existing_job.strategy = "fast_patch"
            existing_job.target_revision = current_rev
            session.flush()
            return cls._model_to_epub_build_job(existing_job)

        # Create new job
        job_id = uuid.uuid4().hex
        needs_full = bool(
            is_structural
            or force_rebuild
            or (novel.is_structural_dirty and not is_scoped_range)
            or not has_existing_base
            or not settings.epub_fast_patch_enabled
        )
        strategy = "full_rebuild" if needs_full else "fast_patch"
        job_is_structural = bool(
            is_structural
            or force_rebuild
            or (novel.is_structural_dirty and (not is_scoped_range or strategy == "full_rebuild"))
        )

        new_job = EpubBuildJobModel(
            job_id=job_id,
            novel_id=novel_id,
            status="queued",
            strategy=strategy,
            dirty_chapters=dirty_dict,
            is_structural=job_is_structural,
            target_revision=current_rev,
            attempts=0,
            max_attempts=3,
        )
        session.add(new_job)
        session.flush()
        return cls._model_to_epub_build_job(new_job)


    EPUB_GLOBAL_BUILD_LOCK_ID = 849201948

    @classmethod
    def acquire_global_build_lock(cls, session: Session) -> bool:
        try:
            res = session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": cls.EPUB_GLOBAL_BUILD_LOCK_ID},
            ).scalar()
            return bool(res)
        except Exception as exc:
            logger.warning("Advisory lock acquisition skipped or unsupported: %s", exc)
            return True

    @classmethod
    def release_global_build_lock(cls, session: Session) -> bool:
        try:
            res = session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": cls.EPUB_GLOBAL_BUILD_LOCK_ID},
            ).scalar()
            return bool(res)
        except Exception as exc:
            logger.warning("Advisory lock release skipped or unsupported: %s", exc)
            return True

    @classmethod
    def claim_next_job(
        cls,
        session: Session,
        worker_id: str,
        lease_duration_seconds: int = 300,
    ) -> Optional[EpubBuildJobModel]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(EpubBuildJobModel)
            .where(
                or_(
                    EpubBuildJobModel.status == "queued",
                    and_(
                        EpubBuildJobModel.status == "processing",
                        EpubBuildJobModel.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(EpubBuildJobModel.created_at.asc())
        )
        try:
            locked_stmt = stmt.with_for_update(skip_locked=True)
            job = session.execute(locked_stmt).scalars().first()
        except Exception:
            job = session.execute(stmt).scalars().first()

        if not job:
            return None

        job.status = "processing"
        job.lease_token = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
        job.attempts = (job.attempts or 0) + 1

        raw_dirty = job.dirty_chapters
        if isinstance(raw_dirty, dict):
            job.claimed_dirty_chapters = {str(k): int(v) for k, v in raw_dirty.items()}
        elif isinstance(raw_dirty, (list, tuple, set)):
            job.claimed_dirty_chapters = {str(c): job.target_revision or 0 for c in raw_dirty}
        else:
            job.claimed_dirty_chapters = {}

        if not job.started_at:
            job.started_at = now
        session.flush()
        return job

    @classmethod
    def heartbeat_job(
        cls,
        session: Session,
        job_id: str,
        lease_token: str,
        lease_duration_seconds: int = 300,
    ) -> bool:
        job = session.get(EpubBuildJobModel, job_id)
        if not job or job.lease_token != lease_token or job.status != "processing":
            return False
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_duration_seconds)
        session.flush()
        return True

    @classmethod
    def complete_job(
        cls,
        session: Session,
        job_id: str,
        built_revision: int,
        epub_key: str,
        worker_id: Optional[str] = None,
        strategy: Optional[str] = None,
    ) -> bool:
        now = datetime.now(timezone.utc)
        job = session.get(EpubBuildJobModel, job_id)
        if not job or job.status != "processing":
            logger.warning("Cannot complete job %s: status is '%s', expected 'processing'", job_id, getattr(job, "status", None))
            return False

        if worker_id and job.lease_token and job.lease_token != worker_id:
            logger.warning("Cannot complete job %s: lease token mismatch (expected %s, got %s)", job_id, job.lease_token, worker_id)
            return False

        job.status = "completed"
        job.built_revision = built_revision
        job.epub_key = epub_key
        if strategy:
            job.strategy = strategy
        job.completed_at = now
        session.flush()


        stmt_novel = select(NovelModel).where(NovelModel.novel_id == job.novel_id)
        try:
            novel = session.execute(stmt_novel.with_for_update()).scalar_one_or_none()
        except Exception:
            novel = session.execute(stmt_novel).scalar_one_or_none()

        if novel:
            novel.current_epub_key = epub_key
            novel.built_revision = built_revision

            raw_claimed = job.claimed_dirty_chapters or job.dirty_chapters
            if isinstance(raw_claimed, dict):
                claimed_map = {str(k): int(v) for k, v in raw_claimed.items()}
            elif isinstance(raw_claimed, (list, tuple, set)):
                claimed_map = {str(k): job.target_revision or built_revision for k in raw_claimed}
            else:
                claimed_map = {}

            raw_novel_dirty = novel.dirty_chapters
            if isinstance(raw_novel_dirty, dict):
                novel_map = {str(k): int(v) for k, v in raw_novel_dirty.items()}
            elif isinstance(raw_novel_dirty, (list, tuple, set)):
                novel_map = {str(k): novel.desired_revision or built_revision for k in raw_novel_dirty}
            else:
                novel_map = {}

            # Retain any chapter that was modified with a revision newer than what was claimed
            remaining_map = {
                ch: rev
                for ch, rev in novel_map.items()
                if ch not in claimed_map or rev > claimed_map.get(ch, 0)
            }
            novel.dirty_chapters = remaining_map

            if job.is_structural:
                novel.is_structural_dirty = False
            session.flush()

            # If newer dirty revisions were accumulated during build, automatically trigger the next job
            if remaining_map or novel.is_structural_dirty:
                remaining_indexes = [int(k) for k in remaining_map.keys() if k.isdigit()]
                cls.mark_dirty_and_enqueue_job(
                    session=session,
                    novel_id=novel.novel_id,
                    dirty_indexes=remaining_indexes,
                    is_structural=novel.is_structural_dirty,
                )

        session.flush()
        return True



    @classmethod
    def fail_or_retry_job(
        cls,
        session: Session,
        job_id: str,
        error_message: str,
        max_attempts: int = 3,
    ) -> None:
        now = datetime.now(timezone.utc)
        job = session.get(EpubBuildJobModel, job_id)
        if not job:
            return
        if (job.attempts or 0) >= max_attempts:
            job.status = "failed"
            job.error_message = error_message
            job.completed_at = now
        else:
            job.status = "queued"
            job.error_message = f"Lần thử {job.attempts} thất bại: {error_message}"
            job.lease_token = None
            job.lease_expires_at = None
        session.flush()

    @classmethod
    def recover_stale_jobs(cls, session: Session, max_attempts: int = 3) -> int:
        now = datetime.now(timezone.utc)
        stmt = (
            select(EpubBuildJobModel)
            .where(
                EpubBuildJobModel.status == "processing",
                EpubBuildJobModel.lease_expires_at <= now,
            )
        )
        stale_jobs = session.execute(stmt).scalars().all()
        count = 0
        for job in stale_jobs:
            if (job.attempts or 0) >= max_attempts:
                job.status = "failed"
                job.error_message = "Tiến trình bị gián đoạn quá số lần cho phép (stale lease)"
                job.completed_at = now
            else:
                job.status = "queued"
                job.error_message = "Tiến trình bị gián đoạn, tự động đưa lại vào hàng đợi"
                job.lease_token = None
                job.lease_expires_at = None
            count += 1
        if count > 0:
            session.flush()
        return count

    @classmethod
    def get_epub_build_job(cls, session: Session, job_id: str) -> Optional[EpubBuildJobResponse]:
        job = session.get(EpubBuildJobModel, job_id)
        if not job:
            return None
        return cls._model_to_epub_build_job(job)

    @classmethod
    def get_epub_build_job_by_id(cls, session: Session, novel_id: str, job_id: str) -> Optional[EpubBuildJobResponse]:
        job = session.get(EpubBuildJobModel, job_id)
        if not job or job.novel_id != novel_id:
            return None
        return cls._model_to_epub_build_job(job)

    @classmethod
    def get_latest_build_job_for_novel(cls, session: Session, novel_id: str) -> Optional[EpubBuildJobResponse]:
        stmt = (
            select(EpubBuildJobModel)
            .where(EpubBuildJobModel.novel_id == novel_id)
            .order_by(
                case(
                    (EpubBuildJobModel.status == "processing", 1),
                    (EpubBuildJobModel.status == "queued", 2),
                    else_=3,
                ),
                EpubBuildJobModel.created_at.desc(),
            )
        )
        job = session.execute(stmt).scalars().first()
        if not job:
            return None
        return cls._model_to_epub_build_job(job)

    @classmethod
    def get_active_build_jobs(cls, session: Session) -> List[EpubBuildJobResponse]:
        stmt = (
            select(EpubBuildJobModel)
            .where(EpubBuildJobModel.status.in_(("processing", "queued")))
            .order_by(
                case(
                    (EpubBuildJobModel.status == "processing", 1),
                    else_=2,
                ),
                EpubBuildJobModel.created_at.asc(),
            )
        )
        jobs = session.execute(stmt).scalars().all()
        return [cls._model_to_epub_build_job(j) for j in jobs]

    @classmethod
    def update_job_progress(
        cls,
        session: Session,
        job_id: str,
        current_step: Optional[str] = None,
        current_chapter: Optional[int] = None,
        processed_chapters: Optional[int] = None,
        total_chapters: Optional[int] = None,
        progress_percentage: Optional[int] = None,
        strategy: Optional[str] = None,
    ) -> bool:
        job = session.get(EpubBuildJobModel, job_id)
        if not job or job.status in ("completed", "failed", "cancelled"):
            return False

        if current_step is not None:
            job.current_step = current_step
        if current_chapter is not None:
            job.current_chapter = current_chapter
        if processed_chapters is not None:
            job.processed_chapters = processed_chapters
        if total_chapters is not None:
            job.total_chapters = total_chapters
        if progress_percentage is not None:
            job.progress_percentage = max(0, min(100, progress_percentage))
        if strategy is not None:
            job.strategy = strategy

        session.flush()
        return True

    @classmethod
    def update_job_strategy(cls, session: Session, job_id: str, strategy: str) -> bool:
        job = session.get(EpubBuildJobModel, job_id)
        if not job or job.status in ("completed", "failed", "cancelled"):
            return False
        job.strategy = strategy
        session.flush()
        return True

    @classmethod
    def cancel_job(cls, session: Session, novel_id: str, job_id: str) -> Optional[EpubBuildJobResponse]:
        job = session.get(EpubBuildJobModel, job_id)
        if not job or job.novel_id != novel_id:
            return None

        if job.status in ("completed", "failed"):
            # Cannot cancel an already finished job
            return cls._model_to_epub_build_job(job)

        job.status = "cancelled"
        job.error_message = "Quá trình biên dịch đã bị người dùng hủy."
        job.current_step = "Đã hủy biên dịch."
        job.completed_at = datetime.now(timezone.utc)
        session.flush()
        return cls._model_to_epub_build_job(job)

    @classmethod
    def is_job_cancelled(cls, session: Session, job_id: str) -> bool:
        job = session.get(EpubBuildJobModel, job_id)
        if not job:
            return True
        return job.status == "cancelled"



