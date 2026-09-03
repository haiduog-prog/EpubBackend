import pytest

from app.modules.book_bible.legacy_service import LegacyBookBibleService
from app.schemas.book_bible import (
    BookBible,
    BookBibleDelta,
    CharacterEntry,
    PendingBibleChange,
    SourceProfile,
    TermEntry,
)


def test_locked_term_rejects_llm_delta_mutation():
    bible = BookBible(
        novel_id="van-thu-test",
        bible_revision=1,
        terms=[
            TermEntry(
                original_name="Khí Võ Cảnh",
                vi_name="Khí Võ Cảnh",
                category="realm",
                locked=True,
                forbidden_variants=[],
            )
        ],
    )

    delta = BookBibleDelta(
        new_terms=[
            TermEntry(
                original_name="Khí Võ Cảnh",
                vi_name="Tụ Võ Cảnh",
                category="realm",
            )
        ]
    )

    merged = LegacyBookBibleService.merge_delta(bible, delta, chapter_index=123)
    term = merged.terms[0]

    # Canonical name MUST remain untouched
    assert term.vi_name == "Khí Võ Cảnh"
    # The bad variant must be tracked in forbidden_variants
    assert "Tụ Võ Cảnh" in term.forbidden_variants
    # A pending change is queued for human review
    assert len(merged.pending_changes) == 1
    assert merged.pending_changes[0].proposed_value == "Tụ Võ Cảnh"


def test_unlocked_term_can_be_improved_and_preserves_old_name_in_aliases():
    bible = BookBible(
        novel_id="van-thu-test",
        bible_revision=1,
        terms=[
            TermEntry(
                original_name="Huyết Lang",
                vi_name="Huyết Lang",
                category="creature",
                locked=False,
            )
        ],
    )

    delta = BookBibleDelta(
        new_terms=[
            TermEntry(
                original_name="Huyết Lang",
                vi_name="Sói Máu",
                category="creature",
                evidence="Chương 125 dòng 10",
                confidence=0.9,
            )
        ]
    )

    merged = LegacyBookBibleService.merge_delta(bible, delta, chapter_index=125)

    # Unlocked term: canonical name is updated to improved translation
    assert merged.terms[0].vi_name == "Sói Máu"
    # Old name is preserved in aliases
    assert "Huyết Lang" in merged.terms[0].aliases
    # Not forbidden because term was not locked
    assert merged.terms[0].forbidden_variants == []


def test_post_edit_mode_rejects_invented_cjk_without_evidence():
    bible = BookBible(
        novel_id="van-thu-test",
        source_profile=SourceProfile(language="vi_machine", mode="post_edit"),
        terms=[
            TermEntry(
                original_name="phi hành thuật",
                vi_name="Phi Hành Thuật",
                category="skill",
            )
        ],
    )

    # LLM hallucinates an invented CJK original_name without CJK evidence
    delta = BookBibleDelta(
        new_terms=[
            TermEntry(
                original_name="飞行术",
                vi_name="Phi Hành Thuật",
                category="skill",
                evidence="Hắn bay vút lên trời",  # no CJK characters in evidence!
            )
        ]
    )

    merged = LegacyBookBibleService.merge_delta(bible, delta, chapter_index=121)
    # The original entry keeps its non-CJK original_name because there was no CJK evidence in post_edit
    assert merged.terms[0].original_name == "phi hành thuật"
