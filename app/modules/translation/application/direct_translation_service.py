from typing import Optional, Tuple

from app.modules.translation.legacy_pipeline import LegacyTranslationPipelineService
from app.schemas.book_bible import BookBible


class DirectTranslationService:
    """Application use case for translating one text payload."""

    def __init__(self, legacy: LegacyTranslationPipelineService):
        self._legacy = legacy

    async def translate(
        self,
        text: str,
        bible: Optional[BookBible] = None,
        chapter_index: Optional[int] = None,
        chapter_id: Optional[str] = None,
    ) -> Tuple[str, BookBible]:
        return await self._legacy.translate_direct_text(
            text,
            bible=bible,
            chapter_index=chapter_index,
            chapter_id=chapter_id,
        )
