import json
from urllib.parse import quote
from typing import List, Optional
from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from app.config import settings

from app.schemas.book_bible import BookBible
from app.schemas.library import (
    ChapterCreateRequest,
    ChapterItem,
    ChapterTranslateRequest,
    ImportJobStatus,
    NovelCreateRequest,
    NovelMetadata,
    NovelSummary,
    NovelUpdateRequest,
)
from app.services.library_service import library_service

router = APIRouter(prefix="/library", tags=["Novel Library"])


@router.post("/novels", response_model=NovelMetadata)
async def create_novel_endpoint(
    title: str = Form(..., description="Tên bộ truyện"),
    original_title: Optional[str] = Form(default="", description="Tên gốc"),
    author: Optional[str] = Form(default="", description="Tác giả"),
    genre: Optional[str] = Form(default="", description="Thể loại (phân cách bằng dấu phẩy)"),
    description: Optional[str] = Form(default="", description="Tóm tắt truyện"),
    novel_id: Optional[str] = Form(default=None, description="Slug định danh"),
    cover: Optional[UploadFile] = File(default=None, description="Ảnh bìa truyện"),
):
    genre_list = [g.strip() for g in genre.split(",") if g.strip()] if genre else []
    cover_data = None
    cover_filename = None
    if cover:
        cover_data = await cover.read()
        cover_filename = cover.filename

    req = NovelCreateRequest(
        title=title,
        original_title=original_title or "",
        author=author or "",
        genre=genre_list,
        description=description or "",
        novel_id=novel_id,
    )
    return library_service.create_novel(req, cover_data=cover_data, cover_filename=cover_filename)


