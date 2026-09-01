import pytest

from app.config import settings
from app.modules.translation.application.semantic_review_service import SemanticReviewService
from app.schemas.book_bible import BookBible
from app.schemas.translation import SemanticReviewReport, TranslationPatch


class FakeReviewer:
    def __init__(self, report=None, error=None):
        self.report = report or SemanticReviewReport()
        self.error = error

    async def semantic_review_chapter(self, **kwargs):
        if self.error:
            raise self.error
        return self.report


def test_reviewer_model_does_not_match_translation_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_review_model", "gemini-pro-latest")
    monkeypatch.setattr(settings, "gemini_model_pool", "gemini-pro-latest,gemini-flash-lite-latest")
    assert SemanticReviewService.resolve_reviewer_model("gemini-pro-latest") == "gemini-flash-lite-latest"


@pytest.mark.asyncio
async def test_applies_high_confidence_patch_and_keeps_low_confidence_issue(monkeypatch):
    monkeypatch.setattr(settings, "gemini_review_enabled", True)
    monkeypatch.setattr(settings, "gemini_review_min_confidence", 0.9)
    monkeypatch.setattr(settings, "gemini_review_max_change_ratio", 0.2)
    report = SemanticReviewReport(
        issues=[
            TranslationPatch(old_text="đi về đông", replacement="đi về tây", reason="Sai hướng", confidence=0.98),
            TranslationPatch(old_text="cậu ấy", replacement="anh ấy", reason="Chưa chắc chủ thể", confidence=0.5),
        ]
    )
    original = "Nhân vật đi về đông rồi cậu ấy dừng lại. Câu chuyện tiếp tục trong căn phòng yên tĩnh với nhiều chi tiết khác."
    result = await SemanticReviewService(FakeReviewer(report)).review_chapter(
        "原文", original, BookBible(), model="review-model"
    )
    assert result.translated_text == "Nhân vật đi về tây rồi cậu ấy dừng lại. Câu chuyện tiếp tục trong căn phòng yên tĩnh với nhiều chi tiết khác."
    assert result.applied_patch_count == 1
    assert result.status == "needs_review"
    assert len(result.issues) == 1
    assert result.issues[0]["old_text"] == "cậu ấy"


@pytest.mark.asyncio
async def test_rejects_duplicate_patch_without_partial_write(monkeypatch):
    monkeypatch.setattr(settings, "gemini_review_enabled", True)
    report = SemanticReviewReport(
        issues=[TranslationPatch(old_text="đúng", replacement="sai", reason="Lỗi", confidence=0.99)]
    )
    original = "đúng và đúng"
    result = await SemanticReviewService(FakeReviewer(report)).review_chapter(
        "source", original, BookBible(), model="review-model"
    )
    assert result.translated_text == original
    assert result.applied_patch_count == 0
    assert result.status == "needs_review"


@pytest.mark.asyncio
async def test_reviewer_error_keeps_translation(monkeypatch):
    monkeypatch.setattr(settings, "gemini_review_enabled", True)
    original = "Bản dịch hiện tại."
    result = await SemanticReviewService(FakeReviewer(error=TimeoutError("timeout"))).review_chapter(
        "source", original, BookBible(), model="review-model"
    )
    assert result.translated_text == original
    assert result.status == "needs_review"
    assert "timeout" in (result.error or "")
