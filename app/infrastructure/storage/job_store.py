from typing import List, Optional

from app.infrastructure.storage.legacy_storage import StorageRepository, storage_repo
from app.schemas.translation import TranslationJob


class JobStore:
    """Structured persistence boundary for translation jobs."""

    def __init__(self, repository: Optional[StorageRepository] = None):
        self._repository = repository or storage_repo

    def save(self, job: TranslationJob) -> None:
        self._repository.save_job(job)

    def get(self, job_id: str) -> Optional[TranslationJob]:
        return self._repository.get_job(job_id)

    def list(self) -> List[TranslationJob]:
        return self._repository.list_jobs()
