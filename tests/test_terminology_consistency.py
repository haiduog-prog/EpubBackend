import pytest

from app.modules.book_bible.legacy_service import LegacyBookBibleService
from app.modules.translation.application.terminology_consistency_service import (
    TerminologyConsistencyService,
)
from app.schemas.book_bible import BookBible, BookBibleDelta, TermEntry


def test_build_windows_scans_full_text_with_overlap():
    text = "a" * 19
    windows = TerminologyConsistencyService.build_windows(
        text, threshold=10, window_size=10, overlap=2
    )

    assert windows[0] == text[:10]
    assert windows[1] == text[8:18]
    assert "".join([]) == ""
    assert "".join(windows) != text  # overlap is intentional, not truncation
    assert windows[-1].endswith(text[-3:])


def test_check_translation_rejects_inconsistent_beast_name():
    bible = BookBible(
        novel_id="demo",
        terms=[
            TermEntry(
                original_name="金毛暴熊",
                vi_name="Kim Mao Bạo Hùng",
                category="beast_species",
                forbidden_variants=["lông vàng Bạo Hùng", "tóc vàng bạo hùng"],
            )
        ],
    )

    issues = TerminologyConsistencyService.check_translation(
        "金毛暴熊 xuất hiện", "lông vàng Bạo Hùng xuất hiện", bible
    )

    assert any("canonical" in issue.issue for issue in issues)
    assert any(issue.found == "lông vàng Bạo Hùng" for issue in issues)


def test_locked_term_keeps_canonical_and_records_conflicting_proposal():
    bible = BookBible(
        novel_id="demo",
        terms=[
            TermEntry(
                original_name="金毛暴熊",
                vi_name="Kim Mao Bạo Hùng",
                locked=True,
            )
        ],
    )
    delta = BookBibleDelta(
        new_terms=[
            TermEntry(original_name="金毛暴熊", vi_name="Lông Vàng Bạo Hùng")
        ]
    )

    merged = LegacyBookBibleService.merge_delta(bible, delta, chapter_index=4)
    term = merged.terms[0]

    assert term.vi_name == "Kim Mao Bạo Hùng"
    assert "Lông Vàng Bạo Hùng" in term.forbidden_variants


class _FakeExtractor:
    def __init__(self):
        self.windows = []

    async def extract_book_bible_delta(self, source_text, known_names, model=None):
        self.windows.append(source_text)
        return BookBibleDelta()


@pytest.mark.asyncio
async def test_scan_full_chapter_calls_every_window():
    client = _FakeExtractor()
    result = await TerminologyConsistencyService.scan_full_chapter(
        client,
        BookBible(novel_id="demo"),
        "x" * 25,
        threshold=10,
        window_size=10,
        overlap=2,
    )

    assert result.complete is True
    assert result.windows_scanned == len(client.windows) == 4
