import pytest
from app.schemas.book_bible import (
    AddressTerm,
    BookBible,
    BookBibleDelta,
    CharacterEntry,
    PlaceEntry,
    TermEntry,
)
from app.schemas.character_profile import (
    BookMetadata,
    BookResolutionRequest,
    ChapterMappingRequest,
    ChapterSubmissionRequest,
    CharacterEventCandidate,
    EditionCreateRequest,
)
from app.services.book_bible_service import BookBibleService
from app.services.character_profile_service import CharacterProfileService


def test_merge_delta_cjk_vietnamese_deduplication():
    """Kiểm tra gộp 2 bản ghi: một bản ghi chỉ có tiếng Việt và một bản ghi có tiếng Trung."""
    bible = BookBible(
        novel_id="test-novel",
        characters=[
            CharacterEntry(
                original_name="Tốn Bác",
                vi_name="Tốn Bác",
                role="Lão bộc",
            )
        ],
    )

    delta = BookBibleDelta(
        new_characters=[
            CharacterEntry(
                original_name="损伯",
                vi_name="Tốn Bác",
                role="Lão bộc",
                voice_notes="Từ tốn",
                address_terms=[
                    AddressTerm(with_person="Gia chủ", self="lão nô", other="Lão gia", context="kính trọng")
                ],
            )
        ]
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert len(merged.characters) == 1
    # original_name phải được nâng cấp thành chữ Hán nguyên tác
    assert merged.characters[0].original_name == "损伯"
    assert merged.characters[0].vi_name == "Tốn Bác"
    assert merged.characters[0].voice_notes == "Từ tốn"
    assert len(merged.characters[0].address_terms) == 1
    assert "Tốn Bác" in merged.characters[0].aliases or merged.characters[0].vi_name == "Tốn Bác"


def test_merge_delta_reverse_order():
    """Kiểm tra khi chữ Hán có trước, bản ghi sau chỉ có tiếng Việt."""
    bible = BookBible(
        novel_id="test-novel",
        characters=[
            CharacterEntry(
                original_name="损伯",
                vi_name="Tốn Bác",
                role="Lão bộc",
            )
        ],
    )

    delta = BookBibleDelta(
        new_characters=[
            CharacterEntry(
                original_name="Tốn Bác",
                vi_name="Tốn Bác",
                voice_notes="Hiền từ",
            )
        ]
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert len(merged.characters) == 1
    assert merged.characters[0].original_name == "损伯"
    assert merged.characters[0].vi_name == "Tốn Bác"
    assert "Hiền từ" in merged.characters[0].voice_notes


def test_merge_places_and_terms_deduplication():
    """Kiểm tra gộp địa danh và thuật ngữ khi trùng tên tiếng Việt hoặc tên Hán."""
    bible = BookBible(
        novel_id="test-novel",
        places=[PlaceEntry(original_name="Ô Tản Thành", vi_name="Ô Tản Thành", notes="Thành nhỏ")],
        terms=[TermEntry(original_name="Đấu Giả", vi_name="Đấu Giả", category="Cảnh giới")],
    )

    delta = BookBibleDelta(
        new_places=[PlaceEntry(original_name="乌坦城", vi_name="Ô Tản Thành", notes="Gia tộc Tiêu thị")],
        new_terms=[TermEntry(original_name="斗者", vi_name="Đấu Giả", category="Cảnh giới", notes="1-9 tinh")],
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert len(merged.places) == 1
    assert merged.places[0].original_name == "乌坦城"
    assert merged.places[0].vi_name == "Ô Tản Thành"
    assert "Gia tộc Tiêu thị" in merged.places[0].notes

    assert len(merged.terms) == 1
    assert merged.terms[0].original_name == "斗者"
    assert merged.terms[0].vi_name == "Đấu Giả"
    assert "1-9 tinh" in merged.terms[0].notes


def test_snapshot_entity_resolution_deduplication():
    """Kiểm tra CharacterProfileService.snapshot tự động gộp 2 thực thể trùng tên tiếng Việt."""
    service = CharacterProfileService(auto_approve=True, min_independent_sources=1)
    resolved = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(title="Test Novel", author="Author", language="vi"),
            create_if_missing=True,
        )
    )
    edition = service.create_edition(
        resolved.book_id,
        EditionCreateRequest(
            metadata=BookMetadata(title="Test Novel", author="Author", language="vi"),
            chapter_count=100,
        ),
    )
    edition_id = edition.edition_id
    book_id = resolved.book_id

    # Chương 1: Trích xuất có tiếng Trung "损伯"
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-1",
        local_chapter_index=1,
        input_type="structured_events",
        content_fingerprint="fp-1",
        candidates=[
            CharacterEventCandidate(
                character_original_name="损伯",
                category="identity",
                attribute_key="profile",
                operation="set",
                value={"vi_name": "Tốn Bác", "role": "Lão bộc", "aliases": []},
                certainty="observed",
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="损伯",
                category="relationship",
                attribute_key="address_terms",
                operation="add",
                value={"with": "Thiếu gia", "self": "lão nô", "other": "Thiếu gia"},
                certainty="observed",
                confidence=0.9,
            ),
        ],
    )

    # Chương 2: Trích xuất chỉ có tiếng Việt "Tốn Bác"
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-2",
        local_chapter_index=2,
        input_type="structured_events",
        content_fingerprint="fp-2",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Tốn Bác",
                category="identity",
                attribute_key="profile",
                operation="set",
                value={"vi_name": "Tốn Bác", "voice_notes": "Ấm áp, trung thành", "aliases": ["Tốn Lão"]},
                certainty="observed",
                confidence=0.9,
            )
        ],
    )

    # Lấy snapshot tại chương 2
    snap_resp = service.snapshot(edition_id, local_chapter_index=2)

    # Snapshot chỉ được có đúng 1 nhân vật duy nhất
    assert len(snap_resp.characters) == 1
    char = snap_resp.characters[0]
    assert char.original_name == "损伯"
    assert char.attributes.get("profile", {}).get("vi_name") == "Tốn Bác"
    assert char.attributes.get("profile", {}).get("role") == "Lão bộc"
    assert "Ấm áp" in char.attributes.get("profile", {}).get("voice_notes", "")
    assert "Tốn Lão" in char.attributes.get("profile", {}).get("aliases", [])
