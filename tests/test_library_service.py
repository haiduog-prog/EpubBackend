import os
import uuid
import tempfile
import pytest
from app.schemas.library import NovelCreateRequest, NovelUpdateRequest, ChapterStatus
from app.services.library_service import LibraryService


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
