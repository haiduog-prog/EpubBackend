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
from datetime import timezone, timezone
from typing import Any, Dict, List, Optional, Tuple

import ebooklib
from ebooklib import epub

from app.config import settings
from app.core.storage import storage_repo
from app.db.session import db_session
from app.repositories.library_repository import LibraryRepository
from app.repositories.book_bible_repository import BookBibleRepository
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


def parse_chapter_index_from_title(title: str) -> Optional[int]:
    """
    Trích xuất số thứ tự chương từ tiêu đề chương hoặc tên file.
    Hỗ trợ:
    - Tiếng Việt: "Chương 101:...", "Hồi 12", "Tiết 5", "Quyển 1 Chương 20"
    - Tiếng Trung: "第101章", "第12回", "第5节"
    - Tiếng Anh: "Chapter 101", "Ch. 101", "ch_0101"
    """
    if not title:
        return None
    # 1. Standard pattern: Chương 101 / Chapter 101 / Hồi 101 / Tiết 101
    m = re.search(r"(?:chương|chuong|chapter|hồi|hoi|tiết|tiet|ch\.?)\s*(\d+)", title, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # 2. Chinese pattern: 第101章 / 第101回 / 第101节
    m = re.search(r"第\s*(\d+)\s*[章回节]", title)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # 3. Filename pattern: ch_0101.xhtml / chapter_0101
    m = re.search(r"(?:ch|chapter)_?(\d+)", title, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


class LibraryService:
    """Dịch vụ quản lý Kho Truyện (Novel Library) lưu trữ trên Cloudflare R2 / Local."""

    def __init__(self):
        self._cache: Dict[str, NovelMetadata] = {}
        self._import_jobs: Dict[str, ImportJobStatus] = {}
        self._novel_locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_novel_lock(self, novel_id: str) -> threading.Lock:
        with self._global_lock:
            if novel_id not in self._novel_locks:
                self._novel_locks[novel_id] = threading.Lock()
            return self._novel_locks[novel_id]

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

        now = datetime.now(timezone.utc).isoformat()
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

        # 1. Read from database if postgres is configured
        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_novel = LibraryRepository.get_novel(session, novel_id)
                    if db_novel:
                        self._cache[novel_id] = db_novel
                    return db_novel
            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    raise exc
                logger.warning("Failed to read novel %s from database: %s", novel_id, exc)


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

        # 1. Read from database if postgres is configured
        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_novels = LibraryRepository.list_novels(session)
                    for n in db_novels:
                        summaries[n.novel_id] = n
                    return list(summaries.values())
            except Exception as exc:
                logger.warning("Failed to list novels from database: %s", exc)

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

        meta.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_metadata(meta)
        self._cache[novel_id] = meta
        return meta

    def delete_novel(self, novel_id: str) -> bool:
        meta = self.get_novel(novel_id)
        if not meta:
            return False

        # 1. Delete from database if dual or postgres
        if settings.structured_storage_backend in ("dual", "postgres"):
            try:
                with db_session() as session:
                    LibraryRepository.delete_novel(session, novel_id)
                    session.commit()
            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    logger.error("Failed to delete novel %s from database in postgres mode: %s", novel_id, exc)
                    raise exc
                logger.warning("Failed to delete novel %s from database in dual mode: %s", novel_id, exc)
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
            existing_item.updated_at = datetime.now(timezone.utc).isoformat()
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
                updated_at=datetime.now(timezone.utc).isoformat(),
                r2_original_key=orig_key,
                r2_translated_key="",
            )
            meta.chapters.append(chapter_item)

        meta.chapters.sort(key=lambda x: x.chapter_index)
        meta.total_chapters = len(meta.chapters)
        meta.translated_chapters = sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED)
        meta.updated_at = datetime.now(timezone.utc).isoformat()

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
            chapter.updated_at = datetime.now(timezone.utc).isoformat()

            meta.translated_chapters = sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED)
            meta.updated_at = datetime.now(timezone.utc).isoformat()
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
        for idx, char in enumerate(bible.characters):
            role_str = char.role or "Nhân vật"
            is_main = any(k in role_str.lower() for k in ["chính", "nam chính", "nữ chính", "protagonist", "main"])
            c_info = {
                "character_id": char.character_id or char.original_name,
                "original_name": char.original_name,
                "vi_name": char.vi_name,
                "role": role_str,
                "is_main": is_main,
                "realm": char.voice_notes or "Chưa rõ cảnh giới",
                "items": [],
                "skills": [],
                "pets": [],
                "faction": "",
                "address_terms": [
                    f"{a.self_term} / {a.other_term} (với {a.with_person})"
                    for a in char.address_terms
                ],
            }
            characters_list.append(c_info)

        # Nếu chưa có nhân vật nào được đánh dấu là chính thì mặc định nhân vật đầu tiên là nhân vật chính
        if characters_list and not any(c["is_main"] for c in characters_list):
            characters_list[0]["is_main"] = True

        # Categorize terms
        items = [f"{t.original_name} → {t.vi_name}" for t in bible.terms if any(k in t.category.lower() for k in ["item", "pháp bảo", "bảo vật", "vũ khí", "đan dược", "vật phẩm"])]
        skills = [f"{t.original_name} → {t.vi_name}" for t in bible.terms if any(k in t.category.lower() for k in ["skill", "công pháp", "võ kỹ", "bí thuật", "chiêu thức"])]
        pets = [f"{t.original_name} → {t.vi_name}" for t in bible.terms if any(k in t.category.lower() for k in ["pet", "linh thú", "sủng vật", "thú cưỡi", "tọa kỵ", "thần thú", "yêu thú", "khế ước thú"])]
        places = [f"{p.original_name} → {p.vi_name}" for p in bible.places]

        # Attach inventory, skills, and pets to main character
        main_char = next((c for c in characters_list if c["is_main"]), None)
        if main_char:
            if items:
                main_char["items"] = items
            if skills:
                main_char["skills"] = skills
            if pets:
                main_char["pets"] = pets

        return {
            "novel_id": novel_id,
            "novel_title": meta.title,
            "chapter_index": chapter_index,
            "main_character": main_char,
            "characters": characters_list,
            "inventory_items": items or [f"{t.original_name} → {t.vi_name}" for t in bible.terms[:5]],
            "skills": skills or [],
            "pets": pets or [],
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

        # Luôn biên dịch sách EPUB hoàn chỉnh từ toàn bộ các chương đã gộp trong kho
        book = epub.EpubBook()
        book.set_identifier(f"epub-backend-{novel_id}")
        book.set_title(meta.title)
        book.set_language("vi")
        if meta.author:
            book.add_author(meta.author)

        # Check and add cover image if available
        cover_content = None
        if storage_repo.is_r2_active and settings.cloudflare_r2_bucket_name:
            try:
                resp = storage_repo.r2_client.get_object(
                    Bucket=settings.cloudflare_r2_bucket_name,
                    Key=f"novels/{novel_id}/cover.jpg",
                )
                cover_content = resp["Body"].read()
            except Exception:
                pass
        if not cover_content:
            local_cover = os.path.join("storage", "novels", novel_id, "cover.jpg")
            if os.path.exists(local_cover):
                try:
                    with open(local_cover, "rb") as cf:
                        cover_content = cf.read()
                except Exception:
                    pass

        if cover_content:
            try:
                book.set_cover("cover.jpg", cover_content)
            except Exception as cover_err:
                logger.debug("Failed to set EPUB cover: %s", cover_err)

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
    # EPUB Extraction & Continuous Indexing Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_raw_chapters_from_epub(book: epub.EpubBook) -> List[Tuple[str, str]]:
        """
        Trích xuất danh sách (chapter_title, full_text) từ sách EPUB.
        - Duyệt theo thứ tự reading order chuẩn (book.spine).
        - Bỏ qua các mục phụ bản (cover, toc, nav, titlepage, copyright).
        - Tự động gộp trang tiêu đề ngắn (<150 ký tự) vào trang nội dung kế tiếp.
        - Hỗ trợ phân tách nếu 1 file HTML chứa nhiều chương.
        - Làm sạch DOM tránh nhân bản đoạn văn do thẻ lồng nhau.
        """
        from bs4 import BeautifulSoup

        # 1. Lấy danh sách item theo đúng thứ tự reading order (spine)
        items = []
        if getattr(book, "spine", None):
            for entry in book.spine:
                item_id = entry[0] if isinstance(entry, (list, tuple)) else entry
                if not item_id or item_id in ("nav", "ncx"):
                    continue
                item = book.get_item_with_id(item_id)
                if item and item.get_type() == ebooklib.ITEM_DOCUMENT:
                    items.append(item)

        # Fallback nếu spine rỗng hoặc không trích xuất được document
        if not items:
            items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

        raw_sections: List[Tuple[str, str]] = []
        pending_title: str = ""

        # Các từ khóa nhận diện file phụ bản không chứa nội dung truyện
        ignored_names = {
            "cover", "nav", "toc", "titlepage", "title_page", "halftitle",
            "copyright", "colophon", "about", "feedback", "author"
        }

        for item in items:
            name_lower = (item.get_name() or "").lower()
            id_lower = (getattr(item, "id", "") or "").lower()

            content_html = item.get_content().decode("utf-8", errors="ignore")
            soup = BeautifulSoup(content_html, "html.parser")

            # Xóa các thẻ không phục vụ đọc
            for tag in soup(["script", "style", "nav", "noscript"]):
                tag.decompose()

            # Bỏ qua file cover / toc / nav nếu tên rõ ràng và không có nhiều nội dung truyện
            if any(ign in name_lower or ign in id_lower for ign in ignored_names):
                p_tags = soup.find_all(["p", "div"])
                total_text_len = sum(len(p.get_text().strip()) for p in p_tags)
                if total_text_len < 300:
                    continue

            # Kiểm tra nếu tài liệu có nhiều tiêu đề chương (multi-chapter file)
            headings = soup.find_all(["h1", "h2", "h3", "h4"])
            chapter_headings = []
            for h in headings:
                h_text = h.get_text().strip()
                if parse_chapter_index_from_title(h_text) is not None or re.search(r"^(?:chương|chuong|hồi|hoi|tiết|tiet|chapter)\s*\d+", h_text, re.IGNORECASE):
                    chapter_headings.append(h)

            if len(chapter_headings) > 1:
                # Tài liệu chứa nhiều chương gộp chung -> Tách theo từng heading
                pending_title = ""
                body = soup.find("body") or soup
                current_split_title = chapter_headings[0].get_text().strip()
                current_split_lines: List[str] = []

                for elem in body.find_all(["p", "h1", "h2", "h3", "h4", "div", "blockquote", "li"]):
                    if elem in chapter_headings:
                        if current_split_lines:
                            sec_text = "\n\n".join(current_split_lines).strip()
                            if len(sec_text) >= 30:
                                raw_sections.append((current_split_title, sec_text))
                        current_split_title = elem.get_text().strip()
                        current_split_lines = []
                    elif elem.name in ["p", "blockquote", "li"]:
                        t = elem.get_text().strip()
                        if t:
                            current_split_lines.append(t)
                    elif elem.name == "div" and not elem.find(["p", "div", "blockquote"]):
                        t = elem.get_text().strip()
                        if t:
                            current_split_lines.append(t)

                if current_split_lines:
                    sec_text = "\n\n".join(current_split_lines).strip()
                    if len(sec_text) >= 30:
                        raw_sections.append((current_split_title, sec_text))
                continue

            # Thay thế thẻ br bằng dấu xuống dòng
            for br in soup.find_all("br"):
                br.replace_with("\n")

            # Trường hợp thông thường: 1 file = 1 chương (hoặc 1 trang tiêu đề lẻ)
            p_tags = soup.find_all(["p", "blockquote", "li"])
            if len(p_tags) > 0:
                text_lines = [
                    elem.get_text().strip()
                    for elem in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "li"])
                    if elem.get_text().strip()
                ]
            else:
                body = soup.find("body") or soup
                text_lines = [l.strip() for l in body.get_text("\n").split("\n") if l.strip()]

            full_text = "\n\n".join(text_lines).strip()
            if not full_text:
                continue

            h_tag = soup.find(["h1", "h2", "h3", "h4"])
            ch_title = h_tag.get_text().strip() if h_tag else ""
            if not ch_title and text_lines:
                first_line = text_lines[0]
                if parse_chapter_index_from_title(first_line) is not None and len(first_line) <= 80:
                    ch_title = first_line

            # Xử lý trang chỉ có Tiêu đề (Title-only page / không có nội dung đoạn văn truyện)
            story_p_len = sum(len(p.get_text().strip()) for p in p_tags)
            is_title_only = False
            if len(p_tags) > 0:
                if story_p_len < 20 and (bool(ch_title) or parse_chapter_index_from_title(full_text) is not None):
                    is_title_only = True
            else:
                if len(full_text) < 80 and len(text_lines) <= 1 and (bool(ch_title) or parse_chapter_index_from_title(full_text) is not None):
                    is_title_only = True

            if is_title_only:
                pending_title = ch_title or full_text
                continue

            # Nếu có pending_title từ trang tiêu đề trước đó, ưu tiên sử dụng
            final_title = ch_title
            if pending_title:
                if not final_title or len(final_title) > 80 or parse_chapter_index_from_title(final_title) is None:
                    final_title = pending_title
                pending_title = ""

            if len(full_text) >= 30:
                raw_sections.append((final_title, full_text))

        return raw_sections

    @staticmethod
    def _assign_canonical_chapter_indices(
        raw_sections: List[Tuple[str, str]],
        start_chapter_index: Optional[int] = None,
    ) -> List[Tuple[int, str, str]]:
        """
        Gán số thứ tự chương duy nhất, đơn điệu tăng dần cho từng chương.
        Đảm bảo không bao giờ bị trùng lặp, lệch pha hay nhảy cóc.
        """
        assigned: List[Tuple[int, str, str]] = []
        curr_idx = 0

        for i, (title, full_text) in enumerate(raw_sections):
            extracted_num = parse_chapter_index_from_title(title)

            if start_chapter_index is not None and start_chapter_index > 1:
                # Chế độ upload từng phần có chỉ định mốc bắt đầu
                if extracted_num is not None and extracted_num >= start_chapter_index and extracted_num > curr_idx:
                    idx = extracted_num
                else:
                    idx = curr_idx + 1 if curr_idx >= start_chapter_index else start_chapter_index
            else:
                # Chế độ nạp thông thường
                if i == 0:
                    idx = extracted_num if (extracted_num is not None and extracted_num > 0) else 1
                elif extracted_num == 1 and curr_idx == 1 and len(assigned) == 1 and parse_chapter_index_from_title(assigned[0][1]) is None:
                    # Section đầu là trang Thông tin/Giới thiệu không số -> dời về index 0
                    assigned[0] = (0, assigned[0][1], assigned[0][2])
                    idx = 1
                elif extracted_num is not None and extracted_num > curr_idx and extracted_num <= curr_idx + 10:
                    idx = extracted_num
                else:
                    idx = curr_idx + 1

            curr_idx = idx
            clean_title = title if (title and len(title) <= 80) else f"Chương {idx}"
            assigned.append((idx, clean_title, full_text))

        return assigned

    # ------------------------------------------------------------------
    # EPUB Direct & Incremental Import
    # ------------------------------------------------------------------
    def _process_epub_chapters_sync(
        self,
        book: epub.EpubBook,
        actual_id: str,
        is_translated: bool,
        start_chapter_index: Optional[int] = None,
        force_overwrite: bool = False,
        job: Optional[ImportJobStatus] = None,
    ) -> NovelMetadata:
        with self._get_novel_lock(actual_id):
            meta = self.get_novel(actual_id)
            if not meta:
                raise ValueError(f"Không tìm thấy thông tin bộ truyện '{actual_id}' trong kho.")

            # 1. Trích xuất các section thô từ EPUB
            raw_sections = self._extract_raw_chapters_from_epub(book)
            if not raw_sections:
                raise ValueError("Không tìm thấy nội dung chương hợp lệ trong file EPUB.")

            # 2. Đánh số thứ tự chương chuẩn hóa, liên tục
            canonical_chapters = self._assign_canonical_chapter_indices(
                raw_sections,
                start_chapter_index=start_chapter_index,
            )

            total = len(canonical_chapters)
            if job:
                job.total_chapters = total

            existing_chapters_map = {ch.chapter_index: ch for ch in meta.chapters}
            merged_chapters = dict(existing_chapters_map)
            added_count = 0
            skipped_count = 0
            updated_count = 0

            for seq_idx, (actual_index, ch_title, full_text) in enumerate(canonical_chapters, start=1):
                ch_id = f"ch_{actual_index:04d}"
                folder = "translated" if is_translated else "original"
                ch_key = f"novels/{actual_id}/{folder}/{ch_id}.txt"

                word_count = len(full_text.split())
                preview = (full_text[:150] + "...") if len(full_text) > 150 else full_text
                now_str = datetime.now(timezone.utc).isoformat()

                existing_ch = existing_chapters_map.get(actual_index)
                if existing_ch:
                    existing_key = existing_ch.r2_translated_key if is_translated else existing_ch.r2_original_key
                    if existing_key and not force_overwrite:
                        skipped_count += 1
                        if job:
                            job.skipped_chapters = skipped_count
                            job.current_chapter = seq_idx
                            job.current_step = f"Bỏ qua chương {actual_index} (đã có bản {'dịch' if is_translated else 'gốc'}): {existing_ch.chapter_title}"
                            job.progress_percentage = 10 + int((seq_idx / max(1, total)) * 75)
                        continue

                    # Save text to storage
                    self._save_raw_file(ch_key, full_text.encode("utf-8"), content_type="text/plain; charset=utf-8")

                    # In-place merge without wiping out other version
                    if is_translated:
                        existing_ch.r2_translated_key = ch_key
                        existing_ch.translated_text_preview = preview
                        existing_ch.status = ChapterStatus.COMPLETED
                        if ch_title:
                            existing_ch.chapter_title = ch_title
                        existing_ch.updated_at = now_str
                    else:
                        existing_ch.r2_original_key = ch_key
                        existing_ch.original_text_preview = preview
                        existing_ch.word_count = word_count
                        if ch_title:
                            existing_ch.chapter_title = ch_title
                        existing_ch.updated_at = now_str

                    merged_chapters[actual_index] = existing_ch
                    if existing_key and force_overwrite:
                        updated_count += 1
                        if job:
                            job.updated_chapters = updated_count
                    else:
                        added_count += 1
                        if job:
                            job.added_chapters = added_count
                else:
                    # New chapter
                    self._save_raw_file(ch_key, full_text.encode("utf-8"), content_type="text/plain; charset=utf-8")
                    new_item = ChapterItem(
                        chapter_index=actual_index,
                        chapter_id=ch_id,
                        chapter_title=ch_title,
                        status=ChapterStatus.COMPLETED if is_translated else ChapterStatus.NOT_TRANSLATED,
                        word_count=word_count,
                        original_text_preview="" if is_translated else preview,
                        translated_text_preview=preview if is_translated else "",
                        updated_at=now_str,
                        r2_original_key="" if is_translated else ch_key,
                        r2_translated_key=ch_key if is_translated else "",
                    )
                    merged_chapters[actual_index] = new_item
                    added_count += 1
                    if job:
                        job.added_chapters = added_count

                if job:
                    job.current_chapter = seq_idx
                    job.current_step = f"Đang nạp chương {actual_index} ({seq_idx}/{total}): {ch_title}"
                    job.progress_percentage = 10 + int((seq_idx / max(1, total)) * 75)

                # Periodic Checkpoint every 20 chapters
                if seq_idx % 20 == 0:
                    meta.chapters = [merged_chapters[k] for k in sorted(merged_chapters.keys())]
                    meta.total_chapters = len(meta.chapters)
                    meta.translated_chapters = sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED)
                    meta.updated_at = now_str
                    self._save_metadata(meta)
                    self._cache[actual_id] = meta

            # Final save
            meta.chapters = [merged_chapters[k] for k in sorted(merged_chapters.keys())]
            meta.total_chapters = len(meta.chapters)
            meta.translated_chapters = sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED)
            meta.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_metadata(meta)
            self._cache[actual_id] = meta
            return meta

    def import_epub_novel(
        self,
        epub_bytes: bytes,
        filename: str = "book.epub",
        is_translated: bool = True,
        novel_id: Optional[str] = None,
        start_chapter_index: Optional[int] = None,
        force_overwrite: bool = False,
    ) -> NovelMetadata:
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

            # Create/Update Novel metadata
            req = NovelCreateRequest(
                title=title,
                author=author,
                description=description,
                novel_id=novel_id,
            )
            meta = self.create_novel(req, cover_data=cover_data, cover_filename=f"cover.{cover_ext}")
            actual_id = meta.novel_id

            # Save uploaded raw epub file into isolated uploads folder
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            upload_key = f"novels/{actual_id}/uploads/{timestamp}.epub"
            self._save_raw_file(upload_key, epub_bytes, content_type="application/epub+zip")
            if is_translated and start_chapter_index is None:
                full_key = f"novels/{actual_id}/full.epub"
                self._save_raw_file(full_key, epub_bytes, content_type="application/epub+zip")

            return self._process_epub_chapters_sync(
                book=book,
                actual_id=actual_id,
                is_translated=is_translated,
                start_chapter_index=start_chapter_index,
                force_overwrite=force_overwrite,
            )

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _persist_import_job(self, job: ImportJobStatus) -> None:
        self._import_jobs[job.job_id] = job
        if settings.structured_storage_backend in ("dual", "postgres"):
            try:
                with db_session() as session:
                    LibraryRepository.save_import_job(session, job)
                    session.commit()
            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    logger.error("Failed to persist import job %s in postgres mode: %s", job.job_id, exc)
                    raise exc
                logger.warning("Failed to persist import job %s in dual mode: %s", job.job_id, exc)

    def get_import_job(self, job_id: str) -> Optional[ImportJobStatus]:
        if job_id in self._import_jobs:
            return self._import_jobs[job_id]

        if settings.structured_storage_read_source == "postgres" or settings.structured_storage_backend == "postgres":
            try:
                with db_session() as session:
                    db_job = LibraryRepository.get_import_job(session, job_id)
                    if db_job:
                        self._import_jobs[job_id] = db_job
                        return db_job
            except Exception as exc:
                logger.warning("Failed to get import job from DB: %s", exc)

        return None

    def start_import_epub_async(
        self,
        epub_bytes: bytes,
        filename: str = "book.epub",
        is_translated: bool = True,
        novel_id: Optional[str] = None,
        start_chapter_index: Optional[int] = None,
        force_overwrite: bool = False,
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
        self._persist_import_job(job)

        def _worker():
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
                job.current_step = f"Đang tạo/so khớp bộ truyện '{title}' & trích xuất ảnh bìa..."
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

                # Save uploaded epub file into isolated uploads folder
                upload_key = f"novels/{actual_id}/uploads/{job_id}.epub"
                self._save_raw_file(upload_key, epub_bytes, content_type="application/epub+zip")
                if is_translated and start_chapter_index is None:
                    full_key = f"novels/{actual_id}/full.epub"
                    self._save_raw_file(full_key, epub_bytes, content_type="application/epub+zip")

                # Process chapters with dual-version diff and checkpointing
                meta = self._process_epub_chapters_sync(
                    book=book,
                    actual_id=actual_id,
                    is_translated=is_translated,
                    start_chapter_index=start_chapter_index,
                    force_overwrite=force_overwrite,
                    job=job,
                )

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
                job.current_step = f"Đã hoàn thành nhập '{title}' (thêm {job.added_chapters} mới, bỏ qua {job.skipped_chapters} đã có)!"
                job.progress_percentage = 100
                job.completed_at = datetime.now(timezone.utc).isoformat()
                self._persist_import_job(job)

            except Exception as exc:
                logger.error("Lỗi khi import EPUB async: %s", exc)
                job.status = "failed"
                job.error_message = str(exc)
                job.current_step = f"Lỗi: {exc}"
                self._persist_import_job(job)
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
        # 1. Save to Database if backend is dual or postgres
        if settings.structured_storage_backend in ("dual", "postgres"):
            try:
                with db_session() as session:
                    LibraryRepository.save_novel(session, meta)
                    session.commit()
            except Exception as exc:
                if settings.structured_storage_backend == "postgres":
                    logger.error("Failed to save novel metadata to database in postgres mode: %s", exc)
                    raise exc
                logger.warning("Failed to save novel metadata to database in dual mode: %s", exc)

        # 2. Save JSON to R2 / Local storage if legacy or dual
        if settings.structured_storage_backend in ("legacy", "dual"):
            key = self._novel_meta_key(meta.novel_id)
            data = meta.model_dump(mode="json")
            if storage_repo.is_r2_active:
                storage_repo._r2_put_json(key, data)

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
