import os
import uuid
import tempfile
import pytest
from app.schemas.library import NovelCreateRequest, NovelUpdateRequest, ChapterStatus
from app.services.library_service import LibraryService, slugify


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


def test_library_incremental_chapter_addition_and_deduplication(tmp_path):
    service = LibraryService()
    novel_id = f"incremental-test-{uuid.uuid4().hex[:6]}"
    
    # 1. Create novel with chapter 1
    req = NovelCreateRequest(title="Test Incremental Novel", novel_id=novel_id)
    service.create_novel(req)
    service.add_or_update_chapter(novel_id, 1, "Chương 1", "Nội dung chương 1")

    meta = service.get_novel(novel_id)
    assert meta.total_chapters == 1
    assert meta.chapters[0].chapter_index == 1

    # 2. Add chapter 2
    service.add_or_update_chapter(novel_id, 2, "Chương 2", "Nội dung chương 2")
    meta2 = service.get_novel(novel_id)
    assert meta2.total_chapters == 2
    assert [c.chapter_index for c in meta2.chapters] == [1, 2]

    # 3. Update chapter 1 without duplicating
    service.add_or_update_chapter(novel_id, 1, "Chương 1 (Sửa đổi)", "Nội dung chương 1 mới")
    meta3 = service.get_novel(novel_id)
    assert meta3.total_chapters == 2
    assert [c.chapter_index for c in meta3.chapters] == [1, 2]
    assert meta3.chapters[0].chapter_title == "Chương 1 (Sửa đổi)"

    # Clean up
    service.delete_novel(novel_id)

