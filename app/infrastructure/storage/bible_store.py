from typing import Optional

from app.infrastructure.storage.legacy_storage import StorageRepository, storage_repo
from app.schemas.book_bible import BookBible, BookBibleDelta


class BibleStore:
    """Structured persistence boundary for Book Bible state."""

    def __init__(self, repository: Optional[StorageRepository] = None):
        self._repository = repository or storage_repo

    def get(self, novel_id: str) -> Optional[BookBible]:
        return self._repository.get_bible(novel_id)

    def save(self, novel_id: str, bible: BookBible) -> None:
        self._repository.save_bible(novel_id, bible)

    def merge(self, novel_id: str, delta: BookBibleDelta, **kwargs) -> BookBible:
        return self._repository.merge_bible_delta(novel_id, delta, **kwargs)

    def delete(self, novel_id: str) -> bool:
        return self._repository.delete_bible(novel_id)
