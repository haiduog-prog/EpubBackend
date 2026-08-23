from app.modules.character_profiles.legacy_service import CharacterProfileService


class EditionService:
    def __init__(self, profile_service: CharacterProfileService):
        self._service = profile_service

    def get(self, edition_id, **kwargs):
        return self._service.get_edition(edition_id, **kwargs)

    def create(self, book_id, request):
        return self._service.create_edition(book_id, request)

    def put_mapping(self, edition_id, request):
        return self._service.put_mapping(edition_id, request)

    def get_mapping(self, edition_id, local_chapter_index):
        return self._service.get_mapping(edition_id, local_chapter_index)
