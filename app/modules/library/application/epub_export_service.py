from typing import Optional

from app.modules.library.legacy_service import LegacyLibraryService


class EpubExportService:
    """Owns EPUB assembly from persisted novel chapters."""

    def __init__(self, legacy: LegacyLibraryService):
        self._legacy = legacy

    def export(self, novel_id: str, output_path: Optional[str] = None) -> str:
        return self._legacy.export_full_epub(novel_id, output_path)
