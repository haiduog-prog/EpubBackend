import os
import uuid
import tempfile
from typing import List, Tuple
import pytest
from app.schemas.library import NovelCreateRequest, NovelUpdateRequest, ChapterStatus
from app.services.library_service import LibraryService, slugify, parse_chapter_index_from_title



def test_slugify_vietnamese_characters():
    assert slugify("Đấu La Đại Lục 3 - Long Vương Truyền Thuyết") == "dau-la-dai-luc-3-long-vuong-truyen-thuyet"
    assert slugify("Đấu Phá Thương Khung") == "dau-pha-thuong-khung"
    assert slugify("Đi Năng Giáo Sư") == "di-nang-giao-su"
    assert slugify("Cổ Chân Nhân (Bản Dịch)") == "co-chan-nhan-ban-dich"


def test_library_create_and_get_novel(tmp_path):
    service = LibraryService()
    unique_id = f"dau-pha-test-{uuid.uuid4().hex[:6]}"
    req = NovelCreateRequest(
        title="Đấu Phá Thương Khung",
        original_title="斗破苍穹",
        author="Thiên Tằm Thổ Đậu",
        genre=["Tiên Hiệp", "Huyền Huyễn"],
        description="Thế giới thuộc về Đấu Khí...",
        novel_id=unique_id,
    )

    created = service.create_novel(req)
    assert created.novel_id == unique_id
    assert created.title == "Đấu Phá Thương Khung"
    assert created.total_chapters == 0

    fetched = service.get_novel(unique_id)
    assert fetched is not None
    assert fetched.author == "Thiên Tằm Thổ Đậu"
    assert len(fetched.genre) == 2

    # Clean up
    service.delete_novel(unique_id)


def test_library_add_chapter_and_get_content(tmp_path):
    service = LibraryService()
    unique_id = f"pham-nhan-test-{uuid.uuid4().hex[:6]}"
    req = NovelCreateRequest(
        title="Phàm Nhân Tu Tiên",
        novel_id=unique_id,
    )
    service.create_novel(req)

    # Add Chapter 1
    ch1 = service.add_or_update_chapter(
        novel_id=unique_id,
        chapter_index=1,
        chapter_title="Chương 1: Sơn thôn thiếu niên",
        content="Hàn Lập sinh ra tại một thôn nghèo...",
    )
    assert ch1.chapter_index == 1
    assert ch1.chapter_title == "Chương 1: Sơn thôn thiếu niên"
    assert ch1.word_count > 0

    meta = service.get_novel(unique_id)
    assert meta.total_chapters == 1

    # Read content back
    content = service.get_chapter_content(unique_id, 1, version="original")
    assert content == "Hàn Lập sinh ra tại một thôn nghèo..."

    # Clean up
    service.delete_novel(unique_id)


def test_library_export_epub(tmp_path):
    service = LibraryService()
    unique_id = f"tru-tien-test-{uuid.uuid4().hex[:6]}"
    req = NovelCreateRequest(
        title="Tru Tiên",
        author="Tiêu Đỉnh",
        novel_id=unique_id,
    )
    service.create_novel(req)

    # Add chapter with translated text
    service.add_or_update_chapter(
        novel_id=unique_id,
        chapter_index=1,
        chapter_title="Chương 1: Thanh Vân Môn",
        content="Văn bản gốc...",
    )

    # Simulate translation completion
    meta = service.get_novel(unique_id)
    meta.chapters[0].status = ChapterStatus.COMPLETED
    service._save_metadata(meta)

    # Save translated content
    trans_key = service._chapter_key(unique_id, 1, is_translated=True)
    service._save_raw_file(trans_key, "Nội dung đã dịch sang tiếng Việt rất mượt mà.".encode("utf-8"))

    # Export EPUB
    epub_out = str(tmp_path / "tru_tien.epub")
    result_path = service.export_full_epub(unique_id, output_path=epub_out)

    assert os.path.exists(result_path)
    assert os.path.getsize(result_path) > 1000  # Valid epub file size

    # Clean up
    service.delete_novel(unique_id)


from ebooklib import epub
from app.services.library_service import LibraryService, slugify, parse_chapter_index_from_title


def _create_mock_epub(title: str, chapters: List[Tuple[str, str]]) -> bytes:
    book = epub.EpubBook()
    book.set_identifier(f"id-{uuid.uuid4().hex[:6]}")
    book.set_title(title)
    book.set_language("vi")

    c_items = []
    for i, (ch_title, content) in enumerate(chapters, 1):
        c = epub.EpubHtml(title=ch_title, file_name=f"ch_{i:04d}.xhtml", lang="vi")
        c.content = f"<h1>{ch_title}</h1><p>{content}</p>".encode("utf-8")
        book.add_item(c)
        c_items.append(c)

    book.toc = tuple(c_items)
    book.spine = ["nav"] + c_items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
        tmp_path = tmp.name
    try:
        epub.write_epub(tmp_path, book)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_parse_chapter_index_from_title():
    assert parse_chapter_index_from_title("Chương 101: Đại Chiến Tiêu Gia") == 101
    assert parse_chapter_index_from_title("Hồi 12: Gặp gỡ người lạ") == 12
    assert parse_chapter_index_from_title("Tiết 5: Khởi đầu") == 5
    assert parse_chapter_index_from_title("Chapter 45 - The Awakening") == 45
    assert parse_chapter_index_from_title("第88章 绝世神功") == 88
    assert parse_chapter_index_from_title("ch_0105.xhtml") == 105
    assert parse_chapter_index_from_title("Lời tựa đầu sách") is None


