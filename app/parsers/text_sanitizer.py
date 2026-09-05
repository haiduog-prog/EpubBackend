"""Proactive text sanitizer and chapter title preservation utilities."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Patterns for common converter and site watermarks
WATERMARK_PATTERNS = [
    # Source / converter / website watermarks
    re.compile(r"(?im)^\s*(?:nguồn|nguon)\s*[:：].*?$"),
    re.compile(r"(?im)^\s*(?:nhóm dịch|dịch giả|convert(?:er)?)\s*[:：].*?$"),
    re.compile(r"(?im)^\s*(?:truyencv|tangthuvien|truyenyy|wikidich|metruyenchu|read\.st|faloo|qidian)[\w\.\-/]*\s*$"),
    re.compile(r"(?im)^\s*https?://\S+\s*$"),
    # Separator dividers
    re.compile(r"(?im)^\s*[-_—=~*#\s]*[oO0]o[oO0][-_—=~*#\s]*$"),
    re.compile(r"(?im)^\s*[-_—=~*#]{3,}\s*$"),
    # Advertisement disclaimers
    re.compile(r"(?im)^\s*(?:ủng hộ dịch giả|bấm chia sẻ|chúc bạn đọc truyện vui vẻ|theo dõi fanpage).*?$"),
]

# Foreign non-standard script sequences (e.g. Arabic, Greek characters leaked from text noise)
FOREIGN_SCRIPT_REGEX = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u0370-\u03FF\u1F00-\u1FFF]+")

# Title match regex
TITLE_PATTERNS = [
    re.compile(r"^(Chương\s+\d+[:.\s\-_–—]*[^\n]*)", re.IGNORECASE),
    re.compile(r"^(Hồi\s+\d+[:.\s\-_–—]*[^\n]*)", re.IGNORECASE),
    re.compile(r"^(Thứ\s+\d+\s+Chương[:.\s\-_–—]*[^\n]*)", re.IGNORECASE),
    re.compile(r"^(第\s*[\d一二三四五六七八九十百千万0-9]+\s*章[^\n]*)"),
]


def clean_raw_text(text: str) -> str:
    """Lọc sạch watermark quảng cáo, URL rác, dividers và ký tự ngoại lai."""
    if not text:
        return ""

    cleaned = text
    for pattern in WATERMARK_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    # Clean foreign non-CJK/non-Latin scripts (Arabic, Greek noise)
    cleaned = FOREIGN_SCRIPT_REGEX.sub("", cleaned)

    # Normalize carriage returns and excessive empty lines
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_chapter_title_prefix(text: str) -> Tuple[Optional[str], str]:
    """Tách dòng tiêu đề chương ra khỏi phần thân bài trước khi đưa vào LLM.

    Trả về:
        (title_line, body_text)
    """
    if not text:
        return None, ""

    lines = text.splitlines()
    # Tìm dòng tiêu đề trong 5 dòng đầu tiên
    title_line = None
    title_line_idx = -1

    for idx, line in enumerate(lines[:5]):
        stripped = line.strip()
        if not stripped:
            continue
        for pat in TITLE_PATTERNS:
            match = pat.match(stripped)
            if match:
                title_line = match.group(1).strip()
                title_line_idx = idx
                break
        if title_line:
            break

    if title_line is not None and title_line_idx >= 0:
        remaining_lines = lines[:title_line_idx] + lines[title_line_idx + 1 :]
        body_text = "\n".join(remaining_lines).strip()
        return title_line, body_text

    return None, text.strip()


def split_chapter_sections(text: str) -> List[Tuple[Optional[str], str]]:
    """Split a TXT novel into titled sections without sending headings to the LLM.

    A file may contain one chapter or a complete range of chapters.  The old
    pipeline only detached the first heading, which allowed later headings to
    be translated as prose or disappear during correction.  Sections without a
    recognized heading are returned as a single untitled section.
    """
    if not text or not text.strip():
        return []

    lines = text.splitlines()
    starts: List[Tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in TITLE_PATTERNS:
            match = pattern.match(stripped)
            if match:
                starts.append((index, match.group(1).strip()))
                break

    if not starts:
        return [(None, text.strip())]

    sections: List[Tuple[Optional[str], str]] = []
    first_start = starts[0][0]
    preamble = "\n".join(lines[:first_start]).strip()
    if preamble:
        sections.append((None, preamble))

    for position, (start, title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        sections.append((title, body))
    return sections


def prepare_chapter_translation(text: str) -> Tuple[Optional[str], str]:
    """Preserve Vietnamese headings; send untranslated CJK headings through AI and QA."""
    title, body = extract_chapter_title_prefix(text)
    if title and re.search(r"[\u3400-\u9fff\uf900-\ufaff]", title):
        return None, text
    return title, body or text


def reattach_chapter_title(
    title_prefix: Optional[str],
    translated_body: str,
    chapter_index: Optional[int] = None,
) -> str:
    """Gắn lại dòng tiêu đề chương chuẩn xác vào đầu bản dịch nếu chưa có."""
    if not translated_body:
        return title_prefix or ""

    first_line = ""
    for line in translated_body.splitlines():
        if line.strip():
            first_line = line.strip()
            break

    # Kiểm tra xem bản dịch đã có tiêu đề chương chưa
    has_title = False
    for pat in TITLE_PATTERNS[:3]:  # chỉ check tiếng Việt
        if pat.match(first_line):
            has_title = True
            break

    if has_title:
        return translated_body.strip()

    # Nếu chưa có tiêu đề và ta có title_prefix đã lưu
    if title_prefix:
        # Chuẩn hóa tiêu đề: nếu là chữ Hán hoặc convert thô, chuẩn hóa số chương
        if chapter_index is not None and not re.match(r"^Chương\s+\d+", title_prefix, re.IGNORECASE):
            # Ví dụ '第120章: ...' -> 'Chương 120: ...'
            clean_sub = re.sub(r"^第\s*\d+\s*章[:.\s]*", "", title_prefix).strip()
            formatted_title = f"Chương {chapter_index}: {clean_sub}" if clean_sub else f"Chương {chapter_index}"
        else:
            formatted_title = title_prefix
        return f"{formatted_title}\n\n{translated_body.strip()}"

    # Nếu không có title_prefix nhưng có chapter_index
    if chapter_index is not None:
        return f"Chương {chapter_index}\n\n{translated_body.strip()}"

    return translated_body.strip()
