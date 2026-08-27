import pytest

from app.llm.errors import StructuredOutputError
from app.modules.book_bible.legacy_service import LegacyBookBibleService
from app.modules.reader.schemas import ReaderChapterSummary
from app.modules.translation.legacy_pipeline import LegacyTranslationPipelineService
from app.schemas.book_bible import BookBible, BookBibleDelta, TermEntry
from app.modules.translation.application.terminology_consistency_service import (
    TerminologyConsistencyService,
)


def test_short_generic_term_does_not_require_canonical_name():
    bible = BookBible(terms=[TermEntry(original_name="熊", vi_name="Hùng", category="item")])

    issues = TerminologyConsistencyService.check_translation("熊 xuất hiện", "Con gấu xuất hiện", bible)

    assert issues == []


def test_unlocked_term_can_be_improved_without_forbidding_new_name():
    bible = BookBible(
        terms=[TermEntry(original_name="金毛暴熊", vi_name="Gấu Lông Vàng", locked=False)]
    )
    delta = BookBibleDelta(
        new_terms=[TermEntry(original_name="金毛暴熊", vi_name="Kim Mao Bạo Hùng")]
    )

    merged = LegacyBookBibleService.merge_delta(bible, delta)
    term = merged.terms[0]

    assert term.vi_name == "Kim Mao Bạo Hùng"
    assert term.aliases == ["Gấu Lông Vàng"]
    assert term.forbidden_variants == []


class _MalformedExtractor:
    async def extract_book_bible_delta(self, source_text, known_names_index):
        raise StructuredOutputError("malformed JSON", operation="extract_book_bible_delta")


@pytest.mark.asyncio
async def test_legacy_enrichment_remains_fail_open_on_structured_output_error():
    service = LegacyTranslationPipelineService(_MalformedExtractor())

    delta = await service._extract_delta_fail_open("source", "known")

    assert isinstance(delta, BookBibleDelta)
    assert delta.new_terms == []


def test_reader_summary_accepts_missing_updated_at():
    summary = ReaderChapterSummary(chapter_index=1, chapter_title="Chương 1", updated_at=None)

    assert summary.updated_at is None
