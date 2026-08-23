from typing import Any

from app.modules.translation.application.direct_translation_service import DirectTranslationService
from app.modules.translation.application.epub_translation_service import EpubTranslationService
from app.modules.translation.application.txt_translation_service import TxtTranslationService
from app.modules.translation.legacy_pipeline import LegacyTranslationPipelineService


class TranslationPipelineService:
    """Stable facade composed from direct, TXT and EPUB translators."""

    def __init__(self, llm_client):
        self._legacy = LegacyTranslationPipelineService(llm_client)
        self.direct = DirectTranslationService(self._legacy)
        self.txt = TxtTranslationService(self._legacy)
        self.epub = EpubTranslationService(self._legacy)

    async def extract_initial_book_bible(self, *args, **kwargs):
        return await self._legacy.extract_initial_book_bible(*args, **kwargs)

    async def translate_direct_text(self, *args, **kwargs):
        return await self.direct.translate(*args, **kwargs)

    async def translate_txt_file(self, *args, **kwargs):
        return await self.txt.translate(*args, **kwargs)

    async def translate_epub_file(self, *args, **kwargs):
        return await self.epub.translate(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._legacy, name)


__all__ = ["TranslationPipelineService"]
