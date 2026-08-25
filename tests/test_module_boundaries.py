from pathlib import Path

from app.modules.book_bible.application.facade import BookBibleService as ModularBookBibleService
from app.modules.book_bible.domain.merge_service import BookBibleMergeService
from app.modules.character_profiles.application.facade import CharacterProfileApplication
from app.modules.library.application.chapter_service import ChapterService
from app.modules.library.application.epub_export_service import EpubExportService
from app.modules.library.application.epub_import_service import EpubImportService
from app.modules.library.application.facade import LibraryService
from app.modules.library.application.novel_service import NovelService
from app.modules.reader.service import ReaderService
from app.modules.translation.application.direct_translation_service import DirectTranslationService
from app.modules.translation.application.epub_translation_service import EpubTranslationService
from app.modules.translation.application.facade import TranslationPipelineService
from app.modules.translation.application.txt_translation_service import TxtTranslationService
from app.infrastructure.storage.facade import StorageRepository, storage_repo
from app.infrastructure.storage.job_store import JobStore
from app.infrastructure.storage.bible_store import BibleStore
from app.infrastructure.cache.direct_translation import DirectTranslationCache
from app.modules.shared.ports import LLMClient
from app.modules.translation.application.qa_service import QAService as ModularQAService
from app.services.book_bible_service import BookBibleService as LegacyBookBibleService
from app.services.qa_service import QAService as LegacyQAService
from app.services.translation_cache import DirectTranslationCache as LegacyCache
from app.services.library_service import LibraryService as LegacyLibraryService
from app.services.pipeline_service import TranslationPipelineService as LegacyPipelineService
from app.schemas.book_bible import BookBible
from app.main import app


def test_legacy_imports_resolve_to_modular_facades():
    assert LegacyLibraryService is LibraryService
    assert LegacyPipelineService is TranslationPipelineService
    assert issubclass(ModularBookBibleService, LegacyBookBibleService)


def test_library_facade_exposes_one_owner_per_use_case():
    service = LibraryService()

    assert isinstance(service.novels, NovelService)
    assert isinstance(service.chapters, ChapterService)
    assert isinstance(service.imports, EpubImportService)
    assert isinstance(service.exports, EpubExportService)


def test_translation_facade_exposes_format_specific_pipelines():
    service = TranslationPipelineService(object())

    assert isinstance(service.direct, DirectTranslationService)
    assert isinstance(service.txt, TxtTranslationService)
    assert isinstance(service.epub, EpubTranslationService)


def test_book_bible_merge_boundary_preserves_timeline_contract():
    bible = BookBible(novel_id="boundary-test")

    result = BookBibleMergeService.ensure_timeline(bible)

    assert result.novel_id == "boundary-test"


def test_storage_adapters_are_importable_from_source_tree():
    assert StorageRepository is not None
    assert storage_repo is not None
    assert isinstance(JobStore(storage_repo), JobStore)
    assert isinstance(BibleStore(storage_repo), BibleStore)


def test_translation_support_has_modular_owners_and_compatibility_shims():
    assert LegacyQAService is ModularQAService
    assert LegacyCache is DirectTranslationCache
    assert LLMClient is not None


def test_reader_depends_on_library_application_not_persistence():
    reader_source = Path(ReaderService.__module__.replace(".", "/") + ".py")
    source = reader_source.read_text(encoding="utf-8")

    assert "app.modules.library.application.facade" in source
    assert "storage_repo" not in source
    assert "db_session" not in source


def test_static_ui_does_not_persist_api_key_and_has_html_escape_helper():
    html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "localStorage.setItem('epub_api_key'" not in html
    assert "function escapeHtml(value)" in html


def test_api_contract_is_stable_after_router_move():
    paths = app.openapi()["paths"]

    # The authenticated reader flow adds the Supabase config and reader sync
    # endpoints to the public contract.
    assert len(paths) == 54
    assert "/api/v1/translate/text" in paths
    assert "/api/v1/translate/file" in paths
    assert "/api/v1/library/novels" in paths
    assert "/api/v1/library/novels/bulk-delete" in paths
    assert "/api/v1/library/novels/{novel_id}/chapters/{chapter_index}/apply-translation" in paths
    assert "/api/v1/book-bible/editions/{edition_id}/chapters/{local_chapter}/snapshot" in paths
    assert "/api/v1/reader/books" in paths
    assert "/api/v1/reader/me/state" in paths
    assert "/api/v1/reader/me/migrate-local" in paths
    assert "/api/v1/reader/me/preferences" in paths
    assert "/api/v1/reader/me/progress/{novel_id}" in paths
    assert "/api/v1/reader/books/{novel_id}/cover" in paths
    assert "/api/auth/config" in paths
    assert "/reader" in paths