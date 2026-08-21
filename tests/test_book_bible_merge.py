import pytest
from app.schemas.character_profile import (
    BookMetadata,
    BookResolutionRequest,
    CharacterEventCandidate,
    EditionCreateRequest,
)
from app.services.character_profile_service import (
    CharacterProfileService,
    _clean_title,
    _token_similarity,
)


def test_clean_title_and_token_similarity():
    # 1. Clean title removes bracketed tags and punctuation
    t1 = "Ta Tại Bệnh Viện Tâm Thần Học Trảm Thần [AI]"
    t2 = "Trảm Thần: Ta Học Trảm Thần Ở Bệnh Viện Tâm Thần"
    
    assert _clean_title(t1) == "Ta Tại Bệnh Viện Tâm Thần Học Trảm Thần"
    assert "Trảm Thần" in _clean_title(t2)
    assert ":" not in _clean_title(t2)

    # 2. Token similarity between variants should be high
    sim = _token_similarity(t1, t2)
    assert sim >= 0.60, f"Expected similarity >= 0.60, got {sim}"


def test_resolve_book_matches_variant_titles_with_same_author():
    service = CharacterProfileService(min_independent_sources=2)

    # 1. Initial book creation (e.g. from Online Novel with [AI] tag)
    initial_res = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(
                title="Ta Tại Bệnh Viện Tâm Thần Học Trảm Thần [AI]",
                author="Tam Cửu Âm Vực",
                language="vi",
            ),
            create_if_missing=True,
        )
    )
    assert initial_res.status == "new_book"
    canonical_book_id = initial_res.book_id
    assert canonical_book_id == "ta-tai-benh-vien-tam-than-hoc-tram-than-ai"

    # 2. EPUB upload resolution with different title formatting and same author
    epub_res = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(
                title="Trảm Thần: Ta Học Trảm Thần Ở Bệnh Viện Tâm Thần",
                author="Tam Cửu Âm Vực",
                language="vi",
            ),
            create_if_missing=True,
        )
    )

    # Should match the existing book_id instead of creating a duplicate!
    assert epub_res.status == "matched"
    assert epub_res.book_id == canonical_book_id


def test_service_merge_books():
    service = CharacterProfileService(min_independent_sources=2)

    # Create Book A (source)
    res_a = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(title="Book Duplicate A", author="Author X", language="vi"),
            create_if_missing=True,
        )
    )
    book_a_id = res_a.book_id

    # Create Book B (target)
    res_b = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(title="Book Canonical B", author="Author X", language="vi"),
            create_if_missing=True,
        )
    )
    book_b_id = res_b.book_id

    # Create edition and event on Book A
    ed_a = service.create_edition(
        book_a_id,
        EditionCreateRequest(
            metadata=BookMetadata(title="Book Duplicate A", author="Author X", language="vi"),
            chapter_count=100,
        ),
    )
    candidate = CharacterEventCandidate(
        character_original_name="Trieu Khong Thanh",
        category="status",
        attribute_key="alive",
        operation="set",
        value=True,
        confidence=0.9,
    )
    sub = service.submit(
        book_id=book_a_id,
        edition_id=ed_a.edition_id,
        idempotency_key="sub-test-1",
        local_chapter_index=1,
        input_type="structured_events",
        content_fingerprint="fp1",
        candidates=[candidate],
    )

    assert service.editions[ed_a.edition_id].book_id == book_a_id
    assert service.submissions[sub.submission_id].book_id == book_a_id

    # Perform Merge
    success = service.merge_books(source_book_id=book_a_id, target_book_id=book_b_id)
    assert success is True

    # Book A must be removed from books dict
    assert book_a_id not in service.books
    assert book_b_id in service.books

    # Editions and submissions must be reparented to Book B
    assert service.editions[ed_a.edition_id].book_id == book_b_id
    assert service.submissions[sub.submission_id].book_id == book_b_id
    for ev in service.events.values():
        assert ev.book_id != book_a_id


def test_get_edition_fuzzy_title_fallback():
    service = CharacterProfileService(min_independent_sources=2)
    res = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(
                title="Ta Tại Bệnh Viện Tâm Thần Học Trảm Thần [AI]",
                author="Tam Cửu Âm Vực",
                language="vi",
            ),
            create_if_missing=True,
        )
    )
    book_id = res.book_id

    # Request edition by ad-hoc edition_id with variant title
    ed = service.get_edition(
        edition_id="edition-custom-test-12345",
        title="Trảm Thần: Ta Học Trảm Thần Ở Bệnh Viện Tâm Thần",
        author="Tam Cửu Âm Vực",
    )
    assert ed is not None
    assert ed.book_id == book_id
