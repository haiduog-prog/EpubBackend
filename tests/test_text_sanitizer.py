import pytest

from app.parsers.text_sanitizer import (
    clean_raw_text,
    extract_chapter_title_prefix,
    reattach_chapter_title,
    split_chapter_sections,
)


def test_clean_raw_text_removes_watermarks_and_separators():
    dirty_text = """Nguồn: Tàng Thư Viện
read.st/truyen/123
------oOo------
Đỗ Phong hít sâu một hơi, vận chuyển Cửu Chuyển công pháp.
Ký tự rác: \u0627\u0644\u0639\u0631\u0628\u064a\u0629 và \u0391\u03b8\u03ae\u03bd\u03b1 kết thúc.
===================
Ủng hộ dịch giả tại web...
"""
    cleaned = clean_raw_text(dirty_text)

    assert "Nguồn:" not in cleaned
    assert "read.st" not in cleaned
    assert "oOo" not in cleaned
    assert "===" not in cleaned
    assert "Ủng hộ dịch giả" not in cleaned
    assert "\u0627\u0644\u0639\u0631\u0628\u064a\u0629" not in cleaned  # Arabic removed
    assert "\u0391\u03b8\u03ae\u03bd\u03b1" not in cleaned  # Greek removed
    assert "Đỗ Phong hít sâu một hơi" in cleaned


def test_extract_chapter_title_prefix():
    text = """Chương 120: Huyết Chiến Tử Kỳ Lân

Gió lạnh rít gào trên đỉnh Đoạn Vân Lĩnh. Đỗ Phong đứng sừng sững."""

    title, body = extract_chapter_title_prefix(text)
    assert title == "Chương 120: Huyết Chiến Tử Kỳ Lân"
    assert "Gió lạnh rít gào" in body
    assert "Chương 120" not in body


def test_reattach_chapter_title():
    title = "Chương 154: Tử Vực Trùng Sinh"
    translated_body_missing_title = "Đỗ Phong mở mắt ra, xung quanh là một mảnh hoang vu."

    result = reattach_chapter_title(title, translated_body_missing_title, chapter_index=154)
    assert result.startswith("Chương 154: Tử Vực Trùng Sinh\n\nĐỗ Phong mở mắt ra")

    # If translation already has a title, it shouldn't duplicate
    translated_body_with_title = "Chương 154: Tử Vực Trùng Sinh\n\nĐỗ Phong mở mắt ra."
    result2 = reattach_chapter_title(title, translated_body_with_title, chapter_index=154)
    assert result2 == translated_body_with_title


def test_split_chapter_sections_preserves_all_headings():
    sections = split_chapter_sections(
        "Chương 1: Mở đầu\n\nNội dung một.\n\n"
        "Chương 2: Tiếp diễn\n\nNội dung hai."
    )

    assert sections == [
        ("Chương 1: Mở đầu", "Nội dung một."),
        ("Chương 2: Tiếp diễn", "Nội dung hai."),
    ]
