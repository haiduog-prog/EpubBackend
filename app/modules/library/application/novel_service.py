from typing import List, Optional

from app.modules.library.legacy_service import LegacyLibraryService
from app.schemas.library import NovelCreateRequest, NovelMetadata, NovelSummary, NovelUpdateRequest


class NovelService:
    """Owns novel metadata and lifecycle operations."""

    def __init__(self, legacy: LegacyLibraryService):
        self._legacy = legacy

    def create(self, request: NovelCreateRequest, **kwargs) -> NovelMetadata:
        return self._legacy.create_novel(request, **kwargs)

    def get(self, novel_id: str) -> Optional[NovelMetadata]:
        return self._legacy.get_novel(novel_id)

    def list(self) -> List[NovelSummary]:
        return self._legacy.list_novels()

    def update(self, novel_id: str, request: NovelUpdateRequest) -> Optional[NovelMetadata]:
        return self._legacy.update_novel(novel_id, request)

    def delete(self, novel_id: str) -> bool:
        return self._legacy.delete_novel(novel_id)
