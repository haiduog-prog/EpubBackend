from __future__ import annotations

from dataclasses import dataclass
import inspect
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("EpubBackend.SemanticReview")

from app.config import settings
from app.modules.shared.ports import LLMClient
from app.schemas.book_bible import BookBible
from app.schemas.translation import TranslationPatch
from app.modules.translation.application.qa_service import QAService


@dataclass(frozen=True)
class SemanticReviewResult:
    translated_text: str
    issues: List[Dict[str, Any]]
    applied_patch_count: int = 0
    status: str = "passed"
    error: Optional[str] = None


class SemanticReviewService:
    """Run a chapter-level Gemini review and apply only safe local patches."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    @staticmethod
    def resolve_reviewer_model(translation_model: Optional[str] = None) -> str:
        """Choose a configured reviewer model that is distinct from the translator."""
        reviewer_model = (settings.gemini_review_model or "").strip()
        translated = (translation_model or "").strip().casefold()
        if reviewer_model and reviewer_model.casefold() != translated:
            return reviewer_model
        for candidate in str(getattr(settings, "gemini_model_pool", "")).split(","):
            candidate = candidate.strip()
            if candidate and candidate.casefold() != translated:
                return candidate
        return ""

    @staticmethod
    def _issue_dict(issue: TranslationPatch) -> Dict[str, Any]:
        return issue.model_dump()

    @staticmethod
    def _rule_issue_dict(issue: Any) -> Dict[str, Any]:
        return {
            "old_text": issue.found or "",
            "replacement": issue.expected or "",
            "reason": issue.issue or "",
            "confidence": 1.0,
        }

    async def review_chapter(
        self,
        source_text: str,
        translated_text: str,
        book_bible: BookBible,
        model: Optional[str] = None,
        apply_patches: bool = True,
    ) -> SemanticReviewResult:
        if not settings.gemini_review_enabled:
            return SemanticReviewResult(translated_text=translated_text, issues=[], status="skipped")

        reviewer_model = (model or settings.gemini_review_model or "").strip()
        if not reviewer_model.strip():
            return SemanticReviewResult(
                translated_text=translated_text,
                issues=[],
                status="needs_review",
                error="Gemini reviewer model chưa được cấu hình.",
            )

        reviewer = getattr(self.llm_client, "semantic_review_chapter", None)
        if not callable(reviewer) or not inspect.iscoroutinefunction(reviewer):
            # Keep custom/test adapters and non-Gemini providers backward compatible.
            return SemanticReviewResult(translated_text=translated_text, issues=[], status="skipped")

        try:
            report = await reviewer(
                source_text=source_text,
                translated_text=translated_text,
                book_bible=book_bible,
                model=reviewer_model,
            )
        except Exception as exc:
            exc_str = str(exc)
            if any(k in exc_str.lower() for k in ["quota", "rate limit", "429", "resource_exhausted"]):
                # Thử fallback sang gemini-flash-latest nếu reviewer_model trước đó là model khác (như pro)
                if reviewer_model != "gemini-flash-latest":
                    try:
                        report = await reviewer(
                            source_text=source_text,
                            translated_text=translated_text,
                            book_bible=book_bible,
                            model="gemini-flash-latest",
                        )
                    except Exception as fallback_exc:
                        logger.warning("Semantic review fallback sang flash cũng hết quota: %s", fallback_exc)
                        return SemanticReviewResult(
                            translated_text=translated_text,
                            issues=[],
                            status="skipped",
                            error=None,
                        )
                else:
                    logger.warning("Semantic review gặp giới hạn quota, bỏ qua review tự động: %s", exc)
                    return SemanticReviewResult(
                        translated_text=translated_text,
                        issues=[],
                        status="skipped",
                        error=None,
                    )
            else:
                return SemanticReviewResult(
                    translated_text=translated_text,
                    issues=[],
                    status="needs_review",
                    error=exc_str[:500],
                )

        patches = list(report.issues or [])
        if not apply_patches:
            return SemanticReviewResult(
                translated_text=translated_text,
                issues=[self._issue_dict(issue) for issue in patches],
                status="needs_review" if patches else "passed",
            )
        if len(patches) > settings.gemini_review_max_issues:
            return SemanticReviewResult(
                translated_text=translated_text,
                issues=[self._issue_dict(issue) for issue in patches],
                status="needs_review",
                error="Reviewer trả về quá nhiều issue.",
            )

        min_confidence = settings.gemini_review_min_confidence
        auto_patches = [p for p in patches if p.confidence >= min_confidence]
        unresolved = [p for p in patches if p.confidence < min_confidence]
        positions = []
        for patch in auto_patches:
            occurrences = []
            start = 0
            while True:
                found = translated_text.find(patch.old_text, start)
                if found < 0:
                    break
                occurrences.append(found)
                start = found + max(1, len(patch.old_text))
            if len(occurrences) != 1:
                return SemanticReviewResult(
                    translated_text=translated_text,
                    issues=[self._issue_dict(issue) for issue in patches],
                    status="needs_review",
                    error="Reviewer trả về patch không khớp duy nhất với bản dịch.",
                )
            start = occurrences[0]
            positions.append((start, start + len(patch.old_text), patch))

        positions.sort(key=lambda item: item[0])
        for previous, current in zip(positions, positions[1:]):
            if current[0] < previous[1]:
                return SemanticReviewResult(
                    translated_text=translated_text,
                    issues=[self._issue_dict(issue) for issue in patches],
                    status="needs_review",
                    error="Reviewer trả về các patch chồng lấn.",
                )

        denominator = max(1, len(translated_text))
        changed_chars = sum(end - start for start, end, _ in positions)
        if changed_chars / denominator > settings.gemini_review_max_change_ratio:
            return SemanticReviewResult(
                translated_text=translated_text,
                issues=[self._issue_dict(issue) for issue in patches],
                status="needs_review",
                error="Tỷ lệ nội dung reviewer muốn sửa vượt giới hạn.",
            )

        reviewed_text = translated_text
        for start, end, patch in reversed(positions):
            reviewed_text = reviewed_text[:start] + patch.replacement + reviewed_text[end:]

        rule_issues = QAService(self.llm_client).fast_rule_check(source_text, reviewed_text, book_bible)
        issues = [self._issue_dict(issue) for issue in unresolved]
        issues.extend(self._rule_issue_dict(issue) for issue in rule_issues)
        status = "needs_review" if issues else "passed"
        return SemanticReviewResult(
            translated_text=reviewed_text,
            issues=issues,
            applied_patch_count=len(positions),
            status=status,
            error=None,
        )


__all__ = ["SemanticReviewResult", "SemanticReviewService"]
