import pytest
from app.schemas.book_bible import (
    BookBible,
    BookBibleDelta,
    CharacterEntry,
    AddressTerm,
    AddressTermUpdate,
    PlaceEntry,
    TermEntry,
    StyleGuide
)
from app.services import BookBibleService


def test_merge_delta_new_character():
    bible = BookBible()
    delta = BookBibleDelta(
        new_characters=[
            CharacterEntry(
                original_name="萧炎",
                vi_name="Tiêu Viêm",
                role="Nam chính",
                voice_notes="Tự tin, kiên định",
                address_terms=[
                    AddressTerm(with_person="Huân Nhi", self="ta", other="Huân Nhi", context="thân mật")
                ]
            )
        ]
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert len(merged.characters) == 1
    assert merged.characters[0].original_name == "萧炎"
    assert merged.characters[0].vi_name == "Tiêu Viêm"
    assert len(merged.characters[0].address_terms) == 1


def test_merge_delta_update_existing_character_address_terms():
    bible = BookBible(
        characters=[
            CharacterEntry(
                original_name="萧炎",
                vi_name="Tiêu Viêm",
                role="Nam chính",
                address_terms=[
                    AddressTerm(with_person="Dược Lão", self="đệ tử", other="Sư phụ", context="bái sư")
                ]
            )
        ]
    )

    delta = BookBibleDelta(
        new_address_terms_for_existing=[
            AddressTermUpdate(
                character_original_name="萧炎",
                address_terms=[
                    AddressTerm(with_person="Dược Lão", self="ta", other="lão đầu", context="trêu chọc")
                ]
            )
        ]
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert len(merged.characters) == 1
    assert len(merged.characters[0].address_terms) == 2
    assert merged.characters[0].address_terms[1].other_term == "lão đầu"


def test_merge_delta_places_and_terms():
    bible = BookBible()
    delta = BookBibleDelta(
        new_places=[PlaceEntry(original_name="乌坦城", vi_name="Ô Tản Thành", notes="Thành phố xuất phát")],
        new_terms=[TermEntry(original_name="斗者", vi_name="Đấu Giả", category="Cảnh giới")],
        style_guide=StyleGuide(genre="Tiên hiệp", tone="Hào hùng", era_setting="Huyền ảo")
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert len(merged.places) == 1
    assert merged.places[0].vi_name == "Ô Tản Thành"
    assert len(merged.terms) == 1
    assert merged.terms[0].vi_name == "Đấu Giả"
    assert merged.style_guide.genre == "Tiên hiệp"
