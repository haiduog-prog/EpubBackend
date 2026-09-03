import os
import uuid
import tempfile
from typing import List, Tuple
import pytest
from app.schemas.book_bible import (
    BookBible,
    CharacterEntry,
    PendingBibleChange,
    PlaceEntry,
    TermEntry,
)
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


def test_library_export_epub_includes_untranslated_original(tmp_path):
    from ebooklib import epub, ITEM_DOCUMENT

    service = LibraryService()
    unique_id = f"full-export-test-{uuid.uuid4().hex[:6]}"
    service.create_novel(NovelCreateRequest(title="Full Export", novel_id=unique_id))
    service.add_or_update_chapter(unique_id, 1, "Chương 1", "Nội dung gốc chương một.")
    service.add_or_update_chapter(unique_id, 2, "Chương 2", "Nội dung gốc chương hai chưa dịch.")

    meta = service.get_novel(unique_id)
    meta.chapters[0].status = ChapterStatus.COMPLETED
    service._save_metadata(meta)
    trans_key = service._chapter_key(unique_id, 1, is_translated=True)
    service._save_raw_file(trans_key, "Nội dung đã dịch chương một.".encode("utf-8"))

    output_path = service.export_full_epub(unique_id, output_path=str(tmp_path / "full.epub"))
    book = epub.read_epub(output_path)
    document_text = "\n".join(
        item.get_content().decode("utf-8", errors="ignore")
        for item in book.get_items_of_type(ITEM_DOCUMENT)
    )

    assert len(list(book.get_items_of_type(ITEM_DOCUMENT))) >= 2
    assert "Nội dung đã dịch chương một." in document_text
    assert "Nội dung gốc chương hai chưa dịch." in document_text
    service.delete_novel(unique_id)

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


def test_storage_repo_file_exists_in_r2():
    from app.core.storage import storage_repo
    # When R2 is not active or key doesn't exist, returns False safely
    assert storage_repo.file_exists_in_r2("non-existent-key.epub") is False


