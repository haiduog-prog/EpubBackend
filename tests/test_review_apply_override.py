from pathlib import Path


INDEX_HTML = Path(__file__).parents[1] / "app" / "static" / "index.html"


def test_review_apply_has_explicit_qa_warning_confirmation_and_override_payload():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "allow_qa_warnings: allowQaWarnings" in html
    assert "window.confirm" in html
    assert "Vẫn duyệt và áp dụng" in html
