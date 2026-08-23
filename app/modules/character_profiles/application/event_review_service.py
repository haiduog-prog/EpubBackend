from app.modules.character_profiles.legacy_service import CharacterProfileService


class EventReviewService:
    def __init__(self, profile_service: CharacterProfileService):
        self._service = profile_service

    def list(self, **kwargs):
        return self._service.list_events(**kwargs)

    def approve(self, event_id, **kwargs):
        return self._service.approve_event(event_id, **kwargs)

    def update(self, event_id, **kwargs):
        return self._service.update_event(event_id, **kwargs)

    def reject(self, event_id):
        return self._service.reject_event(event_id)

    def approve_all(self, **kwargs):
        return self._service.approve_all_pending(**kwargs)

    def settings(self):
        return self._service.get_settings()

    def update_settings(self, **kwargs):
        return self._service.update_settings(**kwargs)
