from app.modules.library.persistence.legacy_repository import LibraryRepository


class TranslationJobRepository(LibraryRepository):
    """Translation-owned view of the shared job persistence adapter."""
