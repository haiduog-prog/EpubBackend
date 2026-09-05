import io
import logging
import os
import posixpath as zip_path
import tempfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub
from ebooklib.epub import EpubException, parse_string

from app.config import settings
from app.parsers.html_merger import HTMLMerger
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem

logger = logging.getLogger("EpubBackend.EPUBParser")


class ResilientEpubReader(epub.EpubReader):
    """
    Reader đọc file EPUB chịu lỗi cao:
    - Không crash khi manifest khai báo file ảnh, bìa (cover), css, font... bị thiếu trong archive zip.
    - Tìm kiếm file không phân biệt hoa thường (case-insensitive) và chuẩn hóa đường dẫn posix.
    - Tự động fallback tìm file theo basename (ví dụ 'cover.png' thay vì 'OEBPS/Images/cover.png').
    - Bỏ qua lỗi cú pháp nav/ncx không hợp lệ một cách an toàn.
    """

    def __init__(self, epub_file_name: str, options: Optional[dict] = None):
        super().__init__(epub_file_name, options)
        self._zip_name_map: Dict[str, List[str]] = {}
        self._zip_basename_map: Dict[str, List[str]] = {}

    def _init_zip_maps(self) -> None:
        if hasattr(self.zf, "namelist") and not self._zip_name_map:
            for actual_name in self.zf.namelist():
                norm = actual_name.lstrip("/").replace("\\", "/").lower()
                self._zip_name_map.setdefault(norm, []).append(actual_name)
                basename = norm.split("/")[-1]
                if basename:
                    self._zip_basename_map.setdefault(basename, []).append(actual_name)

    def read_file(self, name: str) -> bytes:
        name = zip_path.normpath(name)
        # 1. Thử đọc trực tiếp tên gốc
        try:
            return self.zf.read(name)
        except (KeyError, FileNotFoundError):
            pass

        # 2. Chuẩn hóa đường dẫn & tìm không phân biệt chữ hoa/thường
        self._init_zip_maps()
        norm_name = name.lstrip("/").replace("\\", "/").lower()
        if norm_name in self._zip_name_map:
            candidates = self._zip_name_map[norm_name]
            if len(candidates) > 1:
                raise EpubException(
                    -1,
                    f"Có nhiều file trùng đường dẫn không phân biệt hoa thường cho '{name}': {candidates}",
                )
            return self.zf.read(candidates[0])

        # 3. Fallback theo tên file (basename) nếu manifest lệch cấu trúc thư mục
        basename = norm_name.split("/")[-1]
        if basename in self._zip_basename_map:
            candidates = self._zip_basename_map[basename]
            if len(candidates) > 1:
                raise EpubException(
                    -1,
                    f"Không thể fallback file '{name}' vì basename '{basename}' không duy nhất: {candidates}",
                )
            return self.zf.read(candidates[0])

        # 4. Trả về bytes rỗng thay vì raise KeyError làm đứt gãy quá trình nạp truyện
        logger.warning("Không tìm thấy file '%s' trong archive EPUB, bỏ qua an toàn.", name)
        return b""

    def _load_container(self) -> None:
        try:
            super()._load_container()
        except Exception:
            # Fallback tìm kiếm bất kỳ file .opf nào trong archive nếu container.xml bị lỗi hoặc thiếu
            self._init_zip_maps()
            if hasattr(self.zf, "namelist"):
                opf_candidates = [n for n in self.zf.namelist() if n.lower().endswith(".opf")]
                if opf_candidates:
                    self.opf_file = opf_candidates[0]
                    self.opf_dir = zip_path.dirname(self.opf_file)
                    return
            raise EpubException(-1, "Không tìm thấy file container.xml hoặc file .opf trong EPUB archive.")

    def _load_spine(self) -> None:
        """Nạp reading order nhưng coi NCX là metadata không bắt buộc."""
        spine = self.container.find(f"{{{epub.NAMESPACES['OPF']}}}spine")
        if spine is None:
            raise EpubException(-1, "Không tìm thấy spine trong file OPF.")

        self.book.spine = [
            (item.get("idref"), item.get("linear", "yes"))
            for item in spine
        ]
        self.book.set_direction(spine.get("page-progression-direction", None))

        toc_id = spine.get("toc", "")
        nav_item = next(
            (item for item in self.book.items if isinstance(item, epub.EpubNav)),
            None,
        )
        if not toc_id or (self.options.get("ignore_ncx") and nav_item):
            return

        ncx_item = self.book.get_item_with_id(toc_id)
        ncx_content = getattr(ncx_item, "content", b"") if ncx_item else b""
        if not ncx_content:
            logger.warning("Không tìm thấy nội dung NCX '%s', tiếp tục không có NCX.", toc_id)
            return

        try:
            self._parse_ncx(ncx_content)
        except Exception as ncx_err:
            logger.warning("NCX '%s' không hợp lệ, bỏ qua: %s", toc_id, ncx_err)

    def _load_opf_file(self) -> None:
        try:
            s = self.read_file(self.opf_file)
            if not s:
                raise KeyError()
            self.container = parse_string(s)
        except Exception:
            raise EpubException(-1, f"Không thể đọc hoặc parse file OPF: {self.opf_file}")

        self._load_metadata()
        self._load_manifest()
        self._load_spine()
        self._load_guide()

        # Parse file nav nếu có nhưng bắt lỗi an toàn nếu file nav bị lỗi/rỗng
        nav_item = next((item for item in self.book.items if isinstance(item, epub.EpubNav)), None)
        if nav_item and nav_item.content:
            try:
                if self.options.get("ignore_ncx") or not self.book.toc:
                    self._parse_nav(
                        nav_item.content,
                        zip_path.dirname(nav_item.file_name),
                        navtype="toc",
                    )
                self._parse_nav(
                    nav_item.content,
                    zip_path.dirname(nav_item.file_name),
                    navtype="pages",
                )
            except Exception as nav_err:
                logger.debug("Lỗi khi parse EPUB nav (không ảnh hưởng nội dung chương): %s", nav_err)


