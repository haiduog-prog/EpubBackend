from typing import Dict

from app.modules.character_profiles.legacy_service import CharacterProfileService
from app.schemas.character_profile import CharacterEvent, CharacterSnapshot


class SnapshotReducer:
    """Chapter-bounded projection reducer for character events."""

    @staticmethod
    def apply(states: Dict[str, CharacterSnapshot], event: CharacterEvent) -> None:
        CharacterProfileService._apply_event(states, event)

    @staticmethod
    def complete_through(processed: set[int], through: int):
        return CharacterProfileService._contiguous_complete_through(processed, through)
