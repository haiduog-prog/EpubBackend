from app.modules.character_profiles.legacy_service import CharacterProfileService


class SnapshotService:
    def __init__(self, profile_service: CharacterProfileService):
        self._service = profile_service

    def get(self, edition_id, local_chapter):
        return self._service.snapshot(edition_id, local_chapter)

    def timeline(self, edition_id, local_chapter, character_id):
        return self._service.timeline(edition_id, local_chapter, character_id)
