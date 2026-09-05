from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.api.v1.translate import router  # Initialize compatibility routers first.
from app.config import settings
from app.llm.errors import GeminiProviderError
from app.llm.gemini_provider import GeminiProvider
from app.modules.library import legacy_service as library
from app.modules.translation import api
from app.modules.translation.application.qa_service import QAService
from app.modules.translation.application.semantic_review_service import SemanticReviewResult, SemanticReviewService
from app.modules.translation.legacy_pipeline import LegacyTranslationPipelineService
from app.parsers import epub_parser
from app.parsers.txt_chunker import TXTChunker
from app.schemas.book_bible import BookBible, BookBibleDelta, CharacterEntry
from app.schemas.translation import InputType, JobStatusEnum, TranslationJob, SemanticReviewReport, TranslationPatch


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,text", [("MAX_TOKENS", "Một phần bản dịch"), ("SAFETY", "Một phần"), ("STOP", "")])
async def test_provider_rejects_incomplete_or_empty_translation(reason, text):
    client = GeminiProvider.__new__(GeminiProvider)
    client._call_with_fallback = AsyncMock(return_value=SimpleNamespace(
        text=text, candidates=[SimpleNamespace(finish_reason=reason)],
    ))
    with pytest.raises(GeminiProviderError):
        await QAService(client).translate_with_quality("原文", BookBible())


@pytest.mark.asyncio
async def test_provider_accepts_complete_translation():
    client = GeminiProvider.__new__(GeminiProvider)
    client._call_with_fallback = AsyncMock(return_value=SimpleNamespace(
        text="Hắn bước vào phòng.", candidates=[SimpleNamespace(finish_reason="STOP")],
    ))
    result = await QAService(client).translate_with_quality("他走进房间。", BookBible())
    assert result.translated_text == "Hắn bước vào phòng."
    assert not result.report.issues


def test_empty_output_fails_qa_for_other_providers():
    assert QAService(None).fast_rule_check("Nguồn", " ", BookBible())


@pytest.mark.parametrize("source", ["他走进房间。" * 20000, ("他走进房间。" * 100 + "\n") * 100, "word " * 12000],
                         ids=["cjk-long-paragraph", "cjk-many-paragraphs", "latin-long-paragraph"])
def test_chunk_limits_preserve_all_non_whitespace_text(source):
    chunks = TXTChunker().chunk_text(source)
    assert len(chunks) > 1
    assert all(0 < len(chunk.text) <= 8000 for chunk in chunks)
    assert all(len(chunk.previous_context) <= 1000 for chunk in chunks)
    assert "".join("".join(c.text.split()) for c in chunks) == "".join(source.split())


class TitleLLM:
    def __init__(self):
        self.sources = []

    async def extract_book_bible_delta(self, *args, **kwargs):
        return BookBibleDelta()

    async def translate_prose_chunk(self, chunk_text, **kwargs):
        self.sources.append(chunk_text)
        return chunk_text.replace("第120章 风雨", "Chương 120: Mưa gió").replace(
            "第121章 回家", "Chương 121: Về nhà"
        ).replace("他走进房间。", "Hắn bước vào phòng.")


@pytest.mark.asyncio
async def test_direct_translation_includes_cjk_title_in_ai_and_qa():
    llm = TitleLLM()
    pipeline = LegacyTranslationPipelineService(llm)
    text, _ = await pipeline.translate_direct_text("第120章 风雨\n他走进房间。", chapter_index=120)
    assert "第120章 风雨" in llm.sources[0]
    assert text.startswith("Chương 120: Mưa gió")
    assert not pipeline.last_quality_report.issues


@pytest.mark.asyncio
async def test_txt_translates_all_cjk_headings(tmp_path):
    source = tmp_path / "source.txt"
    output = tmp_path / "translated.txt"
    source.write_text("第120章 风雨\n他走进房间。\n第121章 回家\n他走进房间。", encoding="utf-8")
    await LegacyTranslationPipelineService(TitleLLM()).translate_txt_file(str(source), str(output))
    text = output.read_text(encoding="utf-8")
    assert "Chương 120: Mưa gió" in text
    assert "Chương 121: Về nhà" in text
    assert not QAService(None).fast_rule_check(source.read_text(encoding="utf-8"), text, BookBible())


@pytest.fixture
def chapter_service(monkeypatch):
    chapter = SimpleNamespace(chapter_index=120, chapter_title="Mưa gió", r2_original_key="original")
    meta = SimpleNamespace(chapters=[chapter])
    service = library.LegacyLibraryService.__new__(library.LegacyLibraryService)
    service.get_novel = Mock(return_value=meta)
    texts = {"original": "第120章 风雨\n他走进房间。", "translated": "Chương 120: Mưa gió\nHắn bước vào phòng."}
    service.get_chapter_content = Mock(side_effect=lambda *a, version, **kw: texts.get(version))
    service._save_metadata = Mock()
    monkeypatch.setattr(library, "storage_repo", SimpleNamespace(get_bible=lambda _: BookBible()))
    return service, chapter


