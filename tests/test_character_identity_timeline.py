from app.schemas.character_profile import (
    BookMetadata,
    BookResolutionRequest,
    CharacterEventCandidate,
    EditionCreateRequest,
)
from app.services.character_profile_service import CharacterProfileService


def test_identity_link_only_merges_after_reveal_chapter():
    service = CharacterProfileService(min_independent_sources=2)
    book_id = service.resolve_book(
        BookResolutionRequest(metadata=BookMetadata(title="X"))
    ).book_id
    edition_id = service.create_edition(
        book_id,
        EditionCreateRequest(metadata=BookMetadata(title="X")),
    ).edition_id

    old_fact = CharacterEventCandidate(
        character_original_name="Masked Man",
        category="status",
        attribute_key="state",
        operation="set",
        value="unknown",
        confidence=0.9,
    )
    for fingerprint in ("old-a", "old-b"):
        service.submit(book_id, edition_id, fingerprint, 100, "structured_events", fingerprint, candidates=[old_fact])

    link = CharacterEventCandidate(
        character_original_name="Masked Man",
        category="identity",
        attribute_key="identity_link",
        operation="link",
        value={"target_original_name": "Hero"},
        confidence=0.95,
    )
    for fingerprint in ("link-a", "link-b"):
        service.submit(book_id, edition_id, fingerprint, 300, "structured_events", fingerprint, candidates=[link])

    before = service.snapshot(edition_id, 200)
    after = service.snapshot(edition_id, 300)
    assert len(before.characters) == 1
    assert before.characters[0].original_name == "Masked Man"
    assert len(after.characters) == 1
    assert after.characters[0].original_name == "Hero"
    assert after.characters[0].attributes["state"] == "unknown"

