from app.modules.character_profiles.legacy_service import CharacterProfileService


class BookService:
    def __init__(self, profile_service: CharacterProfileService):
        self._service = profile_service

    def resolve(self, request):
        return self._service.resolve_book(request)

    def update(self, book_id, request):
        return self._service.update_book(book_id, request)

    def delete(self, book_id):
        return self._service.delete_book(book_id)

    def merge(self, source_book_id, target_book_id):
        return self._service.merge_books(source_book_id, target_book_id)

    def list(self):
        return self._service.list_books()