def _validate_epub_archive(name: str) -> None:
    """Reject path traversal and zip bombs before ebooklib reads the archive."""
    with zipfile.ZipFile(name, "r") as archive:
        entries = archive.infolist()
        if len(entries) > settings.max_epub_entries:
            raise EpubException(-1, "EPUB archive contains too many entries.")

        total_size = 0
        for entry in entries:
            normalized = entry.filename.replace("\\", "/")
            if normalized.startswith("/") or any(part == ".." for part in normalized.split("/")):
                raise EpubException(-1, "EPUB archive contains an unsafe path.")
            if entry.file_size > settings.max_epub_entry_bytes:
                raise EpubException(-1, "EPUB archive contains an oversized entry.")
            total_size += entry.file_size
            if total_size > settings.max_epub_uncompressed_bytes:
                raise EpubException(-1, "EPUB archive is too large after decompression.")


def read_epub_safe(name: str, options: Optional[dict] = None) -> epub.EpubBook:
    """Read EPUB with malformed-resource tolerance and archive safety limits."""
    try:
        _validate_epub_archive(name)
    except EpubException:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise EpubException(-1, f"Invalid EPUB archive: {exc}") from exc
    reader = ResilientEpubReader(name, options)
    book = reader.load()
    reader.process()
    return book


