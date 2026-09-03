import json
import os
from pathlib import Path
import pytest

from app.schemas.book_bible import (
    AddressTerm,
    BookBible,
    CharacterEntry,
    StyleGuide,
    TermEntry,
)
from app.modules.translation.application.qa_service import QAService


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "translation_quality" / "van_thu_0120_0129.json"
)


def load_fixtures():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["cases"]


@pytest.fixture(scope="module")
def regression_cases():
    return {c["id"]: c for c in load_fixtures()}


def build_bible_from_case(case: dict) -> BookBible:
    characters = [
        CharacterEntry(
            original_name=c.get("original_name", ""),
            vi_name=c.get("vi_name", ""),
            role=c.get("role", ""),
            aliases=c.get("aliases", []),
        )
        for c in case.get("characters", [])
    ]
    # For forbidden variants in characters if supported
    for idx, c in enumerate(case.get("characters", [])):
        if "forbidden_variants" in c:
            setattr(characters[idx], "forbidden_variants", c["forbidden_variants"])

    terms = [
        TermEntry(
            original_name=t.get("original_name", ""),
            vi_name=t.get("vi_name", ""),
            category=t.get("category", ""),
            forbidden_variants=t.get("forbidden_variants", []),
            locked=t.get("locked", False),
        )
        for t in case.get("locked_terms", [])
    ]
    for idx, t in enumerate(case.get("locked_terms", [])):
        if "family" in t:
            setattr(terms[idx], "family", t["family"])
        if "rank_order" in t:
            setattr(terms[idx], "rank_order", t["rank_order"])

    style_guide = StyleGuide(**case.get("style_guide", {}))
    return BookBible(
        novel_id="van-thu-chien-than",
        characters=characters,
        terms=terms,
        style_guide=style_guide,
    )


def test_fixture_file_loads_and_has_all_expected_cases(regression_cases):
    assert len(regression_cases) == 9
    expected_ids = {
        "ERR_HALLUCINATED_WITCH",
        "ERR_CHARACTER_ENTITY_CORRUPTION",
        "ERR_REALM_FAMILY_DRIFT",
        "ERR_PRONOUN_MODERN_DRIFT",
        "ERR_FOREIGN_HYBRID_TOKEN",
        "ERR_NON_VIETNAMESE_UNICODE",
        "ERR_REPEATED_WORDS",
        "ERR_MISSING_CHAPTER_HEADER",
        "ERR_SOURCE_WATERMARK_LEAKAGE",
    }
    assert set(regression_cases.keys()) == expected_ids


def test_style_guide_forbidden_regex_is_enforced():
    bible = BookBible(style_guide=StyleGuide(forbidden_regex=[r"\bhắn\b"]))

    issues = QAService(None).fast_rule_check(
        original_text="Một người bước đi.",
        translated_text="hắn bước đi.",
        book_bible=bible,
    )

    assert any("mẫu bị cấm" in issue.issue for issue in issues)


@pytest.mark.parametrize(
    "case_id",
    [
        "ERR_HALLUCINATED_WITCH",
        "ERR_CHARACTER_ENTITY_CORRUPTION",
        "ERR_REALM_FAMILY_DRIFT",
        "ERR_PRONOUN_MODERN_DRIFT",
        "ERR_FOREIGN_HYBRID_TOKEN",
        "ERR_NON_VIETNAMESE_UNICODE",
        "ERR_REPEATED_WORDS",
        "ERR_MISSING_CHAPTER_HEADER",
        "ERR_SOURCE_WATERMARK_LEAKAGE",
    ],
)
def test_regression_case_detection(case_id, regression_cases):
    case = regression_cases[case_id]
    bible = build_bible_from_case(case)
    qa_service = QAService(None)

    # 1. Clean translation must have 0 issues (no false positives)
    clean_issues = qa_service.fast_rule_check(
        original_text=case["source_text"],
        translated_text=case["clean_translation"],
        book_bible=bible,
    )
    assert clean_issues == [], f"Clean translation had false positive issues: {clean_issues}"

    # 2. Bad translation must trigger at least one rule issue
    bad_issues = qa_service.fast_rule_check(
        original_text=case["source_text"],
        translated_text=case["bad_translation"],
        book_bible=bible,
    )
    assert len(bad_issues) > 0, f"Bad translation was not caught for case {case_id}!"