def test_export_novel_epub_endpoint_fallback_and_redirect(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import settings
    from app.core.storage import storage_repo
    from app.services.library_service import library_service

    client = TestClient(app)
    novel_id = f"export-endpoint-test-{uuid.uuid4().hex[:6]}"

    epub_data = _create_mock_epub(
        "Đấu La Đại Lục",
        [
            ("Chương 1", "Đường Tam thức tỉnh Võ Hồn Lam Ngân Thảo..."),
            ("Chương 2", "Hạo Thiên Chùy xuất hiện chấn động..."),
        ],
    )
    meta = library_service.import_epub_novel(epub_data, is_translated=True, novel_id=novel_id)

    # Scenario 1: File is NOT in storage (file_exists and file_exists_on_r2 return False)
    monkeypatch.setattr(storage_repo, "file_exists", lambda key: False)
    monkeypatch.setattr(storage_repo, "file_exists_on_r2", lambda key: False)
    monkeypatch.setattr(settings, "cloudflare_r2_public_url", "https://pub-test.r2.dev")

    # Should NOT 307 redirect to missing CDN file, but generate and return EPUB file (200 OK)
    resp = client.get(f"/api/v1/library/novels/{novel_id}/export/epub", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/epub+zip"
    assert len(resp.content) > 500

    # Scenario 2A: Supabase active and file cached
    monkeypatch.setattr(type(storage_repo), "active_provider_name", property(lambda self: "supabase"))
    monkeypatch.setattr(storage_repo, "file_exists", lambda key: True)
    monkeypatch.setattr(storage_repo, "get_public_url", lambda key: f"https://test.supabase.co/storage/v1/object/public/novels/{key}")
    resp_supabase = client.get(f"/api/v1/library/novels/{novel_id}/export/epub", follow_redirects=False)
    assert resp_supabase.status_code == 307
    assert resp_supabase.headers["location"] == f"https://test.supabase.co/storage/v1/object/public/novels/novels/{novel_id}/full.epub"

    # Scenario 2B: File is cached specifically on R2 (file_exists_on_r2 returns True)
    monkeypatch.setattr(type(storage_repo), "active_provider_name", property(lambda self: "r2"))
    monkeypatch.setattr(storage_repo, "file_exists_on_r2", lambda key: True)
    resp_redirect = client.get(f"/api/v1/library/novels/{novel_id}/export/epub", follow_redirects=False)
    assert resp_redirect.status_code == 307
    assert resp_redirect.headers["location"] == f"https://pub-test.r2.dev/novels/{novel_id}/full.epub"

    # Scenario 3: Force rebuild bypasses CDN redirect
    monkeypatch.setattr(storage_repo, "upload_file_stream", lambda path, key, content_type=None: f"https://pub-test.r2.dev/{key}")
    monkeypatch.setattr(storage_repo, "file_exists_on_r2", lambda key: True)
    resp_force = client.get(f"/api/v1/library/novels/{novel_id}/export/epub?force_rebuild=true", follow_redirects=False)
    assert resp_force.status_code == 200
    assert resp_force.headers["content-type"] == "application/epub+zip"


    # Clean up
    library_service.delete_novel(novel_id)


def test_epub_with_separated_title_and_content_files():
    """Kiểm tra EPUB có các file tiêu đề ngắn tách rời các file nội dung (như lỗi của Đấu La Đại Lục 3)"""
    service = LibraryService()
    novel_id = f"sep-test-{uuid.uuid4().hex[:6]}"

    book = epub.EpubBook()
    book.set_identifier(f"id-{uuid.uuid4().hex[:6]}")
    book.set_title("Đấu La Đại Lục 3 - Long Vương Truyền Thuyết")
    book.set_language("vi")

    # Item 1: Title page for Ch 1 (35 bytes)
    t1 = epub.EpubHtml(title="Chương 1", file_name="ch01_title.xhtml", lang="vi")
    t1.content = b"<h1>Ch\xc6\xb0\xc6\xa1ng 1: Th\xe1\xbb\xa9c T\xe1\xbb\x89nh V\xc3\xb5 H\xe1\xbb\x93n</h1>"
    book.add_item(t1)

    # Item 2: Content page for Ch 1 (story paragraphs, no h1)
    c1 = epub.EpubHtml(title="", file_name="ch01_content.xhtml", lang="vi")
    c1.content = b"<p>\xc4\x90\xc6\xb0\xe1\xbb\x9dng V\xc5\xa9 L\xc3\xa2n \xc4\x91\xe1\xbb\xa9ng tr\xc6\xb0\xe1\xbb\x9bc g\xc6\xb0\xc6\xa1ng nh\xc3\xacn th\xe1\xba\xa5y v\xc3\xb5 h\xe1\xbb\x93n Lam Ng\xc3\xa2n Th\xe1\xba\xa3o xu\xe1\xba\xa5t hi\xe1\xbb\x87n r\xe1\xba\xa5t r\xc3\xb5 r\xc3\xa0ng...</p>"
    book.add_item(c1)

    # Item 3: Title page for Ch 2 (35 bytes)
    t2 = epub.EpubHtml(title="Chương 2", file_name="ch02_title.xhtml", lang="vi")
    t2.content = b"<h1>Ch\xc6\xb0\xc6\xa1ng 2: Kim Long V\xc6\xb0\xc6\xa1ng Huy\xe1\xba\xbft M\xe1\xba\xa1ch</h1>"
    book.add_item(t2)

    # Item 4: Content page for Ch 2
    c2 = epub.EpubHtml(title="", file_name="ch02_content.xhtml", lang="vi")
    c2.content = b"<p>N\xc4\x83ng l\xc6\xb0\xe1\xbb\xa3ng huy\xe1\xba\xbft m\xe1\xba\xa1ch th\xe1\xba\xa7n b\xc3\xad b\xe1\xba\xaft \xc4\x91\xe1\xba\xa7u th\xe1\xbb\xa9c t\xe1\xbb\x89nh trong c\xc6\xa1 th\xe1\xbb\x83 c\xe1\xba\xaduli\xe1\xbb\x87t...</p>"
    book.add_item(c2)

    book.spine = ["nav", t1, c1, t2, c2]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
        tmp_path = tmp.name
    try:
        epub.write_epub(tmp_path, book)
        with open(tmp_path, "rb") as f:
            epub_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 2
    assert [c.chapter_index for c in meta.chapters] == [1, 2]
    assert "Thức Tỉnh Võ Hồn" in meta.chapters[0].chapter_title
    assert "Kim Long Vương" in meta.chapters[1].chapter_title
    assert meta.chapters[0].word_count > 5
    assert meta.chapters[1].word_count > 5

    service.delete_novel(novel_id)


def test_epub_multi_chapters_in_single_file():
    """Kiểm tra EPUB có nhiều chương gộp trong cùng 1 file HTML"""
    service = LibraryService()
    novel_id = f"multi-test-{uuid.uuid4().hex[:6]}"

    book = epub.EpubBook()
    book.set_identifier(f"id-{uuid.uuid4().hex[:6]}")
    book.set_title("Bộ Truyện Đa Chương")
    book.set_language("vi")

    c = epub.EpubHtml(title="Toàn Bộ", file_name="all_chapters.xhtml", lang="vi")
    c.content = (
        b"<div>"
        b"<h1>Ch\xc6\xb0\xc6\xa1ng 1: Kh\xe1\xbb\x9fi \xc4\x90\xe1\xba\xa7u</h1>"
        b"<p>\xc4\x90o\xe1\xba\xa1n v\xc4\x83n ch\xc6\xb0\xc6\xa1ng 1 r\xe1\xba\xa5t d\xc3\xa0i v\xc3\xa0 \xc4\x91\xe1\xba\xa7y \xc4\x91\xe1\xbb\xa7...</p>"
        b"<h1>Ch\xc6\xb0\xc6\xa1ng 2: Ti\xe1\xba\xbfn B\xc6\xb0\xe1\xbb\x9bc</h1>"
        b"<p>\xc4\x90o\xe1\xba\xa1n v\xc4\x83n ch\xc6\xb0\xc6\xa1ng 2 c\xc5\xa9ng r\xe1\xba\xa5t d\xc3\xa0i v\xc3\xa0 chi ti\xe1\xba\xbft...</p>"
        b"<h1>Ch\xc6\xb0\xc6\xa1ng 3: Cao Tr\xc3\xa0o</h1>"
        b"<p>\xc4\x90o\xe1\xba\xa1n v\xc4\x83n ch\xc6\xb0\xc6\xa1ng 3 b\xc3\xb9ng n\xe1\xbb\x95 tr\xe1\xba\xadn chi\xe1\xba\xbfn...</p>"
        b"</div>"
    )
    book.add_item(c)
    book.spine = ["nav", c]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
        tmp_path = tmp.name
    try:
        epub.write_epub(tmp_path, book)
        with open(tmp_path, "rb") as f:
            epub_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 3
    assert [c.chapter_index for c in meta.chapters] == [1, 2, 3]
    assert "Khởi Đầu" in meta.chapters[0].chapter_title
    assert "Tiến Bước" in meta.chapters[1].chapter_title
    assert "Cao Trào" in meta.chapters[2].chapter_title

    service.delete_novel(novel_id)


def test_epub_with_nav_toc_cover_filtering():
    """Kiểm tra EPUB có chứa cover.xhtml và toc.xhtml rác được lọc bỏ chính xác"""
    service = LibraryService()
    novel_id = f"filter-test-{uuid.uuid4().hex[:6]}"

    book = epub.EpubBook()
    book.set_identifier(f"id-{uuid.uuid4().hex[:6]}")
    book.set_title("Truyện Có Bìa Và Mục Lục")
    book.set_language("vi")

    # Cover page (boilerplate)
    cov = epub.EpubHtml(title="Cover", file_name="cover.xhtml", lang="vi")
    cov.content = b"<div><img src='cover.jpg'/><p>B\xc3\xaca s\xc3\xa1ch</p></div>"
    book.add_item(cov)

    # TOC page (boilerplate list)
    toc_doc = epub.EpubHtml(title="TOC", file_name="toc.xhtml", lang="vi")
    toc_doc.content = b"<div><h2>M\xe1\xbb\xa5c L\xe1\xbb\xa5c</h2><ul><li>Ch\xc6\xb0\xc6\xa1ng 1</li><li>Ch\xc6\xb0\xc6\xa1ng 2</li></ul></div>"
    book.add_item(toc_doc)

    # Actual chapter 1
    c1 = epub.EpubHtml(title="Chương 1", file_name="ch_001.xhtml", lang="vi")
    c1.content = b"<h1>Ch\xc6\xb0\xc6\xa1ng 1: Th\xe1\xba\xbf Gi\xe1\xbb\x9bi M\xe1\xbb\x9bi</h1><p>N\xe1\xbb\x99i dung ch\xc6\xb0\xc6\xa1ng th\xe1\xbb\xb1c s\xe1\xbb\xb1 b\xe1\xba\xaft \xc4\x91\xe1\xba\xa7u \xe1\xbb\x9f \xc4\x91\xc3\xa2y...</p>"
    book.add_item(c1)

    book.spine = ["nav", cov, toc_doc, c1]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
        tmp_path = tmp.name
    try:
        epub.write_epub(tmp_path, book)
        with open(tmp_path, "rb") as f:
            epub_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 1
    assert meta.chapters[0].chapter_index == 1
    service.delete_novel(novel_id)


def test_epub_with_div_and_br_formatting_without_p_tags():
    """Kiểm tra EPUB dùng cấu trúc <div> và <br/> thay vì <p> (như Vạn Thú Chiến Thần)"""
    service = LibraryService()
    novel_id = f"div-test-{uuid.uuid4().hex[:6]}"

    book = epub.EpubBook()
    book.set_identifier(f"id-{uuid.uuid4().hex[:6]}")
    book.set_title("Vạn Thú Chiến Thần")
    book.set_language("vi")

    c1 = epub.EpubHtml(title="Chương 1", file_name="ch_1.xhtml", lang="vi")
    c1.content = (
        b"<div>"
        b"<h1>Ch\xc6\xb0\xc6\xa1ng 1: Tr\xe1\xbb\x8dng Ho\xe1\xba\xa1ch T\xc3\xa2n Sinh</h1>"
        b"<div>Ngu\xe1\xbb\x93n: read.st<br/>Ch\xc6\xb0\xc6\xa1ng 1: Tr\xe1\xbb\x8dng Ho\xe1\xba\xa1ch T\xc3\xa2n Sinh<br/>"
        b"\xe2\x80\x9cPhu qu\xc3\xa2n, ng\xc6\xb0\xe1\xbb\x9di ta s\xe1\xbb\xa3!\xe2\x80\x9d Th\xc6\xb0 sinh \xc4\x90\xe1\xbb\x97 Phong t\xe1\xbb\x89nh d\xe1\xba\xady... (n\xe1\xbb\x99i dung d\xc3\xa0i)</div>"
        b"<div class='Centered'>------oOo------</div>"
        b"</div>"
    )
    book.add_item(c1)

    book.spine = ["nav", c1]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp:
        tmp_path = tmp.name
    try:
        epub.write_epub(tmp_path, book)
        with open(tmp_path, "rb") as f:
            epub_bytes = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 1
    assert meta.chapters[0].chapter_index == 1
    assert "Trọng Hoạch Tân Sinh" in meta.chapters[0].chapter_title
    assert meta.chapters[0].word_count > 10

    service.delete_novel(novel_id)


def test_epub_with_missing_cover_image_in_archive():
    """Kiểm tra file EPUB có manifest khai báo cover.png nhưng không tồn tại trong ZIP archive (lỗi OEBPS/Images/cover.png)"""
    import io
    import zipfile
    service = LibraryService()
    novel_id = f"missing-cover-test-{uuid.uuid4().hex[:6]}"

    container_xml = """<?xml version='1.0' encoding='utf-8'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
  <rootfiles>
    <rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/>
  </rootfiles>
</container>"""

    content_opf = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf' unique-identifier='bookid' version='2.0'>
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:title>Truyện Thiếu Ảnh Bìa</dc:title>
    <dc:creator>Tác Giả Ẩn Danh</dc:creator>
  </metadata>
  <manifest>
    <item id='cover-image' href='Images/cover.png' media-type='image/png' properties='cover-image'/>
    <item id='missing-css' href='Styles/style.css' media-type='text/css'/>
    <item id='ch1' href='chapter1.xhtml' media-type='application/xhtml+xml'/>
  </manifest>
  <spine>
    <itemref idref='ch1'/>
  </spine>
</package>"""

    ch1_html = "<html><body><h1>Chương 1: Khởi Đầu Mới</h1><p>Nội dung chương 1 dài trên 30 ký tự để được nhận diện chính xác...</p></body></html>"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/chapter1.xhtml", ch1_html)

    epub_bytes = buf.getvalue()

    # Nhập truyện không bị crash bởi lỗi KeyError: There is no item named 'OEBPS/Images/cover.png' in the archive
    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 1
    assert meta.title == "Truyện Thiếu Ảnh Bìa"
    assert meta.author == "Tác Giả Ẩn Danh"
    assert meta.chapters[0].chapter_index == 1
    assert "Khởi Đầu Mới" in meta.chapters[0].chapter_title

    service.delete_novel(novel_id)


def test_epub_with_case_mismatched_cover_in_archive():
    """Kiểm tra file EPUB có manifest khai báo Images/cover.png nhưng trong ZIP thực tế là OEBPS/images/Cover.PNG"""
    import io
    import zipfile
    service = LibraryService()
    novel_id = f"case-mismatch-cover-test-{uuid.uuid4().hex[:6]}"

    container_xml = """<?xml version='1.0' encoding='utf-8'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
  <rootfiles>
    <rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/>
  </rootfiles>
</container>"""

    content_opf = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf' unique-identifier='bookid' version='2.0'>
  <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
    <dc:title>Truyện Bìa Khác Case</dc:title>
    <dc:creator>Tác Giả</dc:creator>
  </metadata>
  <manifest>
    <item id='cover-image' href='Images/cover.png' media-type='image/png' properties='cover-image'/>
    <item id='ch1' href='chapter1.xhtml' media-type='application/xhtml+xml'/>
  </manifest>
  <spine>
    <itemref idref='ch1'/>
  </spine>
</package>"""

    ch1_html = "<html><body><h1>Chương 1: Thử Nghiệm</h1><p>Nội dung chương 1 dài trên 30 ký tự để được nhận diện...</p></body></html>"
    fake_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", content_opf)
        zf.writestr("OEBPS/chapter1.xhtml", ch1_html)
        zf.writestr("OEBPS/images/Cover.PNG", fake_png_bytes)

    epub_bytes = buf.getvalue()

    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 1
    assert meta.cover_url is not None

    service.delete_novel(novel_id)


def test_bulk_delete_novels_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.library_service import library_service

    client = TestClient(app)
    id1 = f"bulk-del-test-1-{uuid.uuid4().hex[:6]}"
    id2 = f"bulk-del-test-2-{uuid.uuid4().hex[:6]}"

    epub1 = _create_mock_epub("Truyện Test 1", [("Chương 1", "Nội dung chương 1 dài trên 30 ký tự...")])
    epub2 = _create_mock_epub("Truyện Test 2", [("Chương 1", "Nội dung chương 1 dài trên 30 ký tự...")])

    library_service.import_epub_novel(epub1, is_translated=True, novel_id=id1)
    library_service.import_epub_novel(epub2, is_translated=True, novel_id=id2)

    assert library_service.get_novel(id1) is not None
    assert library_service.get_novel(id2) is not None

    resp = client.post("/api/v1/library/novels/bulk-delete", json={"novel_ids": [id1, id2, "non-existent-id"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted_count"] == 2
    assert "non-existent-id" in data["failed_ids"]

    assert library_service.get_novel(id1) is None
    assert library_service.get_novel(id2) is None


@pytest.mark.anyio
async def test_translate_chapter_fallback_when_original_is_empty(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from app.schemas.book_bible import BookBibleDelta

    service = LibraryService()
    novel_id = f"translate-fallback-{uuid.uuid4().hex[:6]}"

    epub_bytes = _create_mock_epub(
        "Truyện Dịch Lại Fallback",
        [("Chương 1: Khởi đầu", "Đây là nội dung chương 1 đã dịch trước đó dài trên ba mươi ký tự...")],
    )
    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 1

    # Mock LLM client
    mock_llm = MagicMock()
    mock_llm.extract_book_bible_delta = AsyncMock(return_value=BookBibleDelta())
    mock_llm.translate_prose_chunk = AsyncMock(return_value="Bản dịch mới sau khi dịch lại chương 1.")

    monkeypatch.setattr("app.modules.library.legacy_service.create_llm_client", lambda **kwargs: mock_llm)

    # Translate chapter 1
    chapter = await service.translate_chapter(novel_id, 1)

    assert chapter.status == ChapterStatus.COMPLETED
    assert chapter.r2_original_key != ""
    assert "Bản dịch mới" in chapter.translated_text_preview

    # Verify translated has new text
    assert "Bản dịch mới sau khi dịch lại chương 1." in service.get_chapter_content(novel_id, 1, version="translated")

    service.delete_novel(novel_id)


def test_chapter_extraction_stats_are_scoped_to_chapter(monkeypatch):
    service = LibraryService()
    novel_id = f"chapter-stats-{uuid.uuid4().hex[:8]}"
    service.create_novel(NovelCreateRequest(title="Chapter Stats", novel_id=novel_id))

    bible = BookBible(
        novel_id=novel_id,
        characters=[
            CharacterEntry(
                character_id="char-1",
                original_name="萧炎",
                vi_name="Tiêu Viêm",
                role="Nhân vật chính",
                first_seen_chapter=3,
            ),
            CharacterEntry(
                character_id="char-2",
                original_name="药老",
                vi_name="Dược Lão",
                first_seen_chapter=4,
            ),
        ],
        places=[
            PlaceEntry(original_name="乌坦城", vi_name="Ô Thản Thành", first_seen_chapter=3),
        ],
        terms=[
            TermEntry(
                original_name="斗气",
                vi_name="Đấu khí",
                category="power",
                first_seen_chapter=3,
            ),
        ],
        pending_changes=[
            PendingBibleChange(
                change_id="pending-3",
                change_type="canonical_correction",
                target_id="char-1",
                chapter_index=3,
                status="pending",
            ),
        ],
    )
    monkeypatch.setattr(
        "app.modules.library.legacy_service.storage_repo.get_bible",
        lambda requested_novel_id: bible if requested_novel_id == novel_id else None,
    )

    try:
        stats = service.get_chapter_extraction_stats(novel_id, 3)

        assert stats.character_count == 1
        assert stats.place_count == 1
        assert stats.term_count == 1
        assert stats.pending_change_count == 1
        assert [item.vi_name for item in stats.characters] == ["Tiêu Viêm"]
        assert [item.vi_name for item in stats.places] == ["Ô Thản Thành"]
        assert stats.terms[0].category == "power"
    finally:
        service.delete_novel(novel_id)


def test_get_chapter_content_self_healing_from_full_epub():
    from app.infrastructure.storage.facade import storage_repo

    service = LibraryService()
    novel_id = f"self-heal-{uuid.uuid4().hex[:6]}"

    epub_bytes = _create_mock_epub(
        "Truyện Self Healing",
        [("Chương 1: Hồi Phục", "Nội dung chương 1 này sẽ được phục hồi tự động từ full.epub...")],
    )
    meta = service.import_epub_novel(epub_bytes, is_translated=True, novel_id=novel_id)
    assert meta.total_chapters == 1

    # Simulate missing individual txt file on storage, while full.epub remains
    txt_key = f"novels/{novel_id}/translated/ch_0001.txt"
    storage_repo.delete_file(txt_key)
    assert storage_repo.get_bytes(txt_key) is None

    # Requesting chapter content should trigger on-demand extraction from full.epub
    content = service.get_chapter_content(novel_id, 1, version="translated")
    assert content is not None
    assert "Nội dung chương 1 này sẽ được phục hồi" in content

    # Verify that the txt file was re-cached into storage
    assert storage_repo.get_bytes(txt_key) is not None

    service.delete_novel(novel_id)


@pytest.mark.asyncio
async def test_translate_chapter_preview_mode(monkeypatch):
    class MockLLM:
        async def extract_book_bible_delta(self, *args, **kwargs):
            return {}

        async def translate_prose_chunk(self, *args, **kwargs):
            return "Bản dịch thử nghiệm xem trước không ghi đè."

    monkeypatch.setattr("app.modules.library.legacy_service.create_llm_client", lambda **kwargs: MockLLM())

    service = LibraryService()
    novel_id = f"test-preview-{uuid.uuid4().hex[:6]}"
    service.create_novel(NovelCreateRequest(title="Truyện Test Preview", novel_id=novel_id))

    service.add_or_update_chapter(
        novel_id=novel_id,
        chapter_index=1,
        chapter_title="Chương 1: Khởi đầu",
        content="Văn bản gốc tiếng Trung...",
    )
    # Save an initial translation
    trans_key = service._chapter_key(novel_id, 1, is_translated=True)
    service._save_raw_file(trans_key, "Bản dịch cũ ban đầu.".encode("utf-8"))
    meta = service.get_novel(novel_id)
    meta.chapters[0].status = ChapterStatus.COMPLETED
    meta.chapters[0].translated_text_preview = "Bản dịch cũ ban đầu."
    service._save_metadata(meta)

    # Run translation in preview_only mode
    preview_res = await service.translate_chapter(novel_id, 1, preview_only=True)

    # Assert preview response fields
    assert preview_res.novel_id == novel_id
    assert preview_res.chapter_index == 1
    assert preview_res.previous_translated_text == "Bản dịch cũ ban đầu."
    assert preview_res.new_translated_text == "Bản dịch thử nghiệm xem trước không ghi đè."
    assert preview_res.word_count > 0

    # Ensure storage and metadata were NOT overwritten
    assert service.get_chapter_content(novel_id, 1, version="translated") == "Bản dịch cũ ban đầu."
    meta_after = service.get_novel(novel_id)
    assert meta_after.chapters[0].translated_text_preview == "Bản dịch cũ ban đầu."

    # Now apply the new translation
    updated_ch = service.apply_chapter_translation(
        novel_id=novel_id,
        chapter_index=1,
        content="Bản dịch mới đã được người dùng chấp thuận.",
    )
    assert updated_ch.status == ChapterStatus.COMPLETED
    assert "Bản dịch mới đã được người dùng chấp thuận." in updated_ch.translated_text_preview
    assert service.get_chapter_content(novel_id, 1, version="translated") == "Bản dịch mới đã được người dùng chấp thuận."

    service.delete_novel(novel_id)









