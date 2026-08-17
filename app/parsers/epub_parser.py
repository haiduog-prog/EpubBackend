import os
import tempfile
from typing import List, Tuple, Dict
from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub
from app.parsers.html_merger import HTMLMerger
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem


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
        book = epub.read_epub(epub_path)
        chapters: List[Tuple[str, List[HTMLInputItem], BeautifulSoup]] = []

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            content = item.get_content().decode("utf-8", errors="ignore")
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
        book = epub.read_epub(input_epub_path)

        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            item_id = item.get_id()
            if item_id in translated_chapters:
                translations = translated_chapters[item_id]
                original_content = item.get_content().decode("utf-8", errors="ignore")
                _, soup = HTMLMerger.extract_semantic_nodes(original_content)
                new_html = HTMLMerger.reconstruct_html(soup, translations)
                item.set_content(new_html.encode("utf-8"))

        epub.write_epub(output_epub_path, book)
