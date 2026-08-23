from app.modules.book_bible.legacy_service import LegacyBookBibleService


class BookBibleService(LegacyBookBibleService):
    """Application facade for deterministic Book Bible operations."""


__all__ = ["BookBibleService"]
