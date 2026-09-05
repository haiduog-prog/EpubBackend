from pathlib import Path


INDEX_HTML = Path(__file__).parents[1] / "app" / "static" / "index.html"


def test_chapter_status_filter_exposes_needs_review_and_wires_filtering():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="chapter-status-filter"' in html
    assert 'onchange="onChapterStatusFilterChange(this.value)"' in html
    assert '<option value="needs_review">Chờ duyệt</option>' in html
    assert 'id="chapter-needs-review-filter"' in html
    assert 'onclick="toggleChapterStatusFilter(\'needs_review\')"' in html
    assert "let chapterFilterStatus = '';" in html
    assert "function onChapterStatusFilterChange(val)" in html
    assert "function toggleChapterStatusFilter(status)" in html
    assert "list = list.filter(ch => ch.status === chapterFilterStatus);" in html