@pytest.mark.asyncio
async def test_library_preview_translates_chinese_title(chapter_service, monkeypatch):
    service, _ = chapter_service
    llm = TitleLLM()
    monkeypatch.setattr(library, "create_llm_client", lambda **kw: llm)
    monkeypatch.setattr(settings, "semantic_review_mode", "manual_only")
    result = await service.translate_chapter(str(uuid4()), 120, preview_only=True)
    assert result.new_translated_text.startswith("Chương 120: Mưa gió")
    assert result.qa_status == "passed"


@pytest.mark.asyncio
@pytest.mark.parametrize("review_status,error", [("needs_review", "Reviewer unavailable"), ("skipped", None), ("passed", None)])
async def test_standalone_review_preserves_review_outcome(chapter_service, monkeypatch, review_status, error):
    service, chapter = chapter_service
    monkeypatch.setattr(library, "create_llm_client", lambda **kw: SimpleNamespace(default_model="translator"))
    reviewer = AsyncMock(return_value=SemanticReviewResult(
        translated_text="unchanged", issues=[], status=review_status, error=error,
    ))
    monkeypatch.setattr(library.SemanticReviewService, "review_chapter", reviewer)
    result = await service.review_chapter_standalone(str(uuid4()), 120)
    assert result["passed"] is (review_status == "passed")
    assert chapter.review_status == review_status
    assert chapter.review_error == error
    assert reviewer.call_args.kwargs["apply_patches"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", [InputType.TXT, InputType.EPUB])
async def test_file_job_loads_existing_bible(monkeypatch, input_type):
    novel_id, job_id = str(uuid4()), str(uuid4())
    original = BookBible(novel_id=novel_id, characters=[CharacterEntry(original_name="A", vi_name="Tên chuẩn", locked=True)])
    job = TranslationJob(job_id=job_id, filename="book.txt", input_type=input_type, novel_id=novel_id)
    repo = SimpleNamespace(get_job=lambda _: job, get_bible=Mock(return_value=original),
                           save_job=Mock(), save_bible=Mock(), is_blob_active=False, r2_enabled=False)
    async def translate(**kwargs):
        bible = kwargs["bible"]
        assert bible.characters[0].locked
        assert bible.characters[0].vi_name == "Tên chuẩn"
        assert bible is not original
        return bible
    pipeline = SimpleNamespace(translate_txt_file=translate, translate_epub_file=translate)
    monkeypatch.setattr(api, "storage_repo", repo)
    monkeypatch.setattr(api, "create_llm_client", lambda **kw: object())
    monkeypatch.setattr(api, "TranslationPipelineService", lambda _: pipeline)
    await api.run_translation_background_job(job_id, "unused", "unused", input_type, novel_id=novel_id)
    assert job.status == JobStatusEnum.COMPLETED
    repo.get_bible.assert_called_once_with(novel_id)


def test_epub_chapters_follow_spine_and_skip_nonreading_documents(monkeypatch):
    def item(id):
        return SimpleNamespace(get_id=lambda: id, get_content=lambda: b"<p>Chapter text</p>")
    documents = [item("ch2"), item("nav"), item("ch1")]
    book = SimpleNamespace(spine=[("ch1", "yes"), ("nav", "no"), ("ch2", "yes")],
                           get_items_of_type=lambda _: documents)
    monkeypatch.setattr(epub_parser, "read_epub_safe", lambda _: book)
    assert [id for id, _, _ in epub_parser.EPUBParser.read_epub_chapters("unused")] == ["ch1", "ch2"]


@pytest.mark.asyncio
async def test_manual_review_returns_patches_without_silently_applying_them(monkeypatch):
    monkeypatch.setattr(settings, "gemini_review_enabled", True)
    patch = TranslationPatch(old_text="đông", replacement="tây", confidence=1.0)
    client = SimpleNamespace(semantic_review_chapter=AsyncMock(
        return_value=SemanticReviewReport(issues=[patch]),
    ))
    result = await SemanticReviewService(client).review_chapter(
        "原文", "Hắn đi về đông.", BookBible(), model="reviewer", apply_patches=False,
    )
    assert result.translated_text == "Hắn đi về đông."
    assert result.issues == [patch.model_dump()]
    assert result.applied_patch_count == 0
    assert result.status == "needs_review"
