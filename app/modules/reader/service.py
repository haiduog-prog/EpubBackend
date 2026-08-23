import re
from typing import List, Optional, Sequence

from app.modules.library.application.facade import library_service
from app.schemas.library import ChapterItem, NovelMetadata, NovelSummary, ChapterStatus
from app.modules.reader.schemas import (
    ReaderBookDetail,
    ReaderBookSummary,
    ReaderChapterResponse,
    ReaderChapterSummary,
)


class ReaderError(Exception):
    """Base exception for expected public reader failures."""


class ReaderNotFoundError(ReaderError):
    """The requested book/chapter is not publicly readable."""


class ReaderValidationError(ReaderError):
    """The requested reader identifier is malformed."""


class ReaderService:
    """Read-only adapter over the Library application facade."""

    _NOVEL_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z")

    def __init__(self, library=library_service):
        self._library = library

    def list_books(self) -> List[ReaderBookSummary]:
        books: List[ReaderBookSummary] = []
        for summary in self._library.list_novels():
            metadata = self._library.get_novel(summary.novel_id)
            if not metadata:
                continue
            readable = self._readable_chapters(metadata.chapters)
            if not readable:
                continue
            books.append(self._to_book_summary(metadata, len(readable)))
        return books

    def get_book(self, novel_id: str) -> ReaderBookDetail:
        self._validate_novel_id(novel_id)
        metadata = self._library.get_novel(novel_id)
        if not metadata:
            raise ReaderNotFoundError("Không tìm thấy bộ truyện này.")

        chapters = self._readable_chapters(metadata.chapters)
        if not chapters:
            raise ReaderNotFoundError("Bộ truyện chưa có chương đã dịch.")
        return ReaderBookDetail(
            **self._to_book_summary(metadata, len(chapters)).model_dump(),
            chapters=[self._to_chapter_summary(novel_id, chapter) for chapter in chapters],
        )

    def get_chapter(self, novel_id: str, chapter_index: int) -> ReaderChapterResponse:
        self._validate_novel_id(novel_id)
        if chapter_index < 1:
            raise ReaderValidationError("chapter_index phải lớn hơn hoặc bằng 1.")

        metadata = self._library.get_novel(novel_id)
        if not metadata:
            raise ReaderNotFoundError("Không tìm thấy bộ truyện này.")

        chapters = self._readable_chapters(metadata.chapters)
        position = next(
            (index for index, chapter in enumerate(chapters) if chapter.chapter_index == chapter_index),
            None,
        )
        if position is None:
            raise ReaderNotFoundError("Chương này chưa được dịch hoặc không tồn tại.")

        content = self._library.get_chapter_content(novel_id, chapter_index, version="translated")
        if content is None or not content.strip():
            raise ReaderNotFoundError("Nội dung bản dịch của chương này chưa sẵn sàng.")

        chapter = chapters[position]
        return ReaderChapterResponse(
            novel_id=novel_id,
            chapter=self._to_chapter_summary(novel_id, chapter),
            content=content,
            previous_chapter=(
                self._to_chapter_summary(novel_id, chapters[position - 1]) if position > 0 else None
            ),
            next_chapter=(
                self._to_chapter_summary(novel_id, chapters[position + 1])
                if position < len(chapters) - 1
                else None
            ),
        )

    @classmethod
    def _validate_novel_id(cls, novel_id: str) -> None:
        if not cls._NOVEL_ID_PATTERN.fullmatch(novel_id or ""):
            raise ReaderValidationError("novel_id không hợp lệ.")

    @staticmethod
    def _readable_chapters(chapters: Sequence[ChapterItem]) -> List[ChapterItem]:
        return sorted(
            (
                chapter
                for chapter in chapters
                if chapter.status == ChapterStatus.COMPLETED
                and (
                    chapter.r2_translated_key
                    or chapter.r2_translated_url
                    or chapter.translated_text_preview
                )
            ),
            key=lambda chapter: chapter.chapter_index,
        )

    def _to_chapter_summary(self, novel_id: str, chapter: ChapterItem) -> ReaderChapterSummary:
        return ReaderChapterSummary(
            chapter_index=chapter.chapter_index,
            chapter_id=chapter.chapter_id,
            chapter_title=chapter.chapter_title,
            word_count=chapter.word_count,
            updated_at=chapter.updated_at,
            content_url=self._content_url(chapter),
        )

    def _content_url(self, chapter: ChapterItem) -> Optional[str]:
        resolver = getattr(self._library, "get_chapter_content_url", None)
        if not callable(resolver):
            return None
        return resolver(chapter, version="translated")

    @staticmethod
    def _to_book_summary(metadata: NovelMetadata, translated_count: int) -> ReaderBookSummary:
        return ReaderBookSummary(
            novel_id=metadata.novel_id,
            title=metadata.title,
            original_title=metadata.original_title,
            author=metadata.author,
            genre=metadata.genre,
            description=metadata.description,
            cover_url=metadata.cover_url,
            status=metadata.status,
            total_chapters=len(metadata.chapters),
            translated_chapters=translated_count,
        )


reader_service = ReaderService()

__all__ = [
    "ReaderError",
    "ReaderNotFoundError",
    "ReaderService",
    "ReaderValidationError",
    "reader_service",
]
