from typing import Any, Dict, Optional

from app.modules.library.application.chapter_service import ChapterService
from app.modules.library.application.epub_export_service import EpubExportService
from app.modules.library.application.epub_import_service import EpubImportService
from app.modules.library.application.novel_service import NovelService
from app.modules.library.legacy_service import (
    LegacyLibraryService,
    legacy_library_service,
    parse_chapter_index_from_title,
    slugify,
)


class LibraryService:
    """Stable application facade composed from library use cases."""

    def __init__(self, legacy: Optional[LegacyLibraryService] = None):
        self._legacy = legacy or LegacyLibraryService()
        self.novels = NovelService(self._legacy)
        self.chapters = ChapterService(self._legacy)
        self.imports = EpubImportService(self._legacy)
        self.exports = EpubExportService(self._legacy)

    def create_novel(self, request, **kwargs):
        return self.novels.create(request, **kwargs)

    def get_novel(self, novel_id):
        return self.novels.get(novel_id)

    def list_novels(self):
        return self.novels.list()

    def update_novel(self, novel_id, request):
        return self.novels.update(novel_id, request)

    def delete_novel(self, novel_id):
        return self.novels.delete(novel_id)

    def add_or_update_chapter(self, novel_id, chapter_index, chapter_title, content):
        return self.chapters.save(novel_id, chapter_index, chapter_title, content)

    def get_chapter_content(self, novel_id, chapter_index, version="translated"):
        return self.chapters.content(novel_id, chapter_index, version)

    def get_chapter_content_url(self, chapter, version="translated"):
        return self.chapters.content_url(chapter, version)

    def get_chapter_content_urls(self, chapter, version="translated"):
        return self.chapters.content_urls(chapter, version)

    async def translate_chapter(self, novel_id, chapter_index, **kwargs):
        return await self.chapters.translate(novel_id, chapter_index, **kwargs)

    async def scan_characters_and_timeline(self, novel_id, **kwargs):
        return await self.chapters.scan(novel_id, **kwargs)

    def get_novel_bible(self, novel_id):
        return self.chapters.bible(novel_id)

    def get_character_snapshot_at_chapter(self, novel_id, chapter_index):
        return self.chapters.snapshot(novel_id, chapter_index)

    def export_full_epub(self, novel_id, output_path=None):
        return self.exports.export(novel_id, output_path)

    def import_epub_novel(self, epub_bytes: bytes, **kwargs):
        return self.imports.import_file(epub_bytes, **kwargs)

    def start_import_epub_async(self, epub_bytes: bytes, **kwargs):
        return self.imports.start(epub_bytes, **kwargs)

    def get_import_job(self, job_id: str):
        return self.imports.get_job(job_id)

    def recover_import_jobs(self):
        return self.imports.recover()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._legacy, name)


library_service = LibraryService(legacy_library_service)

__all__ = [
    "LibraryService",
    "library_service",
    "parse_chapter_index_from_title",
    "slugify",
]
