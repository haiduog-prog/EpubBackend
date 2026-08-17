from app.schemas.book_bible import BookBibleDelta, CharacterEntry
from app.schemas.character_profile import (
    BookMetadata,
    BookResolutionRequest,
    ChapterMappingRequest,
    CharacterEventCandidate,
    EditionCreateRequest,
)
from app.services.character_profile_service import CharacterProfileService


def _service_and_edition():
    service = CharacterProfileService(min_independent_sources=2)
    resolved = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(title="Novel", author="Author", language="vi"),
            create_if_missing=True,
        )
    )
    edition = service.create_edition(
        resolved.book_id,
        EditionCreateRequest(
            metadata=BookMetadata(title="Novel", author="Author", language="vi"),
            chapter_count=300,
        ),
    )
    return service, resolved.book_id, edition.edition_id


def _submit(service, book_id, edition_id, chapter, fingerprint, candidate, key=None):
    return service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key=key or f"key-{chapter}-{fingerprint}",
        local_chapter_index=chapter,
        input_type="structured_events",
        content_fingerprint=fingerprint,
        candidates=[candidate],
    )


def test_event_requires_two_independent_source_groups_before_approval():
    service, book_id, edition_id = _service_and_edition()
    candidate = CharacterEventCandidate(
        character_original_name="A",
        category="realm",
        attribute_key="current",
        operation="set",
        value="Truc Co",
        confidence=0.95,
    )

    first = _submit(service, book_id, edition_id, 10, "source-a", candidate)
    assert first.status == "completed"
    assert {event.status for event in service.events.values()} == {"pending"}
    assert service.snapshot(edition_id, 10).characters == []

    second = _submit(service, book_id, edition_id, 10, "source-b", candidate)
    assert second.status == "completed"
    assert {event.status for event in service.events.values()} == {"approved"}
    snapshot = service.snapshot(edition_id, 10)
    assert snapshot.characters[0].attributes["current"] == "Truc Co"


def test_snapshot_never_uses_future_character_event():
    service, book_id, edition_id = _service_and_edition()
    earlier = CharacterEventCandidate(
        character_original_name="A",
        category="item",
        attribute_key="weapon",
        operation="set",
        value="Old Sword",
        confidence=0.9,
    )
    later = earlier.model_copy(update={"value": "Future Sword"})
    for fingerprint in ("a1", "a2"):
        _submit(service, book_id, edition_id, 50, fingerprint, earlier)
    for fingerprint in ("b1", "b2"):
        _submit(service, book_id, edition_id, 200, fingerprint, later)

    at_100 = service.snapshot(edition_id, 100)
    at_200 = service.snapshot(edition_id, 200)
    assert at_100.characters[0].attributes["weapon"] == "Old Sword"
    assert at_200.characters[0].attributes["weapon"] == "Future Sword"


def test_submission_is_idempotent_and_mapping_controls_snapshot_boundary():
    service, book_id, edition_id = _service_and_edition()
    service.put_mapping(
        edition_id,
        ChapterMappingRequest(
            local_chapter_index=1,
            canonical_chapter_start=10,
            canonical_chapter_end=12,
        ),
    )
    candidate = CharacterEventCandidate(
        character_original_name="A",
        category="status",
        attribute_key="state",
        operation="set",
        value="alive",
        confidence=0.9,
    )
    first = _submit(service, book_id, edition_id, 1, "same", candidate, key="stable")
    second = _submit(service, book_id, edition_id, 1, "same", candidate, key="stable")
    assert first.submission_id == second.submission_id
    assert len(service.submissions) == 1
    snapshot = service.snapshot(edition_id, 1)
    assert snapshot.canonical_chapter == 12


def test_legacy_delta_can_enter_the_new_timeline():
    service, book_id, edition_id = _service_and_edition()
    submission = service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="legacy-1",
        local_chapter_index=4,
        input_type="chapter_text",
        content_fingerprint="legacy-content",
        content="chapter",
    )
    updated = service.process_legacy_delta(
        submission.submission_id,
        BookBibleDelta(new_characters=[CharacterEntry(original_name="A", vi_name="A")]),
    )
    assert updated.status == "completed"
    assert len(service.events) == 1
    assert next(iter(service.events.values())).category == "identity"

