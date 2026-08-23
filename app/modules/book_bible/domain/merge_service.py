from typing import Dict, Optional

from app.modules.book_bible.application.facade import BookBibleService
from app.schemas.book_bible import BookBible, BookBibleDelta


class BookBibleMergeService:
    """Pure merge and filtering entry points for Book Bible state."""

    @staticmethod
    def ensure_timeline(bible: BookBible) -> BookBible:
        return BookBibleService.ensure_timeline(bible)

    @staticmethod
    def merge_delta(
        bible: BookBible,
        delta: BookBibleDelta,
        chapter_index: Optional[int] = None,
        chapter_id: str = "",
        chunk_id: str = "",
    ) -> BookBible:
        return BookBibleService.merge_delta(
            bible,
            delta,
            chapter_index=chapter_index,
            chapter_id=chapter_id,
            chunk_id=chunk_id,
        )

    @staticmethod
    def filter_for_text(bible: BookBible, text: str) -> BookBible:
        return BookBibleService.filter_bible_for_text(bible, text)

    @staticmethod
    def detect_novel(text: str, bibles: Dict[str, BookBible]):
        return BookBibleService.detect_novel_id(text, bibles)
