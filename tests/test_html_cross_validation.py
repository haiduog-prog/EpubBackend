import pytest
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem
from app.llm.anthropic_provider import AnthropicProvider


def test_html_cross_constraint_alignment():
    input_items = [
        HTMLInputItem(id="p1", text="Hello world"),
        HTMLInputItem(id="p2", text="Second paragraph"),
        HTMLInputItem(id="p3", text="Third paragraph")
    ]

    # LLM returned p1 and p3, but skipped p2
    raw_translations = [
        HTMLTranslationItem(id="p1", text_vi="Xin chào thế giới"),
        HTMLTranslationItem(id="p3", text_vi="Đoạn thứ ba")
    ]

    out_map = {item.id: item.text_vi for item in raw_translations}
    aligned = []
    for item in input_items:
        if item.id in out_map and out_map[item.id].strip():
            aligned.append(HTMLTranslationItem(id=item.id, text_vi=out_map[item.id]))
        else:
            aligned.append(HTMLTranslationItem(id=item.id, text_vi=item.text))

    assert len(aligned) == 3
    assert aligned[0].id == "p1" and aligned[0].text_vi == "Xin chào thế giới"
    assert aligned[1].id == "p2" and aligned[1].text_vi == "Second paragraph"  # Fallback to original
    assert aligned[2].id == "p3" and aligned[2].text_vi == "Đoạn thứ ba"