@router.post("/novels/import-epub", response_model=ImportJobStatus)
async def import_epub_endpoint(
    file: UploadFile = File(..., description="File EPUB trọn bộ hoặc phần bổ sung"),
    is_translated: bool = Form(default=True, description="True nếu là sách đã dịch sẵn, False nếu là sách raw chưa dịch"),
    novel_id: Optional[str] = Form(default=None, description="Mã định danh slug (tùy chọn)"),
    start_chapter_index: Optional[int] = Form(default=None, description="Số thứ tự chương bắt đầu nếu upload file partial (vd: 101)"),
    force_overwrite: bool = Form(default=False, description="Ghi đè nội dung phiên bản nếu chương đã tồn tại"),
    auto_scan_characters: bool = Form(default=False, description="Tự động quét và lập Book Bible ban đầu"),
    x_api_key: Optional[str] = Header(default=None),
    x_provider: Optional[str] = Header(default=None),
    x_model: Optional[str] = Header(default=None),
):
    if not file.filename.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Vui lòng tải lên file định dạng .epub")

    epub_bytes = await file.read()
    try:
        return library_service.start_import_epub_async(
            epub_bytes=epub_bytes,
            filename=file.filename,
            is_translated=is_translated,
            novel_id=novel_id,
            start_chapter_index=start_chapter_index,
            force_overwrite=force_overwrite,
            auto_scan_characters=auto_scan_characters,
            provider=x_provider,
            api_key=x_api_key,
            model=x_model,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi bắt đầu nhập file EPUB: {exc}")


@router.get("/import-jobs/{job_id}", response_model=ImportJobStatus)
def get_import_job_endpoint(job_id: str):
    job = library_service.get_import_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy tiến trình upload này.")
    return job


@router.get("/novels/{novel_id}/bible", response_model=BookBible)
def get_novel_bible_endpoint(novel_id: str):
    return library_service.get_novel_bible(novel_id)


@router.post("/novels/{novel_id}/scan-characters", response_model=BookBible)
async def scan_characters_endpoint(
    novel_id: str,
    max_chapters: int = Query(default=5, ge=1, le=20, description="Số chương đầu cần quét để trích xuất nhân vật"),
    x_api_key: Optional[str] = Header(default=None),
    x_provider: Optional[str] = Header(default=None),
    x_model: Optional[str] = Header(default=None),
):
    try:
        return await library_service.scan_characters_and_timeline(
            novel_id=novel_id,
            max_chapters=max_chapters,
            provider=x_provider,
            api_key=x_api_key,
            model=x_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi quét nhân vật: {exc}")


@router.get("/novels", response_model=List[NovelSummary])
def list_novels_endpoint():
    return library_service.list_novels()


@router.get("/novels/{novel_id}", response_model=NovelMetadata)
def get_novel_endpoint(novel_id: str):
    novel = library_service.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ truyện này.")
    return novel


@router.put("/novels/{novel_id}", response_model=NovelMetadata)
def update_novel_endpoint(novel_id: str, req: NovelUpdateRequest):
    novel = library_service.update_novel(novel_id, req)
    if not novel:
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ truyện này.")
    return novel


@router.delete("/novels/{novel_id}")
def delete_novel_endpoint(novel_id: str):
    success = library_service.delete_novel(novel_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ truyện này.")
    return {"message": f"Đã xóa thành công bộ truyện '{novel_id}' khỏi kho."}


@router.get("/novels/{novel_id}/missing-chapters")
def check_missing_chapters_endpoint(
    novel_id: str,
    expected_total: Optional[int] = Query(default=None, description="Tổng số chương dự kiến (ví dụ: 150)"),
    include_detail: bool = Query(default=False, description="Bao gồm chi tiết trạng thái từng chương"),
):
    """
    Kiểm tra các chương đã có trong kho và danh sách các chương còn thiếu theo từng phiên bản (Original & Translated).
    Giúp Client quyết định chính xác cần upload bù bản dịch hay bản raw.
    """
    novel = library_service.get_novel(novel_id)
    if not novel:
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ truyện này.")

    existing_indices = {ch.chapter_index for ch in novel.chapters}
    max_idx = max(existing_indices) if existing_indices else 0
    target_total = expected_total or max_idx

    has_orig_indices = {ch.chapter_index for ch in novel.chapters if ch.r2_original_key or (ch.status == ChapterStatus.NOT_TRANSLATED and ch.word_count > 0)}
    has_trans_indices = {ch.chapter_index for ch in novel.chapters if ch.r2_translated_key or ch.status == ChapterStatus.COMPLETED}

    missing_orig = [i for i in range(1, target_total + 1) if i not in has_orig_indices]
    missing_trans = [i for i in range(1, target_total + 1) if i not in has_trans_indices]

    chapters_detail = None
    if include_detail:
        chapters_detail = [
            {
                "index": ch.chapter_index,
                "title": ch.chapter_title,
                "has_original": ch.chapter_index in has_orig_indices,
                "has_translated": ch.chapter_index in has_trans_indices,
                "status": ch.status,
            }
            for ch in sorted(novel.chapters, key=lambda c: c.chapter_index)
        ]

    return {
        "novel_id": novel_id,
        "title": novel.title,
        "total_chapters_recorded": len(existing_indices),
        "max_chapter_index": max_idx,
        "existing_original_count": len(has_orig_indices),
        "existing_translated_count": len(has_trans_indices),
        "missing_original_indices": missing_orig,
        "missing_translated_indices": missing_trans,
        "chapters_detail": chapters_detail,
    }




@router.post("/novels/{novel_id}/chapters", response_model=ChapterItem)
async def add_chapter_endpoint(
    novel_id: str,
    chapter_index: int = Form(...),
    chapter_title: str = Form(...),
    content: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    text_content = ""
    if file:
        raw_bytes = await file.read()
        text_content = raw_bytes.decode("utf-8", errors="ignore")
    elif content:
        text_content = content
    else:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp nội dung chương hoặc tải lên file TXT.")

    try:
        return library_service.add_or_update_chapter(
            novel_id=novel_id,
            chapter_index=chapter_index,
            chapter_title=chapter_title,
            content=text_content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/novels/{novel_id}/chapters/{chapter_index}/content")
def get_chapter_content_endpoint(
    novel_id: str,
    chapter_index: int,
    version: str = Query(default="translated", description="'original' hoặc 'translated'"),
):
    content = library_service.get_chapter_content(novel_id, chapter_index, version=version)
    if content is None:
        raise HTTPException(status_code=404, detail="Nội dung chương chưa tồn tại.")
    return {"novel_id": novel_id, "chapter_index": chapter_index, "version": version, "content": content}


@router.get("/novels/{novel_id}/chapters/{chapter_index}/character-snapshot")
def get_chapter_character_snapshot_endpoint(novel_id: str, chapter_index: int):
    try:
        return library_service.get_character_snapshot_at_chapter(novel_id, chapter_index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/novels/{novel_id}/chapters/{chapter_index}/translate", response_model=ChapterItem)
async def translate_chapter_endpoint(
    novel_id: str,
    chapter_index: int,
    req: ChapterTranslateRequest = ChapterTranslateRequest(),
    x_api_key: Optional[str] = Header(default=None),
    x_provider: Optional[str] = Header(default=None),
    x_model: Optional[str] = Header(default=None),
):
    key = req.api_key or x_api_key
    prov = req.provider or x_provider or "gemini"
    mod = req.model or x_model

    try:
        return await library_service.translate_chapter(
            novel_id=novel_id,
            chapter_index=chapter_index,
            provider=prov,
            api_key=key,
            model=mod,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi trong quá trình dịch chương: {exc}")


@router.get("/novels/{novel_id}/export/epub")
def export_novel_epub_endpoint(novel_id: str):
    try:
        # 1. If public CDN URL is configured, redirect directly to Cloudflare CDN (Fastest, 0ms latency)
        if settings.cloudflare_r2_public_url:
            cdn_url = f"{settings.cloudflare_r2_public_url.rstrip('/')}/novels/{novel_id}/full.epub"
            return RedirectResponse(url=cdn_url, status_code=307)

        # 2. Local fallback
        output_path = library_service.export_full_epub(novel_id)
        meta = library_service.get_novel(novel_id)
        title = meta.title if meta else novel_id
        safe_ascii_name = f"{novel_id}_vi.epub"
        encoded_name = quote(f"{title}.epub")

        headers = {
            "Content-Disposition": f"attachment; filename=\"{safe_ascii_name}\"; filename*=UTF-8''{encoded_name}"
        }
        return FileResponse(
            path=output_path,
            media_type="application/epub+zip",
            headers=headers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xuất file EPUB: {exc}")
