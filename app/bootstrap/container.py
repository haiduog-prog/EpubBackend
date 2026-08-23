from dataclasses import dataclass
from typing import Optional

from app.infrastructure.storage.bible_store import BibleStore
from app.infrastructure.storage.facade import StorageRepository, storage_repo
from app.infrastructure.storage.job_store import JobStore
from app.modules.character_profiles.application.facade import CharacterProfileApplication
from app.modules.library.application.facade import LibraryService


@dataclass
class ApplicationContainer:
    storage: StorageRepository
    jobs: JobStore
    bible: BibleStore
    library: LibraryService
    character_profiles: CharacterProfileApplication


def build_container(repository: Optional[StorageRepository] = None) -> ApplicationContainer:
    repository = repository or storage_repo
    return ApplicationContainer(
        storage=repository,
        jobs=JobStore(repository),
        bible=BibleStore(repository),
        library=LibraryService(),
        character_profiles=CharacterProfileApplication(
            firestore_db=repository.firestore_db,
            storage_repo=repository,
        ),
    )
