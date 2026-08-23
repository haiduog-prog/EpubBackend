from typing import Any, Callable, Optional

from app.modules.translation.legacy_pipeline import LegacyTranslationPipelineService
from app.schemas.book_bible import BookBible


class TxtTranslationService:
    """Application use case for TXT translation."""

    def __init__(self, legacy: LegacyTranslationPipelineService):
        self._legacy = legacy

    async def translate(
        self,
        input_path: str,
        output_path: str,
        bible: Optional[BookBible] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        on_bible_updated: Optional[Callable[[BookBible], Any]] = None,
        **kwargs,
    ) -> BookBible:
        return await self._legacy.translate_txt_file(
            input_path,
            output_path,
            bible=bible,
            progress_callback=progress_callback,
            on_bible_updated=on_bible_updated,
            **kwargs,
        )