def extract_cover_from_epub(
    book: epub.EpubBook,
    epub_path_or_bytes: Optional[Any] = None,
) -> Tuple[Optional[bytes], str]:
    """
    Trích xuất an toàn ảnh bìa (cover image) từ sách EPUB.
    Ưu tiên:
    1. Item ảnh có tên chứa 'cover' trong ebooklib items (với nội dung > 0 bytes).
    2. Item ảnh bất kỳ đầu tiên trong ebooklib items.
    3. Tìm trực tiếp file ảnh trong zip archive nếu ebooklib không tìm thấy.
    """
    cover_data: Optional[bytes] = None
    cover_ext = "jpg"

    # 1. Thử lấy từ các image item đã nạp trong ebooklib
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        try:
            content = item.get_content()
            if not content:
                continue
            ext = item.get_name().split(".")[-1].lower() if "." in item.get_name() else "jpg"
            if "cover" in item.get_name().lower():
                return content, ext
            if not cover_data:
                cover_data = content
                cover_ext = ext
        except Exception:
            continue

    if cover_data:
        return cover_data, cover_ext

    # 2. Fallback: Quét trực tiếp zip archive
    if epub_path_or_bytes:
        try:
            if isinstance(epub_path_or_bytes, (bytes, bytearray)):
                zf = zipfile.ZipFile(io.BytesIO(epub_path_or_bytes), "r")
            elif isinstance(epub_path_or_bytes, str) and os.path.exists(epub_path_or_bytes):
                zf = zipfile.ZipFile(epub_path_or_bytes, "r")
            else:
                zf = None

            if zf:
                img_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
                cover_candidate = None
                first_img_candidate = None

                for name in zf.namelist():
                    lower_name = name.lower()
                    if any(lower_name.endswith(ext) for ext in img_exts):
                        if "cover" in lower_name:
                            cover_candidate = name
                            break
                        elif not first_img_candidate:
                            first_img_candidate = name

                chosen = cover_candidate or first_img_candidate
                if chosen:
                    ext = chosen.split(".")[-1].lower() if "." in chosen else "jpg"
                    data = zf.read(chosen)
                    if data:
                        return data, ext
        except Exception as zip_err:
            logger.debug("Lỗi khi tìm ảnh bìa trực tiếp từ zip: %s", zip_err)

    return None, "jpg"


class EPUBParser:
    """
    Đọc file EPUB, giải mã các chương HTML, trích xuất text nodes thông qua HTMLMerger,
    và ghi lại thành file EPUB dịch hoàn chỉnh.
    """

    @staticmethod
    def read_epub_chapters(epub_path: str) -> List[Tuple[str, List[HTMLInputItem], BeautifulSoup]]:
        """
        Đọc file EPUB và trả về danh sách các chương: (item_id, input_items, soup_tree).
        """
        book = read_epub_safe(epub_path)
        chapters: List[Tuple[str, List[HTMLInputItem], BeautifulSoup]] = []

        # The OPF manifest is an inventory, not the reading order.
        documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        by_id = {item.get_id(): item for item in documents}
        ordered = []
        seen = set()
        for item_id, linear in book.spine:
            if item_id in by_id and item_id not in seen and linear != "no":
                ordered.append(by_id[item_id])
                seen.add(item_id)
        # Some legacy EPUBs have no usable spine. Keep their previous fallback.
        if not ordered:
            ordered = documents
        for item in ordered:
            try:
                content_bytes = item.get_content() or b""
            except Exception as read_err:
                logger.warning("Không thể đọc nội dung item %s: %s", item.get_name(), read_err)
                continue

            content = content_bytes.decode("utf-8", errors="ignore")
            input_items, soup = HTMLMerger.extract_semantic_nodes(content)
            if input_items:
                chapters.append((item.get_id(), input_items, soup))

        return chapters

    @staticmethod
    def rebuild_epub(
        input_epub_path: str,
        output_epub_path: str,
        translated_chapters: Dict[str, List[HTMLTranslationItem]]
    ) -> None:
        """
        Đọc EPUB gốc, thay thế nội dung đã dịch theo item_id, và lưu thành output_epub_path.
        """
        book = read_epub_safe(input_epub_path)

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            item_id = item.get_id()
            if item_id in translated_chapters:
                translations = translated_chapters[item_id]
                try:
                    content_bytes = item.get_content() or b""
                except Exception:
                    continue
                original_content = content_bytes.decode("utf-8", errors="ignore")
                _, soup = HTMLMerger.extract_semantic_nodes(original_content)
                new_html = HTMLMerger.reconstruct_html(soup, translations, strict_markers=True)
                item.set_content(new_html.encode("utf-8"))

        epub.write_epub(output_epub_path, book)
