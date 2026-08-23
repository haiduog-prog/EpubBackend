"""Backward-compatible imports for the library bounded context."""

from app.modules.library.application.facade import (
    LibraryService,
    library_service,
    parse_chapter_index_from_title,
    slugify,
)

__all__ = [
    "LibraryService",
    "library_service",
    "parse_chapter_index_from_title",
    "slugify",
]
