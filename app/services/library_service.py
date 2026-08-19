import io
import json
import logging
import os
import re
import tempfile
import threading
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import ebooklib
from ebooklib import epub

from app.config import settings
from app.core.storage import storage_repo
from app.llm.factory import create_llm_client
from app.schemas.book_bible import BookBible
from app.schemas.library import (
    ChapterCreateRequest,
    ChapterItem,
    ChapterStatus,
    ImportJobStatus,
    NovelCreateRequest,
    NovelMetadata,
    NovelStatus,
    NovelSummary,
    NovelUpdateRequest,
)
from app.services.pipeline_service import TranslationPipelineService

logger = logging.getLogger("EpubBackend.LibraryService")


def slugify(text: str) -> str:
    """Chuyển chuỗi tiếng Việt thành slug URL an toàn"""
    # Thay thế chữ Đ/đ trước vì Unicode NFKD không tự tách chữ Đ thành d + dấu
    text = text.replace("đ", "d").replace("Đ", "d")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text) or "novel"



class LibraryService:
    """Dịch vụ quản lý Kho Truyện (Novel Library) lưu trữ trên Cloudflare R2 / Local."""

    def __init__(self):
        self._cache: Dict[str, NovelMetadata] = {}
        self._import_jobs: Dict[str, ImportJobStatus] = {}

    def _novel_meta_key(self, novel_id: str) -> str:
        return f"novels/{novel_id}/metadata.json"

    def _chapter_key(self, novel_id: str, chapter_index: int, is_translated: bool = False) -> str:
        folder = "translated" if is_translated else "original"
        return f"novels/{novel_id}/{folder}/ch_{chapter_index:04d}.txt"

    def _cover_key(self, novel_id: str, extension: str = "jpg") -> str:
        return f"novels/{novel_id}/cover.{extension}"

    # ------------------------------------------------------------------
    # Novel Management
    # ------------------------------------------------------------------
    def create_novel(
        self,
        req: NovelCreateRequest,
        cover_data: Optional[bytes] = None,
        cover_filename: Optional[str] = None,
        overwrite: bool = True,
    ) -> NovelMetadata:
        novel_id = (req.novel_id or slugify(req.title)).strip().lower()
        existing = self.get_novel(novel_id)

        cover_url = existing.cover_url if existing else None
        if cover_data:
            ext = cover_filename.split(".")[-1].lower() if cover_filename and "." in cover_filename else "jpg"
            cover_key = self._cover_key(novel_id, ext)
            cover_url = self._save_raw_file(cover_key, cover_data, content_type=f"image/{ext}")

        now = datetime.utcnow().isoformat()
        if existing and overwrite:
            # Cập nhật thông tin truyện hiện có mà không sinh thêm novel_id đuôi timestamp mới
            existing.title = req.title or existing.title
            existing.original_title = req.original_title or existing.original_title
            existing.author = req.author or existing.author
            if req.genre:
                existing.genre = req.genre
            if req.description:
                existing.description = req.description
            if cover_url:
                existing.cover_url = cover_url
            existing.updated_at = now
            self._save_metadata(existing)
            self._cache[novel_id] = existing
            return existing

        metadata = NovelMetadata(
            novel_id=novel_id,
            title=req.title,
            original_title=req.original_title or "",
            author=req.author or "",
            genre=req.genre or [],
            description=req.description or "",
            cover_url=cover_url,
            status=NovelStatus.ONGOING,
            total_chapters=0,
            translated_chapters=0,
            created_at=now,
            updated_at=now,
            chapters=[],
        )

        self._save_metadata(metadata)
        self._cache[novel_id] = metadata

        # Khởi tạo Book Bible ban đầu cho bộ truyện nếu chưa có
        if not storage_repo.get_bible(novel_id):
            storage_repo.save_bible(novel_id, BookBible(novel_id=novel_id))

        return metadata

    def get_novel(self, novel_id: str) -> Optional[NovelMetadata]:
        if novel_id in self._cache:
            return self._cache[novel_id]

        meta_key = self._novel_meta_key(novel_id)
        data = None
        if storage_repo.is_r2_active:
            data = storage_repo._r2_get_json(meta_key)

        if not data:
            # Check local file fallback
            local_meta = os.path.join("storage", "novels", novel_id, "metadata.json")
            if os.path.exists(local_meta):
                try:
                    with open(local_meta, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as exc:
                    logger.warning("Failed to load local novel meta: %s", exc)

        if data:
            meta = NovelMetadata.model_validate(data)
            self._cache[novel_id] = meta
            return meta
        return None

    def list_novels(self) -> List[NovelSummary]:
        summaries: Dict[str, NovelSummary] = {}

        if storage_repo.is_r2_active:
            raw_objects = storage_repo._r2_list_json_objects("novels/")
            for raw in raw_objects:
                if "novel_id" in raw and "title" in raw and "chapters" in raw:
                    try:
                        meta = NovelMetadata.model_validate(raw)
                        self._cache[meta.novel_id] = meta
                        summaries[meta.novel_id] = NovelSummary(
                            novel_id=meta.novel_id,
                            title=meta.title,
                            original_title=meta.original_title,
                            author=meta.author,
                            genre=meta.genre,
                            description=meta.description,
                            cover_url=meta.cover_url,
                            status=meta.status,
                            total_chapters=len(meta.chapters),
                            translated_chapters=sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED),
                            created_at=meta.created_at,
                            updated_at=meta.updated_at,
                        )
                    except Exception as err:
                        logger.warning("Error parsing novel summary: %s", err)

        # Fallback / merge with local cache
        for nid, meta in self._cache.items():
            if nid not in summaries:
                summaries[nid] = NovelSummary(
                    novel_id=meta.novel_id,
                    title=meta.title,
                    original_title=meta.original_title,
                    author=meta.author,
                    genre=meta.genre,
                    description=meta.description,
                    cover_url=meta.cover_url,
                    status=meta.status,
                    total_chapters=len(meta.chapters),
                    translated_chapters=sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED),
                    created_at=meta.created_at,
                    updated_at=meta.updated_at,
                )

        return list(summaries.values())

    def update_novel(self, novel_id: str, req: NovelUpdateRequest) -> Optional[NovelMetadata]:
        meta = self.get_novel(novel_id)
        if not meta:
            return None

        if req.title is not None:
            meta.title = req.title
        if req.original_title is not None:
            meta.original_title = req.original_title
        if req.author is not None:
            meta.author = req.author
        if req.genre is not None:
            meta.genre = req.genre
        if req.description is not None:
            meta.description = req.description
        if req.status is not None:
            meta.status = req.status

        meta.updated_at = datetime.utcnow().isoformat()
        self._save_metadata(meta)
        self._cache[novel_id] = meta
        return meta

    def delete_novel(self, novel_id: str) -> bool:
        meta = self.get_novel(novel_id)
        if not meta:
            return False

        if storage_repo.is_r2_active:
            try:
                paginator = storage_repo.r2_client.get_paginator("list_objects_v2")
                prefix = f"novels/{novel_id}/"
                for page in paginator.paginate(Bucket=settings.cloudflare_r2_bucket_name, Prefix=prefix):
                    objects_to_delete = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                    if objects_to_delete:
                        storage_repo.r2_client.delete_objects(
                            Bucket=settings.cloudflare_r2_bucket_name,
                            Delete={"Objects": objects_to_delete},
                        )
            except Exception as exc:
                logger.warning("Error deleting novel files from R2: %s", exc)

        # Xóa luôn Book Bible liên quan đến bộ truyện
        storage_repo.delete_bible(novel_id)

        # Xóa local metadata file nếu có
        try:
            local_meta = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "storage", "novels", novel_id)
            if os.path.exists(local_meta):
                import shutil
                shutil.rmtree(local_meta, ignore_errors=True)
        except Exception:
            pass

        self._cache.pop(novel_id, None)
        return True

    # ------------------------------------------------------------------
    # Chapter Management
    # ------------------------------------------------------------------
    def add_or_update_chapter(
        self,
        novel_id: str,
        chapter_index: int,
        chapter_title: str,
        content: str,
    ) -> ChapterItem:
        meta = self.get_novel(novel_id)
        if not meta:
            raise ValueError(f"Không tìm thấy bộ truyện '{novel_id}'")

        ch_id = f"ch_{chapter_index:04d}"
        orig_key = self._chapter_key(novel_id, chapter_index, is_translated=False)
        self._save_raw_file(orig_key, content.encode("utf-8"), content_type="text/plain; charset=utf-8")

        word_count = len(content.split())
        preview = (content[:150] + "...") if len(content) > 150 else content

        # Check if chapter already exists in index
        existing_item = next((c for c in meta.chapters if c.chapter_index == chapter_index), None)
        if existing_item:
            existing_item.chapter_title = chapter_title
            existing_item.word_count = word_count
            existing_item.original_text_preview = preview
            existing_item.updated_at = datetime.utcnow().isoformat()
            chapter_item = existing_item
        else:
            chapter_item = ChapterItem(
                chapter_index=chapter_index,
                chapter_id=ch_id,
                chapter_title=chapter_title,
                status=ChapterStatus.NOT_TRANSLATED,
                word_count=word_count,
                original_text_preview=preview,
                translated_text_preview="",
                updated_at=datetime.utcnow().isoformat(),
                r2_original_key=orig_key,
                r2_translated_key="",
            )
            meta.chapters.append(chapter_item)

        meta.chapters.sort(key=lambda x: x.chapter_index)
        meta.total_chapters = len(meta.chapters)
        meta.translated_chapters = sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED)
        meta.updated_at = datetime.utcnow().isoformat()

        self._save_metadata(meta)
        self._cache[novel_id] = meta
        return chapter_item

    def get_chapter_content(
        self,
        novel_id: str,
        chapter_index: int,
        version: str = "translated",
    ) -> Optional[str]:
        is_trans = (version.lower() == "translated")
        key = self._chapter_key(novel_id, chapter_index, is_translated=is_trans)

        if storage_repo.is_r2_active:
            try:
                resp = storage_repo.r2_client.get_object(
                    Bucket=settings.cloudflare_r2_bucket_name,
                    Key=key,
                )
                return resp["Body"].read().decode("utf-8")
            except Exception as exc:
                logger.debug("Chapter content not found on R2: %s", exc)

        local_path = os.path.join("storage", key)
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                return f.read()

        return None

    async def translate_chapter(
        self,
        novel_id: str,
        chapter_index: int,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ChapterItem:
        meta = self.get_novel(novel_id)
        if not meta:
            raise ValueError(f"Không tìm thấy bộ truyện '{novel_id}'")

        chapter = next((c for c in meta.chapters if c.chapter_index == chapter_index), None)
        if not chapter:
            raise ValueError(f"Không tìm thấy chương {chapter_index} của truyện '{novel_id}'")

        orig_content = self.get_chapter_content(novel_id, chapter_index, version="original")
        if not orig_content or not orig_content.strip():
            raise ValueError(f"Nội dung chương gốc {chapter_index} đang trống.")

        chapter.status = ChapterStatus.TRANSLATING
        self._save_metadata(meta)

        try:
            # Khởi tạo LLM và pipeline
            llm_client = create_llm_client(provider=provider, api_key=api_key, model=model)
            pipeline = TranslationPipelineService(llm_client)

            # Lấy Book Bible hiện tại của truyện
            bible = storage_repo.get_bible(novel_id) or BookBible(novel_id=novel_id)

            # Trích xuất delta & cập nhật bible
            delta = await llm_client.extract_book_bible_delta(orig_content[:3000])
            bible = storage_repo.merge_bible_delta(novel_id, delta, default_novel_id=novel_id)

            # Dịch nội dung chương
            filtered_bible = bible.filter_for_text(orig_content)
            translated_text = await llm_client.translate_prose_chunk(
                text=orig_content,
                bible=filtered_bible,
            )

            # Lưu bản dịch lên R2
            trans_key = self._chapter_key(novel_id, chapter_index, is_translated=True)
            trans_url = self._save_raw_file(trans_key, translated_text.encode("utf-8"), content_type="text/plain; charset=utf-8")

            chapter.status = ChapterStatus.COMPLETED
            chapter.r2_translated_key = trans_key
            chapter.r2_translated_url = trans_url
            chapter.translated_text_preview = (translated_text[:150] + "...") if len(translated_text) > 150 else translated_text
            chapter.updated_at = datetime.utcnow().isoformat()

            meta.translated_chapters = sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED)
            meta.updated_at = datetime.utcnow().isoformat()
            self._save_metadata(meta)
            self._cache[novel_id] = meta
            return chapter

        except Exception as exc:
            logger.error("Lỗi khi dịch chương %d: %s", chapter_index, exc)
            chapter.status = ChapterStatus.FAILED
            self._save_metadata(meta)
            raise

    async def scan_characters_and_timeline(
        self,
        novel_id: str,
        max_chapters: int = 5,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> BookBible:
        meta = self.get_novel(novel_id)
        if not meta:
            raise ValueError(f"Không tìm thấy bộ truyện '{novel_id}'")

        if not meta.chapters:
            raise ValueError(f"Bộ truyện '{novel_id}' chưa có chương nào để quét nhân vật.")

        llm_client = create_llm_client(provider=provider, api_key=api_key, model=model)
        bible = storage_repo.get_bible(novel_id) or BookBible(novel_id=novel_id)

        chapters_to_scan = meta.chapters[:max_chapters]
        for ch in chapters_to_scan:
            content = self.get_chapter_content(novel_id, ch.chapter_index, version="original")
            if not content:
                content = self.get_chapter_content(novel_id, ch.chapter_index, version="translated")
            if not content:
                continue

            delta = await llm_client.extract_book_bible_delta(content[:4000])
            bible = storage_repo.merge_bible_delta(novel_id, delta, default_novel_id=novel_id)

        return bible

    def get_novel_bible(self, novel_id: str) -> BookBible:
        return storage_repo.get_bible(novel_id) or BookBible(novel_id=novel_id)

    def get_character_snapshot_at_chapter(self, novel_id: str, chapter_index: int) -> Dict[str, Any]:
        """Lấy hồ sơ trạng thái nhân vật (cảnh giới, pháp bảo, đồ đạc, công pháp) tại một chương cụ thể."""
        meta = self.get_novel(novel_id)
        if not meta:
            raise ValueError(f"Không tìm thấy bộ truyện '{novel_id}'")

        bible = storage_repo.get_bible(novel_id) or BookBible(novel_id=novel_id)

        characters_list = []
        for char in bible.characters:
            c_info = {
                "character_id": char.character_id or char.original_name,
                "original_name": char.original_name,
                "vi_name": char.vi_name,
                "role": char.role or "Nhân vật",
                "realm": char.voice_notes or "Chưa rõ cảnh giới",
                "items": [],
                "skills": [],
                "faction": "",
                "address_terms": [
                    f"{a.self_term} / {a.other_term} (với {a.with_person})"
                    for a in char.address_terms
                ],
            }
            characters_list.append(c_info)

        # Categorize terms
        items = [f"{t.original_name} → {t.vi_name}" for t in bible.terms if any(k in t.category.lower() for k in ["item", "pháp bảo", "bảo vật", "vũ khí", "đan dược", "vật phẩm"])]
        skills = [f"{t.original_name} → {t.vi_name}" for t in bible.terms if any(k in t.category.lower() for k in ["skill", "công pháp", "võ kỹ", "bí thuật", "chiêu thức"])]
        places = [f"{p.original_name} → {p.vi_name}" for p in bible.places]

        # Attach to main character if available
        if characters_list:
            if items:
                characters_list[0]["items"] = items
            if skills:
                characters_list[0]["skills"] = skills

        return {
            "novel_id": novel_id,
            "novel_title": meta.title,
            "chapter_index": chapter_index,
            "characters": characters_list,
            "inventory_items": items or [f"{t.original_name} → {t.vi_name}" for t in bible.terms[:5]],
            "skills": skills or [],
            "known_places": places,
        }

    # ------------------------------------------------------------------
    # EPUB Export
    # ------------------------------------------------------------------
    def export_full_epub(self, novel_id: str, output_path: Optional[str] = None) -> str:
        meta = self.get_novel(novel_id)
        if not meta:
            raise ValueError(f"Không tìm thấy bộ truyện '{novel_id}'")

        if not output_path:
            os.makedirs(os.path.join("storage", "outputs"), exist_ok=True)
            output_path = os.path.join("storage", "outputs", f"{novel_id}_vi.epub")

        # 1. Check if full.epub exists locally in novel dir
        local_full = os.path.join("storage", "novels", novel_id, "full.epub")
        if os.path.exists(local_full):
            return local_full

        # 2. Check if output_path already exists
        if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
            return output_path

        # 3. Check if full.epub exists in Cloudflare R2 (download whole file in 1 single fast call)
        full_epub_key = f"novels/{novel_id}/full.epub"
        if storage_repo.is_r2_active and settings.cloudflare_r2_bucket_name:
            try:
                resp = storage_repo.r2_client.get_object(
                    Bucket=settings.cloudflare_r2_bucket_name,
                    Key=full_epub_key,
                )
                epub_data = resp["Body"].read()
                if epub_data and len(epub_data) > 100:
                    with open(output_path, "wb") as f:
                        f.write(epub_data)
                    return output_path
            except Exception as e:
                logger.debug("full.epub not in R2, compiling from chapters: %s", e)

        # 4. Compile EPUB from chapters
        book = epub.EpubBook()
        book.set_identifier(f"epub-backend-{novel_id}")
        book.set_title(meta.title)
        book.set_language("vi")
        if meta.author:
            book.add_author(meta.author)

        # Style CSS
        style = """
        @namespace epub "http://www.idpf.org/2007/ops";
        body { font-family: sans-serif; line-height: 1.6; margin: 5%; }
        h1 { text-align: center; margin-bottom: 1.5em; font-size: 1.4em; }
        p { text-indent: 1.5em; margin: 0.5em 0; text-align: justify; }
        """
        default_css = epub.EpubItem(
            uid="style_default",
            file_name="style/default.css",
            media_type="text/css",
            content=style.encode("utf-8"),
        )
        book.add_item(default_css)

        epub_chapters = []
        for ch in meta.chapters:
            content = self.get_chapter_content(novel_id, ch.chapter_index, version="translated")
            if not content:
                content = self.get_chapter_content(novel_id, ch.chapter_index, version="original")
            if content:
                paragraphs = "".join(f"<p>{p.strip()}</p>" for p in content.split("\n") if p.strip())
                html_content = f"<h1>{ch.chapter_title}</h1>{paragraphs}"

                c_item = epub.EpubHtml(
                    title=ch.chapter_title,
                    file_name=f"ch_{ch.chapter_index:04d}.xhtml",
                    lang="vi",
                )
                c_item.content = html_content.encode("utf-8")
                c_item.add_item(default_css)
                book.add_item(c_item)
                epub_chapters.append(c_item)

        if not epub_chapters:
            raise ValueError(f"Bộ truyện '{meta.title}' chưa có nội dung chương để xuất sách EPUB.")

        book.toc = tuple(epub_chapters)
        book.spine = ["nav"] + epub_chapters
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        epub.write_epub(output_path, book)
        return output_path

    # ------------------------------------------------------------------
    # EPUB Direct Import
    # ------------------------------------------------------------------
    def import_epub_novel(
        self,
        epub_bytes: bytes,
        filename: str = "book.epub",
        is_translated: bool = True,
        novel_id: Optional[str] = None,
    ) -> NovelMetadata:
        from bs4 import BeautifulSoup
        with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
            tmp.write(epub_bytes)
            tmp_path = tmp.name

        try:
            book = epub.read_epub(tmp_path)

            # Extract metadata
            titles = book.get_metadata("DC", "title")
            title = titles[0][0] if titles else os.path.splitext(filename)[0]

            creators = book.get_metadata("DC", "creator")
            author = creators[0][0] if creators else "Chưa rõ"

            descriptions = book.get_metadata("DC", "description")
            description = descriptions[0][0] if descriptions else ""

            # Extract Cover Image
            cover_data = None
            cover_ext = "jpg"
            for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                if "cover" in item.get_name().lower() or not cover_data:
                    cover_data = item.get_content()
                    cover_ext = item.get_name().split(".")[-1].lower() if "." in item.get_name() else "jpg"
                    if "cover" in item.get_name().lower():
                        break

            # Create Novel metadata
            req = NovelCreateRequest(
                title=title,
                author=author,
                description=description,
                novel_id=novel_id,
            )
            meta = self.create_novel(req, cover_data=cover_data, cover_filename=f"cover.{cover_ext}")
            actual_id = meta.novel_id

            # Save full original epub file on R2 as well
            full_epub_key = f"novels/{actual_id}/full.epub"
            self._save_raw_file(full_epub_key, epub_bytes, content_type="application/epub+zip")

            # Extract chapters
            chapter_index = 1
            for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                content_html = item.get_content().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(content_html, "html.parser")

                # Extract text
                text_lines = [p.get_text().strip() for p in soup.find_all(["p", "h1", "h2", "h3", "div"]) if p.get_text().strip()]
                full_text = "\n\n".join(text_lines)

                if not full_text.strip() or len(full_text.strip()) < 30:
                    continue  # skip empty/cover/nav pages

                h_tag = soup.find(["h1", "h2", "h3"])
                ch_title = h_tag.get_text().strip() if h_tag else f"Chương {chapter_index}"
                if len(ch_title) > 80:
                    ch_title = f"Chương {chapter_index}"

                ch_id = f"ch_{chapter_index:04d}"
                folder = "translated" if is_translated else "original"
                ch_key = f"novels/{actual_id}/{folder}/{ch_id}.txt"

                self._save_raw_file(ch_key, full_text.encode("utf-8"), content_type="text/plain; charset=utf-8")

                word_count = len(full_text.split())
                preview = (full_text[:150] + "...") if len(full_text) > 150 else full_text

                chapter_item = ChapterItem(
                    chapter_index=chapter_index,
                    chapter_id=ch_id,
                    chapter_title=ch_title,
                    status=ChapterStatus.COMPLETED if is_translated else ChapterStatus.NOT_TRANSLATED,
                    word_count=word_count,
                    original_text_preview="" if is_translated else preview,
                    translated_text_preview=preview if is_translated else "",
                    updated_at=datetime.utcnow().isoformat(),
                    r2_original_key="" if is_translated else ch_key,
                    r2_translated_key=ch_key if is_translated else "",
                )
                meta.chapters.append(chapter_item)
                chapter_index += 1

            meta.total_chapters = len(meta.chapters)
            meta.translated_chapters = len(meta.chapters) if is_translated else 0
            meta.updated_at = datetime.utcnow().isoformat()
            self._save_metadata(meta)
            self._cache[actual_id] = meta
            return meta

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def get_import_job(self, job_id: str) -> Optional[ImportJobStatus]:
        return self._import_jobs.get(job_id)

    def start_import_epub_async(
        self,
        epub_bytes: bytes,
        filename: str = "book.epub",
        is_translated: bool = True,
        novel_id: Optional[str] = None,
        auto_scan_characters: bool = False,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ImportJobStatus:
        job_id = f"import_{uuid.uuid4().hex[:8]}"
        job = ImportJobStatus(
            job_id=job_id,
            status="processing",
            current_step="Đang chuẩn bị file và đọc thông tin sách...",
            progress_percentage=5,
        )
        self._import_jobs[job_id] = job

        def _worker():
            from bs4 import BeautifulSoup
            with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
                tmp.write(epub_bytes)
                tmp_path = tmp.name

            try:
                book = epub.read_epub(tmp_path)

                # Extract metadata
                titles = book.get_metadata("DC", "title")
                title = titles[0][0] if titles else os.path.splitext(filename)[0]

                creators = book.get_metadata("DC", "creator")
                author = creators[0][0] if creators else "Chưa rõ"

                descriptions = book.get_metadata("DC", "description")
                description = descriptions[0][0] if descriptions else ""

                job.title = title
                job.current_step = f"Đang tạo bộ truyện '{title}' & trích xuất ảnh bìa..."
                job.progress_percentage = 10

                # Extract Cover Image
                cover_data = None
                cover_ext = "jpg"
                for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                    if "cover" in item.get_name().lower() or not cover_data:
                        cover_data = item.get_content()
                        cover_ext = item.get_name().split(".")[-1].lower() if "." in item.get_name() else "jpg"
                        if "cover" in item.get_name().lower():
                            break

                # Create Novel metadata
                req = NovelCreateRequest(
                    title=title,
                    author=author,
                    description=description,
                    novel_id=novel_id,
                )
                meta = self.create_novel(req, cover_data=cover_data, cover_filename=f"cover.{cover_ext}")
                actual_id = meta.novel_id
                job.novel_id = actual_id

                # Save full original epub file on R2 as well
                full_epub_key = f"novels/{actual_id}/full.epub"
                self._save_raw_file(full_epub_key, epub_bytes, content_type="application/epub+zip")

                # Pre-filter document items to get total valid chapters
                doc_items = []
                for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                    content_html = item.get_content().decode("utf-8", errors="ignore")
                    soup = BeautifulSoup(content_html, "html.parser")
                    text_lines = [p.get_text().strip() for p in soup.find_all(["p", "h1", "h2", "h3", "div"]) if p.get_text().strip()]
                    full_text = "\n\n".join(text_lines)
                    if full_text.strip() and len(full_text.strip()) >= 30:
                        h_tag = soup.find(["h1", "h2", "h3"])
                        ch_title = h_tag.get_text().strip() if h_tag else ""
                        doc_items.append((ch_title, full_text))

                total = len(doc_items)
                job.total_chapters = total

                # Process and upload each chapter with live progress
                for idx, (ch_title, full_text) in enumerate(doc_items, start=1):
                    if not ch_title or len(ch_title) > 80:
                        ch_title = f"Chương {idx}"

                    ch_id = f"ch_{idx:04d}"
                    folder = "translated" if is_translated else "original"
                    ch_key = f"novels/{actual_id}/{folder}/{ch_id}.txt"

                    self._save_raw_file(ch_key, full_text.encode("utf-8"), content_type="text/plain; charset=utf-8")

                    word_count = len(full_text.split())
                    preview = (full_text[:150] + "...") if len(full_text) > 150 else full_text

                    chapter_item = ChapterItem(
                        chapter_index=idx,
                        chapter_id=ch_id,
                        chapter_title=ch_title,
                        status=ChapterStatus.COMPLETED if is_translated else ChapterStatus.NOT_TRANSLATED,
                        word_count=word_count,
                        original_text_preview="" if is_translated else preview,
                        translated_text_preview=preview if is_translated else "",
                        updated_at=datetime.utcnow().isoformat(),
                        r2_original_key="" if is_translated else ch_key,
                        r2_translated_key=ch_key if is_translated else "",
                    )
                    meta.chapters.append(chapter_item)

                    # Update live progress
                    job.current_chapter = idx
                    job.current_step = f"Đang lưu R2: Chương {idx}/{total} - {ch_title}"
                    job.progress_percentage = 10 + int((idx / max(1, total)) * 75)

                meta.total_chapters = len(meta.chapters)
                meta.translated_chapters = len(meta.chapters) if is_translated else 0
                meta.updated_at = datetime.utcnow().isoformat()
                self._save_metadata(meta)
                self._cache[actual_id] = meta

                # Optional character scan
                if auto_scan_characters and meta.chapters and api_key:
                    job.current_step = "Đang tự động quét & trích xuất nhân vật / Book Bible..."
                    job.progress_percentage = 90
                    import asyncio
                    try:
                        asyncio.run(self.scan_characters_and_timeline(
                            novel_id=actual_id,
                            max_chapters=5,
                            provider=provider,
                            api_key=api_key,
                            model=model,
                        ))
                    except Exception as scan_err:
                        logger.warning("Auto scan characters skipped or failed: %s", scan_err)

                job.status = "completed"
                job.current_step = f"Đã hoàn thành nhập '{title}' ({total} chương) vào Cloudflare R2!"
                job.progress_percentage = 100
                job.completed_at = datetime.utcnow().isoformat()

            except Exception as exc:
                logger.error("Lỗi khi import EPUB async: %s", exc)
                job.status = "failed"
                job.error_message = str(exc)
                job.current_step = f"Lỗi: {exc}"
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return job

    # ------------------------------------------------------------------
    # Storage Helpers
    # ------------------------------------------------------------------
    def _save_metadata(self, meta: NovelMetadata) -> None:
        key = self._novel_meta_key(meta.novel_id)
        data = meta.model_dump(mode="json")
        if storage_repo.is_r2_active:
            storage_repo._r2_put_json(key, data)

        # Also save local backup
        local_dir = os.path.join("storage", "novels", meta.novel_id)
        os.makedirs(local_dir, exist_ok=True)
        with open(os.path.join(local_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_raw_file(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        if storage_repo.is_r2_active and settings.cloudflare_r2_bucket_name:
            try:
                storage_repo.r2_client.put_object(
                    Bucket=settings.cloudflare_r2_bucket_name,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
                if settings.cloudflare_r2_public_url:
                    return f"{settings.cloudflare_r2_public_url.rstrip('/')}/{key}"
                return f"https://{settings.cloudflare_account_id}.r2.cloudflarestorage.com/{settings.cloudflare_r2_bucket_name}/{key}"
            except Exception as exc:
                logger.warning("Failed to save raw file to R2 (%s): %s", key, exc)

        local_path = os.path.join("storage", key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(data)
        return f"/storage/{key}"


library_service = LibraryService()
