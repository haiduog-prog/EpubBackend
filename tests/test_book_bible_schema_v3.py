import pytest

from app.modules.book_bible.domain.schema_migration import (
    migrate_book_bible_dict_to_v3,
    migrate_book_bible_to_v3,
)
from app.modules.book_bible.schemas import BookBible, CharacterEntry, TermEntry, StyleGuide


def test_migrate_v2_payload_to_v3_with_safe_defaults():
    v2_payload = {
        "novel_id": "van-thu-chien-than",
        "schema_version": 2,
        "bible_revision": 7,
        "characters": [
            {
                "character_id": "c-1",
                "original_name": "杜峰",
                "vi_name": "Đỗ Phong",
                "aliases": ["Phong Nhi"],
                "address_terms": [
                    {
                        "with": "段庆羽",
                        "self": "ta",
                        "other": "lão yêu bà",
                        "context": "Khi tra tấn",
                    }
                ],
            }
        ],
        "terms": [
            {
                "original_name": "Khí Võ Cảnh",
                "vi_name": "Khí Võ Cảnh",
                "category": "realm",
                "locked": True,
                "forbidden_variants": ["Tụ Võ Cảnh"],
            }
        ],
        "style_guide": {
            "genre": "Tiên hiệp",
            "tone": "Hào hùng",
            "era_setting": "Cổ phong",
        },
    }

    bible_v3 = migrate_book_bible_to_v3(v2_payload)

    assert bible_v3.schema_version == 3
    assert bible_v3.bible_revision == 7
    assert bible_v3.source_profile.language == "zh"
    assert bible_v3.source_profile.mode == "translate"
    assert bible_v3.scan_state == {}

    # Check character
    char = bible_v3.characters[0]
    assert char.original_name == "杜峰"
    assert char.vi_name == "Đỗ Phong"
    assert char.aliases == ["Phong Nhi"]
    assert char.narrative_term == ""
    assert char.forbidden_variants == []
    assert len(char.address_terms) == 1
    assert char.address_terms[0].with_person == "段庆羽"

    # Check term
    term = bible_v3.terms[0]
    assert term.original_name == "Khí Võ Cảnh"
    assert term.vi_name == "Khí Võ Cảnh"
    assert term.locked is True
    assert term.forbidden_variants == ["Tụ Võ Cảnh"]
    assert term.family == ""
    assert term.rank_order is None
    assert term.confidence == 1.0

    # Check style guide defaults
    assert bible_v3.style_guide.era_setting == "Cổ phong"
    assert bible_v3.style_guide.pronoun_policy == "ancient"
    assert bible_v3.style_guide.source_mode == "translate"


def test_roundtrip_v3_preserves_all_metadata_and_locks():
    initial = BookBible(
        novel_id="test-novel",
        schema_version=3,
        bible_revision=10,
        characters=[
            CharacterEntry(
                character_id="c-2",
                original_name="Mộc Linh",
                vi_name="Mộc Linh",
                forbidden_variants=["Mộc Cảnh Nam"],
                narrative_term="nàng",
                locked=True,
            )
        ],
        terms=[
            TermEntry(
                original_name="Khí Võ Cảnh",
                vi_name="Khí Võ Cảnh",
                family="cultivation_realm",
                rank_order=1,
                evidence="Chương 120 dòng 15",
                confidence=0.98,
                locked=True,
                forbidden_variants=["Tụ Võ Cảnh"],
            )
        ],
    )

    dumped = initial.model_dump(by_alias=True)
    reloaded = migrate_book_bible_to_v3(dumped)

    assert reloaded.schema_version == 3
    assert reloaded.bible_revision == 10
    assert reloaded.characters[0].forbidden_variants == ["Mộc Cảnh Nam"]
    assert reloaded.characters[0].narrative_term == "nàng"
    assert reloaded.characters[0].locked is True

    term = reloaded.terms[0]
    assert term.family == "cultivation_realm"
    assert term.rank_order == 1
    assert term.evidence == "Chương 120 dòng 15"
    assert term.confidence == 0.98
    assert term.locked is True
    assert term.forbidden_variants == ["Tụ Võ Cảnh"]
