import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.infrastructure.storage.facade import storage_repo
from app.llm import create_llm_client
from app.schemas.book_bible import BookBible
from app.schemas.translation import InputType, JobStatusEnum, TranslationJob
from app.modules.book_bible.application.facade import BookBibleService
from app.modules.book_bible.domain.address_resolver import AddressRuleResolver
from app.modules.translation.application.facade import TranslationPipelineService
from app.infrastructure.cache.direct_translation import DirectTranslationCache
from app.config import settings
from app.api.dependencies import require_write_access
from app.api.uploads import read_upload_limited
from app.infrastructure.jobs import limited_background_work

router = APIRouter(prefix="/translate", tags=["Translation"])
direct_translation_cache = DirectTranslationCache()
logger = logging.getLogger("EpubBackend.TranslationAPI")
_running_jobs: set[str] = set()

STORAGE_BASE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "storage"
)
UPLOAD_DIR = os.path.join(STORAGE_BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(STORAGE_BASE_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


class DirectTextRequest(BaseModel):
    text: str = Field(...)
    novel_id: Optional[str] = None
    chapter_index: Optional[int] = Field(default=None, ge=0)
    chapter_id: Optional[str] = None
    api_key: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class AddressResolutionResponse(BaseModel):
    applied_observation_ids: list[str] = Field(default_factory=list)
    pending_change_ids: list[str] = Field(default_factory=list)
    has_uncertainty: bool = False


class DirectTextResponse(BaseModel):
    translated_text: str
    book_bible: BookBible
    address_resolution: AddressResolutionResponse


@limited_background_work
async def run_translation_background_job(
    job_id: str,
    input_file_path: str,
    output_file_path: str,
    input_type: InputType,
    api_key: Optional[str] = None,
    provider: str = "gemini",
    model: Optional[str] = None,
    novel_id: Optional[str] = None,
    chapter_index: Optional[int] = None,
    chapter_id: Optional[str] = None,
):
    job = storage_repo.get_job(job_id)
    if not job:
        return

    _running_jobs.add(job_id)
    job.status = JobStatusEnum.PROCESSING
    job.provider = provider
    job.model = model
    job.novel_id = novel_id or job.novel_id
    job.chapter_index = chapter_index or job.chapter_index
    job.chapter_id = chapter_id or job.chapter_id
    storage_repo.save_job(job)
    last_persisted_progress = -1.0
    last_persisted_step = ""
    try:
        llm_client = create_llm_client(provider=provider, api_key=api_key, model=model)
        pipeline = TranslationPipelineService(llm_client)

        def progress_callback(pct: float, step: str):
            nonlocal last_persisted_progress, last_persisted_step
            job.progress_percentage = round(pct, 2)
            job.current_step = step
            if (
                job.progress_percentage - last_persisted_progress >= 1.0
                or step != last_persisted_step
            ):
                storage_repo.save_job(job)
                last_persisted_progress = job.progress_percentage
                last_persisted_step = step

        def on_bible_updated(updated_bible: BookBible):
            storage_repo.save_bible(job_id, updated_bible)

        bible = BookBible(novel_id=novel_id or "default")
        if input_type == InputType.TXT:
            bible = await pipeline.translate_txt_file(
                input_path=input_file_path,
                output_path=output_file_path,
                bible=bible,
                progress_callback=progress_callback,
                on_bible_updated=on_bible_updated,
                chapter_index_offset=chapter_index or 0,
                chapter_id_prefix=chapter_id or "txt",
            )
        elif input_type == InputType.EPUB:
            bible = await pipeline.translate_epub_file(
                input_path=input_file_path,
                output_path=output_file_path,
                bible=bible,
                progress_callback=progress_callback,
                on_bible_updated=on_bible_updated,
                chapter_index_offset=chapter_index or 0,
                chapter_id_prefix=chapter_id or "epub",
            )
        else:
            raise ValueError(f"Input type {input_type} currently unsupported.")

        storage_repo.save_bible(job_id, bible)
        job.status = JobStatusEnum.COMPLETED
        job.translated_file_path = output_file_path
        if storage_repo.is_blob_active or storage_repo.r2_enabled:
            try:
                storage_key = f"outputs/{job_id}_{job.filename}"
                link = storage_repo.upload_file(output_file_path, storage_key)
                job.r2_url = link
                logger.info("Uploaded output to Storage: %s", link)
            except Exception as store_err:
                logger.warning("Failed to upload output to Storage: %s", store_err)
        job.completed_at = datetime.now(timezone.utc).isoformat()
        job.progress_percentage = 100.0
        job.current_step = "Hoan thanh"
        storage_repo.save_job(job)
    except Exception as exc:
        job.status = JobStatusEnum.FAILED
        job.error_message = str(exc)
        job.current_step = f"Loi: {exc}"
        storage_repo.save_job(job)
    finally:
        _running_jobs.discard(job_id)


def recover_pending_translation_jobs() -> None:
    """Resume jobs whose persisted state survived a process restart."""
    for job in storage_repo.list_jobs():
        if job.status not in {JobStatusEnum.PENDING, JobStatusEnum.PROCESSING}:
            continue
        extension = os.path.splitext(job.filename or "novel.txt")[1].lower() or ".txt"
        input_path = os.path.join(UPLOAD_DIR, f"{job.job_id}{extension}")
        output_path = job.translated_file_path or os.path.join(
            OUTPUT_DIR, f"translated_{job.job_id}{extension}"
        )
        if not os.path.exists(input_path):
            job.status = JobStatusEnum.FAILED
            job.error_message = "Input file is unavailable after process restart."
            job.current_step = "Khong the khoi phuc job sau khi khoi dong lai"
            storage_repo.save_job(job)
            continue
        if job.job_id in _running_jobs:
            continue
        asyncio.create_task(
            run_translation_background_job(
                job.job_id,
                input_path,
                output_path,
                job.input_type,
                provider=job.provider or settings.default_provider,
                model=job.model,
                novel_id=job.novel_id or None,
                chapter_index=job.chapter_index or None,
                chapter_id=job.chapter_id,
            )
        )


@router.post("/text", response_model=DirectTextResponse)
async def translate_text_direct_endpoint(
    req: DirectTextRequest,
    x_api_key: Optional[str] = Header(default=None),
    x_provider: Optional[str] = Header(default=None),
    x_model: Optional[str] = Header(default=None),
    _: None = Depends(require_write_access),
):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Van ban goc khong duoc rong.")
    if len(req.text) > settings.max_text_input_chars:
        raise HTTPException(status_code=413, detail="Van ban vuot qua gioi han cho phep.")

    key = req.api_key or x_api_key
    prov = req.provider or x_provider or settings.default_provider
    mod = req.model or x_model

    try:
        detected_novel_id = None
        if not req.novel_id:
            existing_bibles = storage_repo.list_bibles()
            detected_novel_id = BookBibleService.detect_novel_id(req.text, existing_bibles)

        novel_key = req.novel_id or detected_novel_id or "default_novel"
        existing_bible = storage_repo.get_bible(novel_key)
        if not existing_bible:
            existing_bible = BookBible(novel_id=novel_key)

        source_revision = existing_bible.bible_revision
        cached = direct_translation_cache.get(
            novel_id=novel_key,
            text=req.text,
            chapter_index=req.chapter_index,
            chapter_id=req.chapter_id,
            provider=prov,
            model=mod,
            current_bible_revision=source_revision,
        )
        if cached:
            translated_text = cached["translated_text"]
            updated_bible = BookBible.model_validate(cached["book_bible"])
            logger.info(
                "[CACHE] direct-text returned without AI novel=%s chapter=%s",
                novel_key,
                req.chapter_id or req.chapter_index,
            )
        else:
            llm_client = create_llm_client(provider=prov, api_key=key, model=mod)
            pipeline = TranslationPipelineService(llm_client)
            translated_text, updated_bible = await pipeline.translate_direct_text(
                req.text,
                existing_bible,
                chapter_index=req.chapter_index,
                chapter_id=req.chapter_id,
            )
            storage_repo.save_bible(novel_key, updated_bible)
            direct_translation_cache.put(
                novel_id=novel_key,
                text=req.text,
                chapter_index=req.chapter_index,
                chapter_id=req.chapter_id,
                provider=prov,
                model=mod,
                source_bible_revision=source_revision,
                translated_text=translated_text,
                book_bible=updated_bible,
            )

        job_id = str(uuid.uuid4())
        short_snippet = req.text.strip().replace("\n", " ")[:30]
        filename = f"PasteText_{short_snippet}.txt"
        output_file_path = os.path.join(OUTPUT_DIR, f"translated_{job_id}.txt")
        with open(output_file_path, "w", encoding="utf-8") as output_file:
            output_file.write(translated_text)

        now = datetime.now(timezone.utc).isoformat()
        job = TranslationJob(
            job_id=job_id,
            filename=filename,
            input_type=InputType.TXT,
            status=JobStatusEnum.COMPLETED,
            progress_percentage=100,
            current_step="Da dich truc tiep thanh cong",
            translated_file_path=output_file_path,
            created_at=now,
            completed_at=now,
            provider=prov,
            model=mod,
            novel_id=novel_key,
            chapter_index=req.chapter_index or 0,
            chapter_id=req.chapter_id,
        )
        storage_repo.save_job(job)

        resolution = AddressRuleResolver.resolve(updated_bible, req.chapter_index)
        return DirectTextResponse(
            translated_text=translated_text,
            book_bible=updated_bible,
            address_resolution=AddressResolutionResponse(**resolution),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Loi khi dich AI: {exc}")

@router.post("/file", response_model=TranslationJob)
async def translate_file_endpoint(
    file: UploadFile = File(...),
    novel_id: Optional[str] = Form(default=None),
    chapter_index: Optional[int] = Form(default=None),
    chapter_id: Optional[str] = Form(default=None),
    x_api_key: Optional[str] = Header(default=None),
    x_provider: Optional[str] = Header(default=None),
    x_model: Optional[str] = Header(default=None),
    _: None = Depends(require_write_access),
):
    filename = file.filename or "novel.txt"
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".txt":
        input_type = InputType.TXT
    elif ext == ".epub":
        input_type = InputType.EPUB
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chi ho tro file .txt hoac .epub",
        )

    job_id = str(uuid.uuid4())
    input_file_path = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
    output_file_path = os.path.join(OUTPUT_DIR, f"translated_{job_id}{ext}")
    content = await read_upload_limited(file)
    with open(input_file_path, "wb") as output_file:
        output_file.write(content)

    job = TranslationJob(
        job_id=job_id,
        filename=filename,
        input_type=input_type,
        status=JobStatusEnum.PENDING,
        created_at=datetime.now(timezone.utc).isoformat(),
        provider=x_provider or settings.default_provider,
        model=x_model,
        novel_id=novel_id or "",
        chapter_index=chapter_index or 0,
        chapter_id=chapter_id,
    )
    storage_repo.save_job(job)
    asyncio.create_task(
        run_translation_background_job(
            job_id,
            input_file_path,
            output_file_path,
            input_type,
            api_key=x_api_key,
            provider=x_provider or settings.default_provider,
            model=x_model,
            novel_id=novel_id,
            chapter_index=chapter_index,
            chapter_id=chapter_id,
        )
    )
    return job


@router.get("/job/{job_id}", response_model=TranslationJob)
def get_job_status(job_id: str):
    job = storage_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Khong tim thay Job ID nay.")
    return job


@router.get("/download/{job_id}")
def download_translated_file(job_id: str):
    job = storage_repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Khong tim thay Job ID nay.")
    if job.status != JobStatusEnum.COMPLETED or not job.translated_file_path:
        raise HTTPException(status_code=400, detail="Job chua hoan thanh hoac file khong ton tai.")
    if job.r2_url:
        return RedirectResponse(url=job.r2_url, status_code=307)
    if not os.path.exists(job.translated_file_path):
        raise HTTPException(status_code=404, detail="File dich khong con tren server.")
    return FileResponse(
        path=job.translated_file_path,
        filename=f"dich_{job.filename}",
        media_type="application/octet-stream",
    )



