from typing import Optional

from app.modules.library.legacy_service import LegacyLibraryService
from app.schemas.library import ImportJobStatus, NovelMetadata


class EpubImportService:
    """Owns synchronous and background EPUB imports plus recovery."""

    def __init__(self, legacy: LegacyLibraryService):
        self._legacy = legacy

    def import_file(self, epub_bytes: bytes, **kwargs) -> NovelMetadata:
        return self._legacy.import_epub_novel(epub_bytes, **kwargs)

    def start(self, epub_bytes: bytes, **kwargs) -> ImportJobStatus:
        return self._legacy.start_import_epub_async(epub_bytes, **kwargs)

    def get_job(self, job_id: str) -> Optional[ImportJobStatus]:
        return self._legacy.get_import_job(job_id)

    def recover(self) -> None:
        self._legacy.recover_import_jobs()