def test_import_raw_then_import_translated_merges_without_data_loss():
    service = LibraryService()
    novel_id = f"dual-ver-test-{uuid.uuid4().hex[:6]}"

    raw_epub = _create_mock_epub(
        "Đấu Phá Khung",
        [
            ("Chương 1", "萧炎看着手中的戒指，眼中闪过一丝无奈... (Nội dung raw chương 1 dài trên 30 ký tự)"),
            ("Chương 2", "药老的声音缓缓响起，带着一丝戏谑... (Nội dung raw chương 2 dài trên 30 ký tự)"),
        ],
    )

    # 1. Import raw version
    meta_raw = service.import_epub_novel(raw_epub, is_translated=False, novel_id=novel_id)
    assert meta_raw.total_chapters == 2
    assert meta_raw.chapters[0].r2_original_key != ""
    assert meta_raw.chapters[0].r2_translated_key == ""
    assert meta_raw.chapters[0].status == ChapterStatus.NOT_TRANSLATED

    orig_key_1 = meta_raw.chapters[0].r2_original_key

    # 2. Import translated version of same novel
    trans_epub = _create_mock_epub(
        "Đấu Phá Khung",
        [
            ("Chương 1", "Tiêu Viêm nhìn chiếc nhẫn trong tay, trong mắt lóe lên vẻ bất đắc dĩ..."),
            ("Chương 2", "Giọng nói của Dược Lão chậm rãi vang lên, mang theo một chút trêu chọc..."),
        ],
    )
    meta_trans = service.import_epub_novel(trans_epub, is_translated=True, novel_id=novel_id)

    # Verify both versions exist and merged properly without data loss
    assert meta_trans.total_chapters == 2
    assert meta_trans.chapters[0].r2_original_key == orig_key_1  # Preserved!
    assert meta_trans.chapters[0].r2_translated_key != ""        # Added!
    assert meta_trans.chapters[0].status == ChapterStatus.COMPLETED
    assert meta_trans.translated_chapters == 2

    # Clean up
    service.delete_novel(novel_id)


def test_import_partial_epub_with_start_chapter_index():
    service = LibraryService()
    novel_id = f"partial-test-{uuid.uuid4().hex[:6]}"

    # Part 1: Chapters 1..2
    part1_epub = _create_mock_epub(
        "Phàm Nhân Tu Tiên",
        [
            ("Phần 1 - Đoạn 1", "Hàn Lập tiến vào Thất Huyền Môn học nghệ... (nội dung dài trên 30 ký tự)"),
            ("Phần 1 - Đoạn 2", "Mặc Đại Phu kiểm tra tư chất linh căn... (nội dung dài trên 30 ký tự)"),
        ],
    )
    meta1 = service.import_epub_novel(part1_epub, is_translated=True, novel_id=novel_id, start_chapter_index=1)
    assert meta1.total_chapters == 2
    assert [c.chapter_index for c in meta1.chapters] == [1, 2]

    # Part 2: Chapters 3..4 (using start_chapter_index=3)
    part2_epub = _create_mock_epub(
        "Phàm Nhân Tu Tiên",
        [
            ("Phần 2 - Đoạn 1", "Hàn Lập phát hiện chiếc bình nhỏ màu xanh... (nội dung dài trên 30 ký tự)"),
            ("Phần 2 - Đoạn 2", "Linh dịch nhỏ ra từ bình xanh thúc đẩy linh thảo... (nội dung dài trên 30 ký tự)"),
        ],
    )
    meta2 = service.import_epub_novel(part2_epub, is_translated=True, novel_id=novel_id, start_chapter_index=3)
    assert meta2.total_chapters == 4
    assert [c.chapter_index for c in meta2.chapters] == [1, 2, 3, 4]

    # Clean up
    service.delete_novel(novel_id)


def test_import_chapter_title_regex_auto_detection():
    service = LibraryService()
    novel_id = f"regex-test-{uuid.uuid4().hex[:6]}"

    epub_data = _create_mock_epub(
        "Kiếm Lai",
        [
            ("Chương 101: Rời khỏi Ly Châu Động Thiên", "Trần Bình An mang theo hộp kiếm bước ra bờ sông... (nội dung dài trên 30 ký tự)"),
            ("Chương 102: Lần đầu bước vào giang hồ", "Gió thu thổi qua rừng trúc xào xạc... (nội dung dài trên 30 ký tự)"),
        ],
    )

    meta = service.import_epub_novel(epub_data, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 2
    assert [c.chapter_index for c in meta.chapters] == [101, 102]
    assert meta.chapters[0].chapter_title == "Chương 101: Rời khỏi Ly Châu Động Thiên"
    assert meta.chapters[1].chapter_title == "Chương 102: Lần đầu bước vào giang hồ"

    # Clean up
    service.delete_novel(novel_id)


