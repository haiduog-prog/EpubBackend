from app.modules.character_profiles.legacy_service import CharacterProfileService


class SubmissionService:
    def __init__(self, profile_service: CharacterProfileService):
        self._service = profile_service

    def submit(self, **kwargs):
        return self._service.submit(**kwargs)

    def process_candidates(self, **kwargs):
        return self._service.process_candidates(**kwargs)

    def process_legacy_delta(self, *args, **kwargs):
        return self._service.process_legacy_delta(*args, **kwargs)

    def get(self, submission_id):
        return self._service.get_submission(submission_id)

    def fail(self, submission_id, error_code, message):
        return self._service.fail_submission(submission_id, error_code, message)

    def known_names(self, book_id):
        return self._service.known_names_index(book_id)
