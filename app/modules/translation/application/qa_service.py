from dataclasses import dataclass
from typing import List, Optional

from app.modules.shared.ports import LLMClient
from app.schemas.book_bible import BookBible
from app.schemas.translation import QAIssue, QAReport
from app.modules.book_bible.domain.address_term_policy import cjk_sequences
from app.modules.translation.application.terminology_consistency_service import TerminologyConsistencyService


@dataclass(frozen=True)
class TranslationQualityResult:
    translated_text: str
    report: QAReport
    correction_attempted: bool = False


class TranslationQualityError(RuntimeError):
    def __init__(self, issues: List[QAIssue]):
        self.issues = issues
        super().__init__("Bản dịch không vượt qua kiểm tra chất lượng.")


class QAService:
    """Translation consistency checks owned by the Translation context."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def fast_rule_check(
        self,
        original_text: str,
        translated_text: str,
        book_bible: BookBible,
    ) -> List[QAIssue]:
        issues: List[QAIssue] = []
        original_lower = original_text.lower()
        translated_lower = translated_text.lower()
        seen = set()

        def add_issue(issue: QAIssue) -> None:
            key = (issue.found.lower(), issue.expected.lower(), issue.issue)
            if key not in seen:
                seen.add(key)
                issues.append(issue)

        for sequence in cjk_sequences(translated_text):
            position = translated_text.find(sequence)
            add_issue(
                QAIssue(
                    issue=f"Bản dịch còn chứa chữ Hán/CJK '{sequence}', phải chuyển sang tiếng Việt",
                    found=sequence,
                    expected="Tiếng Việt",
                    location=translated_text[
                        max(0, position - 20) : position + len(sequence) + 20
                    ],
                )
            )

        for character in book_bible.characters:
            original_name = (character.original_name or "").lower()
            vi_name = (character.vi_name or "").lower()
            if original_name and original_name in original_lower and original_name in translated_lower:
                if original_name != vi_name:
                    position = translated_lower.find(original_name)
                    add_issue(
                        QAIssue(
                            issue=(
                                f"Tên gốc '{character.original_name}' bị lọt vào bản dịch "
                                f"thay vì '{character.vi_name}'"
                            ),
                            found=character.original_name,
                            expected=character.vi_name,
                            location=translated_text[
                                max(0, position - 20) : position + len(character.original_name) + 20
                            ],
                        )
                    )

        for term in book_bible.terms:
            original_name = (term.original_name or "").lower()
            vi_name = (term.vi_name or "").lower()
            if original_name and original_name in original_lower and original_name in translated_lower:
                if original_name != vi_name:
                    position = translated_lower.find(original_name)
                    add_issue(
                        QAIssue(
                            issue=(
                                f"Thuật ngữ gốc '{term.original_name}' bị lọt vào bản dịch "
                                f"thay vì '{term.vi_name}'"
                            ),
                            found=term.original_name,
                            expected=term.vi_name,
                            location=translated_text[
                                max(0, position - 20) : position + len(term.original_name) + 20
                            ],
                        )
                    )

        for term in book_bible.terms:
            original_name = (term.original_name or "").lower()
            vi_name = (term.vi_name or "").lower()
            if TerminologyConsistencyService.requires_canonical(term) and original_name and original_name in original_lower and vi_name and vi_name not in translated_lower:
                add_issue(
                    QAIssue(
                        issue=(
                            f"Thuật ngữ '{term.original_name}' xuất hiện trong nguồn nhưng thiếu tên canonical "
                            f"'{term.vi_name}' trong bản dịch"
                        ),
                        found=(translated_text[:120] or "<missing>"),
                        expected=term.vi_name,
                        location=translated_text[:240],
                    )
                )
            for forbidden in getattr(term, "forbidden_variants", []) or []:
                forbidden_lower = forbidden.lower().strip()
                if forbidden_lower and forbidden_lower in translated_lower:
                    position = translated_lower.find(forbidden_lower)
                    add_issue(
                        QAIssue(
                            issue=(
                                f"Biến thể không hợp lệ '{forbidden}' của '{term.original_name}' xuất hiện "
                                f"thay vì '{term.vi_name}'"
                            ),
                            found=forbidden,
                            expected=term.vi_name,
                            location=translated_text[
                                max(0, position - 20) : position + len(forbidden) + 20
                            ],
                        )
                    )

        return issues

    async def verify_chunk(
        self,
        original_text: str,
        translated_text: str,
        book_bible: BookBible,
        force_ai: bool = False,
    ) -> QAReport:
        rule_issues = self.fast_rule_check(original_text, translated_text, book_bible)
        if rule_issues or force_ai:
            ai_report = await self.llm_client.qa_check_chunk(translated_text, book_bible)
            return QAReport(issues=rule_issues + ai_report.issues)
        return QAReport(issues=[])

    async def translate_with_quality(
        self,
        original_text: str,
        book_bible: BookBible,
        previous_context: str = "",
        model: Optional[str] = None,
    ) -> TranslationQualityResult:
        translate_kwargs = {
            "chunk_text": original_text,
            "book_bible": book_bible,
            "previous_context": previous_context,
        }
        if model is not None:
            translate_kwargs["model"] = model
        translated_text = await self.llm_client.translate_prose_chunk(**translate_kwargs)
        return await self.correct_and_recheck(
            original_text,
            translated_text,
            book_bible,
            model=model,
        )

    async def correct_and_recheck(
        self,
        original_text: str,
        translated_text: str,
        book_bible: BookBible,
        model: Optional[str] = None,
    ) -> TranslationQualityResult:
        report = QAReport(issues=self.fast_rule_check(original_text, translated_text, book_bible))
        if not report.issues:
            return TranslationQualityResult(translated_text=translated_text, report=report)

        corrector = getattr(self.llm_client, "correct_translation_terms", None)
        if not callable(corrector):
            return TranslationQualityResult(translated_text=translated_text, report=report)

        try:
            correction_kwargs = {
                "source_text": original_text,
                "translated_text": translated_text,
                "book_bible": book_bible,
                "issues": report.issues,
            }
            if model is not None:
                correction_kwargs["model"] = model
            corrected_text = await corrector(**correction_kwargs)
        except NotImplementedError:
            return TranslationQualityResult(translated_text=translated_text, report=report)

        corrected_text = (corrected_text or translated_text).strip()
        final_report = QAReport(
            issues=self.fast_rule_check(original_text, corrected_text, book_bible)
        )
        return TranslationQualityResult(
            translated_text=corrected_text,
            report=final_report,
            correction_attempted=True,
        )


__all__ = ["QAService", "TranslationQualityError", "TranslationQualityResult"]
