from typing import Any, Dict, List, Optional

from app.modules.library.legacy_service import LegacyLibraryService
from app.schemas.book_bible import BookBible
from app.schemas.library import ChapterItem


class ChapterService:
    """Owns chapter content, chapter translation and chapter-derived views."""

    def __init__(self, legacy: LegacyLibraryService):
        self._legacy = legacy

    def save(self, novel_id: str, chapter_index: int, title: str, content: str) -> ChapterItem:
        return self._legacy.add_or_update_chapter(novel_id, chapter_index, title, content)

    def content(self, novel_id: str, chapter_index: int, version: str = "translated") -> Optional[str]:
        return self._legacy.get_chapter_content(novel_id, chapter_index, version)

    def content_url(self, chapter: ChapterItem, version: str = "translated") -> Optional[str]:
        return self._legacy.get_chapter_content_url_for_item(chapter, version)

    def content_urls(self, chapter: ChapterItem, version: str = "translated") -> List[str]:
        return self._legacy.get_chapter_content_urls_for_item(chapter, version)

    async def translate(self, novel_id: str, chapter_index: int, **kwargs) -> ChapterItem:
        return await self._legacy.translate_chapter(novel_id, chapter_index, **kwargs)

    async def scan(self, novel_id: str, **kwargs) -> BookBible:
        return await self._legacy.scan_characters_and_timeline(novel_id, **kwargs)

    def bible(self, novel_id: str) -> BookBible:
        return self._legacy.get_novel_bible(novel_id)

    def snapshot(self, novel_id: str, chapter_index: int) -> Dict[str, Any]:
        return self._legacy.get_character_snapshot_at_chapter(novel_id, chapter_index)
