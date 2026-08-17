import pytest
from app.parsers.html_merger import HTMLMerger
from app.schemas.translation import HTMLTranslationItem


def test_html_merger_extract():
    html_input = """
    <html>
        <body>
            <h1>Chương 1: Khởi đầu</h1>
            <p>萧炎 nhìn <em>Dược Lão</em> và nói: <b>"Sư phụ, chúng ta đi thôi!"</b></p>
            <p>Gió thổi qua ngọn núi.</p>
        </body>
    </html>
    """

    items, soup = HTMLMerger.extract_semantic_nodes(html_input)
    assert len(items) == 3
    assert items[0].text == "Chương 1: Khởi đầu"
    # Paragraph with <em> and <b> should be merged into a single sentence item!
    assert "萧炎 nhìn Dược Lão và nói: \"Sư phụ, chúng ta đi thôi!\"" in items[1].text
    assert items[2].text == "Gió thổi qua ngọn núi."


def test_html_merger_reconstruct():
    html_input = "<p>Text <em>gốc</em></p>"
    items, soup = HTMLMerger.extract_semantic_nodes(html_input)
    
    translations = [HTMLTranslationItem(id=items[0].id, text_vi="Văn bản đã dịch thuần Việt")]
    reconstructed = HTMLMerger.reconstruct_html(soup, translations)

    assert "Văn bản đã dịch thuần Việt" in reconstructed
    assert "data-node-id" not in reconstructed
