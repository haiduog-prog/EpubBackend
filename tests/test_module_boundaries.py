from app.modules.book_bible.application.facade import BookBibleService as ModularBookBibleService
from app.modules.book_bible.domain.merge_service import BookBibleMergeService
from app.modules.character_profiles.application.facade import CharacterProfileApplication
from app.modules.library.application.chapter_service import ChapterService
from app.modules.library.application.epub_export_service import EpubExportService
from app.modules.library.application.epub_import_service import EpubImportService
from app.modules.library.application.facade import LibraryService
from app.modules.library.application.novel_service import NovelService
from app.modules.translation.application.direct_translation_service import DirectTranslationService
from app.modules.translation.application.epub_translation_service import EpubTranslationService
from app.modules.translation.application.facade import TranslationPipelineService
from app.modules.translation.application.txt_translation_service import TxtTranslationService
from app.services.book_bible_service import BookBibleService as LegacyBookBibleService
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


def test_api_contract_is_stable_after_router_move():
    paths = app.openapi()["paths"]

    assert len(paths) == 41
    assert "/api/v1/translate/text" in paths
    assert "/api/v1/translate/file" in paths
    assert "/api/v1/library/novels" in paths
    assert "/api/v1/book-bible/editions/{edition_id}/chapters/{local_chapter}/snapshot" in paths
