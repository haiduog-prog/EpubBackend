import pytest
from fastapi.testclient import TestClient

from app.modules.reader.schemas import ReaderBookDetail
from app.modules.reader.service import (
    ReaderNotFoundError,
    ReaderService,
    ReaderValidationError,
)
from app.main import app
from app.schemas.library import ChapterItem, ChapterStatus, NovelMetadata, NovelStatus, NovelSummary


def _chapter(index: int, *, status=ChapterStatus.COMPLETED, marker="translated-key") -> ChapterItem:
    return ChapterItem(
        chapter_index=index,
        chapter_id=f"ch_{index:04d}",
        chapter_title=f"Chương {index}",
        status=status,
        word_count=100,
        translated_text_preview="Bản dịch..." if marker else "",
        r2_translated_key=marker,
    )


def _metadata(chapters):
    return NovelMetadata(
        novel_id="reader-book",
        title="Truyện đọc thử",
        original_title="Reader Test",
        author="Tác giả",
        status=NovelStatus.ONGOING,
        total_chapters=len(chapters),
        translated_chapters=sum(c.status == ChapterStatus.COMPLETED for c in chapters),
        chapters=chapters,
    )


@pytest.fixture
def reader_service():
    metadata = _metadata(
        [
            _chapter(1),
            _chapter(2, status=ChapterStatus.NOT_TRANSLATED, marker=""),
            _chapter(5),
            _chapter(8),
        ]
    )

    class FakeLibrary:
        def list_novels(self):
            return [NovelSummary(novel_id=metadata.novel_id, title=metadata.title)]

        def get_novel(self, novel_id):
            return metadata if novel_id == metadata.novel_id else None

        def get_chapter_content(self, novel_id, chapter_index, version="translated"):
            if version != "translated":
                raise AssertionError("Reader must never request original content")
            return {1: "Nội dung chương một.", 5: "Nội dung chương năm."}.get(chapter_index)

    return ReaderService(FakeLibrary())


def test_list_books_excludes_books_without_readable_chapters(reader_service):
    result = reader_service.list_books()

    assert len(result) == 1
    assert result[0].translated_chapters == 3
    assert result[0].total_chapters == 4


def test_get_book_filters_untranslated_and_missing_marker_chapters(reader_service):
    result = reader_service.get_book("reader-book")

    assert isinstance(result, ReaderBookDetail)
    assert [chapter.chapter_index for chapter in result.chapters] == [1, 5, 8]


def test_get_book_excludes_metadata_chapter_zero(reader_service):
    metadata = _metadata([_chapter(0), _chapter(1)])

    class FakeLibrary:
        def list_novels(self):
            return [NovelSummary(novel_id=metadata.novel_id, title=metadata.title)]

        def get_novel(self, novel_id):
            return metadata if novel_id == metadata.novel_id else None

    result = ReaderService(FakeLibrary()).get_book("reader-book")

    assert [chapter.chapter_index for chapter in result.chapters] == [1]


def test_get_chapter_returns_previous_and_next_readable_chapters(reader_service):
    result = reader_service.get_chapter("reader-book", 5)

    assert result.content == "Nội dung chương năm."
    assert result.previous_chapter.chapter_index == 1
    assert result.next_chapter.chapter_index == 8


def test_reader_exposes_public_storage_url_for_direct_reads():
    metadata = _metadata([_chapter(1, marker="novels/reader-book/chapters/1.translated.txt")])

    class FakeLibrary:
        def get_novel(self, novel_id):
            return metadata if novel_id == metadata.novel_id else None

        def get_chapter_content(self, novel_id, chapter_index, version="translated"):
            return "Nội dung chương một."

        def get_chapter_content_url(self, chapter, version="translated"):
            return "https://project.supabase.co/storage/v1/object/public/novels/novels/reader-book/chapters/1.translated.txt"

        def get_chapter_content_urls(self, chapter, version="translated"):
            return [
                "https://project.supabase.co/storage/v1/object/public/novels/novels/reader-book/chapters/1.translated.txt",
                "https://cdn.example.com/reader-book/1.txt",
            ]

    result = ReaderService(FakeLibrary()).get_book("reader-book")
    assert result.chapters[0].content_url is None
    assert result.chapters[0].content_urls == []

def test_get_chapter_fails_closed_when_storage_content_is_missing(reader_service):
    with pytest.raises(ReaderNotFoundError, match="chưa sẵn sàng"):
        reader_service.get_chapter("reader-book", 8)


def test_reader_rejects_unsafe_novel_ids(reader_service):
    with pytest.raises(ReaderValidationError):
        reader_service.get_book("../reader-book")


def test_reader_api_is_read_only_and_maps_expected_errors(reader_service, monkeypatch):
    import app.modules.reader.api as reader_api

    monkeypatch.setattr(reader_api, "reader_service", reader_service)
    client = TestClient(app)

    books = client.get("/api/v1/reader/books")
    assert books.status_code == 200
    assert books.json()[0]["translated_chapters"] == 3

    chapter = client.get("/api/v1/reader/books/reader-book/chapters/5")
    assert chapter.status_code == 200
    assert chapter.json()["previous_chapter"]["chapter_index"] == 1
    assert chapter.json()["next_chapter"]["chapter_index"] == 8

    missing_content = client.get("/api/v1/reader/books/reader-book/chapters/8")
    assert missing_content.status_code == 404

    invalid_book = client.get("/api/v1/reader/books/not%20a%20slug")
    assert invalid_book.status_code == 422

    reader_page = client.get("/reader")
    assert reader_page.status_code == 200
    assert 'id="reader-view"' in reader_page.text
    assert 'localStorage.setItem(progressKey' in reader_page.text
    assert 'window.EpubAuth' in reader_page.text
    assert "fetch(apiUrl(path)" in reader_page.text

    reader_paths = {
        path: methods
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/api/v1/reader/books") or path == "/reader"
    }
    assert reader_paths
    assert all(set(methods) == {"get"} for methods in reader_paths.values())