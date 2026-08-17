import pytest
from app.schemas.book_bible import BookBible, CharacterEntry, PlaceEntry, TermEntry
from app.services import BookBibleService


def test_filter_bible_for_text():
    full_bible = BookBible(
        characters=[
            CharacterEntry(original_name="萧炎", vi_name="Tiêu Viêm"),
            CharacterEntry(original_name="美杜莎", vi_name="Mỹ Đỗ Toa"),
            CharacterEntry(original_name="纳兰嫣然", vi_name="Nạp Lan Yên Nhiên")
        ],
        places=[
            PlaceEntry(original_name="乌坦城", vi_name="Ô Tản Thành"),
            PlaceEntry(original_name="魔兽山脉", vi_name="Ma Thú Sơn Mạch")
        ],
        terms=[
            TermEntry(original_name="斗帝", vi_name="Đấu Đế"),
            TermEntry(original_name="异火", vi_name="Dị Hỏa")
        ]
    )

    chunk_text = "Tại 乌坦城, 萧炎 đã tìm thấy một loại 异火 quý hiếm."
    filtered = BookBibleService.filter_bible_for_text(full_bible, chunk_text)

    # Should only contain 萧炎, 乌坦城, and 异火
    assert len(filtered.characters) == 1
    assert filtered.characters[0].original_name == "萧炎"
    assert len(filtered.places) == 1
    assert filtered.places[0].original_name == "乌坦城"
    assert len(filtered.terms) == 1
    assert filtered.terms[0].original_name == "异火"
