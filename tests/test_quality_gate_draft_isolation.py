import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.config import settings
from app.modules.library.legacy_service import LegacyLibraryService as LibraryService
from app.schemas.book_bible import BookBible, TermEntry
from app.modules.library.schemas import ChapterItem, ChapterStatus, NovelMetadata as NovelItem
from app.schemas.translation import QAIssue, QAReport
from app.modules.translation.application.qa_service import TranslationQualityResult


@pytest.fixture
def mock_storage(tmp_path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    return storage_dir


@pytest.mark.asyncio
async def test_quality_gate_fails_closed_to_draft_on_qa_issues(tmp_path, monkeypatch):
    """When translation has QA/terminology issues, it must save to drafts/ and status=needs_review."""
    service = LibraryService()
    novel_id = "test-novel-isolation-1"
    chapter_index = 120

    novel = NovelItem(
        novel_id=novel_id,
        title="Test Novel",
        total_chapters=1,
        translated_chapters=0,
        chapters=[
            ChapterItem(
                chapter_index=chapter_index,
                chapter_title="Chương 120",
                status=ChapterStatus.NOT_TRANSLATED,
            )
        ],
    )

    orig_text = "Đỗ Phong thi triển phi hành thuật."
    bad_trans = "Đỗ Phong thi triển phù thủy."

    monkeypatch.setattr(service, "get_novel", lambda nid: novel if nid == novel_id else None)
    monkeypatch.setattr(
        service,
        "get_chapter_content",
        lambda nid, idx, version="original", **kw: orig_text if version == "original" else None,
    )
    saved_files = {}

    def mock_save_raw_file(key, content, content_type="text/plain"):
        saved_files[key] = content.decode("utf-8")
        return f"http://mock/{key}"

    monkeypatch.setattr(service, "_save_raw_file", mock_save_raw_file)
    monkeypatch.setattr(service, "_save_metadata", lambda m: None)

    # Mock Bible with locked forbidden term
    bible = BookBible(
        novel_id=novel_id,
        terms=[
            TermEntry(
                original_name="phi hành thuật",
                vi_name="Phi Hành Thuật",
                category="skill",
                forbidden_variants=["phù thủy"],
                locked=True,
            )
        ],
    )

    from app.infrastructure.storage.facade import storage_repo
    monkeypatch.setattr(storage_repo, "get_bible", lambda nid: bible)
    monkeypatch.setattr(storage_repo, "save_bible", lambda nid, b: None)

    # Mock LLM and QA Service
    with patch("app.modules.library.legacy_service.create_llm_client") as mock_client_factory:
        mock_llm = MagicMock()
        mock_client_factory.return_value = mock_llm

        # Mock translate_with_quality to return bad text with a detected issue
        with patch("app.modules.library.legacy_service.QAService") as mock_qa_cls:
            mock_qa = MagicMock()
            mock_qa.translate_with_quality = AsyncMock(
                return_value=TranslationQualityResult(
                    translated_text=bad_trans,
                    report=QAReport(
                        issues=[
                            QAIssue(
                                issue="Biến thể không hợp lệ 'phù thủy'",
                                found="phù thủy",
                                expected="Phi Hành Thuật",
                                location="...",
                            )
                        ]
                    ),
                )
            )
            mock_qa.fast_rule_check = MagicMock(
                return_value=[
                    QAIssue(
                        issue="Biến thể không hợp lệ 'phù thủy'",
                        found="phù thủy",
                        expected="Phi Hành Thuật",
                        location="...",
                    )
                ]
            )
            mock_qa_cls.return_value = mock_qa

            with patch("app.modules.translation.application.terminology_consistency_service.TerminologyConsistencyService.scan_full_chapter") as mock_scan:
                scan_mock = MagicMock()
                scan_mock.bible = bible
                scan_mock.complete = True
                mock_scan.return_value = scan_mock

                result_chapter = await service.translate_chapter(
                    novel_id=novel_id,
                    chapter_index=chapter_index,
                    provider="gemini",
                    api_key="test-key",
                    model="gemini-flash",
                )

    # Verification:
    # 1. Chapter status must be NEEDS_REVIEW
    assert result_chapter.status == ChapterStatus.NEEDS_REVIEW

    # 2. Saved under drafts/, NOT translated/
    draft_key = f"novels/{novel_id}/drafts/ch_{chapter_index:04d}.txt"
    trans_key = f"novels/{novel_id}/translated/ch_{chapter_index:04d}.txt"
    assert draft_key in saved_files
    assert trans_key not in saved_files


@pytest.mark.asyncio
async def test_quality_gate_passes_clean_translation_to_completed(monkeypatch):
    """When translation has 0 issues, it must save directly to translated/ and status=completed."""
    service = LibraryService()
    novel_id = "test-novel-isolation-2"
    chapter_index = 120

    novel = NovelItem(
        novel_id=novel_id,
        title="Test Novel Clean",
        total_chapters=1,
        translated_chapters=0,
        chapters=[
            ChapterItem(
                chapter_index=chapter_index,
                chapter_title="Chương 120",
                status=ChapterStatus.NOT_TRANSLATED,
            )
        ],
    )

    orig_text = "Đỗ Phong thi triển phi hành thuật."
    clean_trans = "Đỗ Phong thi triển Phi Hành Thuật."

    monkeypatch.setattr(service, "get_novel", lambda nid: novel if nid == novel_id else None)
    monkeypatch.setattr(
        service,
        "get_chapter_content",
        lambda nid, idx, version="original", **kw: orig_text if version == "original" else None,
    )
    saved_files = {}

    def mock_save_raw_file(key, content, content_type="text/plain"):
        saved_files[key] = content.decode("utf-8")
        return f"http://mock/{key}"

    monkeypatch.setattr(service, "_save_raw_file", mock_save_raw_file)
    monkeypatch.setattr(service, "_save_metadata", lambda m: None)
    monkeypatch.setattr(service, "mark_dirty", lambda *a, **kw: None)

    bible = BookBible(novel_id=novel_id)
    from app.infrastructure.storage.facade import storage_repo
    monkeypatch.setattr(storage_repo, "get_bible", lambda nid: bible)
    monkeypatch.setattr(storage_repo, "save_bible", lambda nid, b: None)

    with patch("app.modules.library.legacy_service.create_llm_client") as mock_client_factory:
        mock_llm = MagicMock()
        mock_client_factory.return_value = mock_llm

        with patch("app.modules.library.legacy_service.QAService") as mock_qa_cls:
            mock_qa = MagicMock()
            mock_qa.translate_with_quality = AsyncMock(
                return_value=TranslationQualityResult(
                    translated_text=clean_trans,
                    report=QAReport(issues=[]),
                )
            )
            mock_qa.fast_rule_check = MagicMock(return_value=[])
            mock_qa_cls.return_value = mock_qa

            with patch("app.modules.translation.application.terminology_consistency_service.TerminologyConsistencyService.scan_full_chapter") as mock_scan:
                scan_mock = MagicMock()
                scan_mock.bible = bible
                scan_mock.complete = True
                mock_scan.return_value = scan_mock

                result_chapter = await service.translate_chapter(
                    novel_id=novel_id,
                    chapter_index=chapter_index,
                    provider="gemini",
                    api_key="test-key",
                    model="gemini-flash",
                )

    # Verification:
    # 1. Chapter status must be COMPLETED
    assert result_chapter.status == ChapterStatus.COMPLETED

    # 2. Saved under translated/, NOT drafts/
    draft_key = f"novels/{novel_id}/drafts/ch_{chapter_index:04d}.txt"
    trans_key = f"novels/{novel_id}/translated/ch_{chapter_index:04d}.txt"
    assert trans_key in saved_files
    assert draft_key not in saved_files


@pytest.mark.asyncio
async def test_review_chapter_standalone(monkeypatch):
    """Verifies standalone review endpoint service logic without retranslating."""
    service = LibraryService()
    novel_id = "test-novel-standalone"
    chapter_index = 125

    novel = NovelItem(
        novel_id=novel_id,
        title="Standalone Test Novel",
        total_chapters=1,
        translated_chapters=0,
        chapters=[
            ChapterItem(
                chapter_index=chapter_index,
                chapter_title="Chương 125",
                status=ChapterStatus.NEEDS_REVIEW,
            )
        ],
    )

    orig_text = "Hắn thi triển Cửu Chuyển Huyền Công."
    draft_text = "Hắn thi triển Cửu Transfer Huyền Công."

    monkeypatch.setattr(service, "get_novel", lambda nid: novel if nid == novel_id else None)
    monkeypatch.setattr(
        service,
        "get_chapter_content",
        lambda nid, idx, version="original", **kw: orig_text if version == "original" else (draft_text if version == "draft" else None),
    )
    monkeypatch.setattr(service, "_save_metadata", lambda m: None)

    bible = BookBible(novel_id=novel_id)
    from app.infrastructure.storage.facade import storage_repo
    monkeypatch.setattr(storage_repo, "get_bible", lambda nid: bible)

    with patch("app.modules.library.legacy_service.create_llm_client"):
        result = await service.review_chapter_standalone(
            novel_id=novel_id,
            chapter_index=chapter_index,
            provider="gemini",
            api_key="test",
            model="gemini-flash",
        )

    assert result["novel_id"] == novel_id
    assert result["chapter_index"] == chapter_index
    # Unallowed foreign token 'Transfer' should be caught by fast_rule_check!
    assert result["passed"] is False
    assert result["review_status"] == "needs_review"
    assert any("Transfer" in str(i) for i in result["issues"])


@pytest.mark.asyncio
async def test_review_chapter_standalone_fails_when_original_is_missing(monkeypatch):
    service = LibraryService()
    novel_id = "test-novel-missing-orig"
    chapter_index = 1

    novel = NovelItem(
        novel_id=novel_id,
        title="Missing Original Novel",
        total_chapters=1,
        translated_chapters=1,
        chapters=[
            ChapterItem(
                chapter_index=chapter_index,
                chapter_title="Chương 1",
                status=ChapterStatus.COMPLETED,
            )
        ],
    )

    monkeypatch.setattr(service, "get_novel", lambda nid: novel if nid == novel_id else None)
    # Original is None or equal to translated
    monkeypatch.setattr(
        service,
        "get_chapter_content",
        lambda nid, idx, version="original", **kw: None if version == "original" else "Bản dịch...",
    )

    with pytest.raises(ValueError, match="thiếu nội dung bản gốc"):
        await service.review_chapter_standalone(
            novel_id=novel_id,
            chapter_index=chapter_index,
            provider="gemini",
            api_key="test",
            model="gemini-flash",
        )


def test_get_chapter_draft_details_returns_reasons_and_suggestions(monkeypatch):
    """Ensure get_chapter_draft_details surfaces QA issues with smart replacements."""
    service = LibraryService()
    novel_id = "test-novel-draft-details"
    chapter_index = 170

    draft_text = "Chương 170: Sơn động\n\nKhông có gì đâu, chúng em đang đợi Trình sư huynh tới mà."
    orig_text = "第170章 山洞\n\n没什么，我们在等程师兄过来呢。"

    novel = NovelItem(
        novel_id=novel_id,
        title="Test Novel",
        total_chapters=1,
        translated_chapters=0,
        chapters=[
            ChapterItem(
                chapter_index=chapter_index,
                chapter_title="Chương 170: Sơn động",
                status=ChapterStatus.NEEDS_REVIEW,
                translated_text_preview="NEEDS_REVIEW: Xưng hô hiện đại 'chúng em'; draft_key=...",
            )
        ],
    )

    monkeypatch.setattr(service, "get_novel", lambda nid: novel if nid == novel_id else None)
    monkeypatch.setattr(service, "get_chapter_draft_content", lambda nid, idx: draft_text if (nid == novel_id and idx == chapter_index) else None)
    monkeypatch.setattr(service, "get_chapter_content", lambda nid, idx, version="original", **kw: orig_text if version == "original" else None)

    details = service.get_chapter_draft_details(novel_id, chapter_index)
    assert details.novel_id == novel_id
    assert details.chapter_index == chapter_index
    assert details.version == "draft"
    assert "chúng em" in details.review_reason.lower()
    assert len(details.issues) >= 1
    issue = next((i for i in details.issues if "chúng em" in i.found.lower()), None)
    assert issue is not None
    assert issue.replacement == "chúng đệ"

