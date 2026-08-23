from typing import Any

from app.modules.character_profiles.application.book_service import BookService
from app.modules.character_profiles.application.edition_service import EditionService
from app.modules.character_profiles.application.event_review_service import EventReviewService
from app.modules.character_profiles.application.snapshot_service import SnapshotService
from app.modules.character_profiles.application.submission_service import SubmissionService
from app.modules.character_profiles.legacy_service import CharacterProfileService


class CharacterProfileApplication:
    """Application facade for the book, edition, submission and review use cases."""

    def __init__(self, *args, **kwargs):
        self._service = CharacterProfileService(*args, **kwargs)
        self.books = BookService(self._service)
        self.editions = EditionService(self._service)
        self.submissions = SubmissionService(self._service)
        self.events = EventReviewService(self._service)
        self.snapshots = SnapshotService(self._service)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)


__all__ = ["CharacterProfileApplication"]
