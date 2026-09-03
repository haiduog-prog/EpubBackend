from dataclasses import dataclass
import inspect
import re
from typing import List, Optional

from app.modules.shared.ports import LLMClient
from app.parsers.text_sanitizer import WATERMARK_PATTERNS
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

    def __init__(self, llm_client: Optional[LLMClient] = None):
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

        # 1. CJK characters leakage
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

        # 2. Original character name leakage
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

        # 3. Character forbidden variants check (only when character is in source)
        for character in book_bible.characters:
            char_orig = (character.original_name or "").lower().strip()
            if char_orig and char_orig in original_lower:
                for forbidden in getattr(character, "forbidden_variants", []) or []:
                    forbidden_lower = forbidden.lower().strip()
                    if forbidden_lower and forbidden_lower in translated_lower:
                        position = translated_lower.find(forbidden_lower)
                        add_issue(
                            QAIssue(
                                issue=(
                                    f"Biến thể nhân vật không hợp lệ '{forbidden}' xuất hiện "
                                    f"thay vì '{character.vi_name}'"
                                ),
                                found=forbidden,
                                expected=character.vi_name,
                                location=translated_text[
                                    max(0, position - 20) : position + len(forbidden) + 20
                                ],
                            )
                        )

        # 4. Original term leakage
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

        # 5. Canonical term missing & forbidden variants
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
            term_orig = (term.original_name or "").lower().strip()
            term_vi = (term.vi_name or "").lower().strip()
            term_present = (term_orig and term_orig in original_lower) or (term_vi and term_vi in original_lower) or not original_text
            if term_present:
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

        # 6. Pronoun modern drift check (huynh/muội -> anh/em)
        sg = getattr(book_bible, "style_guide", None)
        policy = getattr(sg, "pronoun_policy", "") or ""
        era = getattr(sg, "era_setting", "") or ""
        if policy in {"huynh_muoi", "ancient"} or "cổ phong" in era.lower() or "tiên hiệp" in era.lower():
            if re.search(r"\b(huynh|muội|tỷ|đệ|ca ca|tiểu muội)\b", original_lower) or policy == "huynh_muoi":
                for m in re.finditer(r"\b(anh|em)\b", translated_lower):
                    pos = m.start()
                    found_word = m.group(1)
                    # Bỏ qua từ ghép tiếng Việt: anh hùng, anh dũng, anh danh, anh ruột, em ruột, v.v.
                    after = translated_lower[m.end() : m.end() + 15]
                    if re.match(r"^\s+(?:ruột|họ|danh|hùng|dũng|tuấn|minh|kiệt|linh|đào|gái|trai|em\b)", after):
                        continue
                    # Bỏ qua tên riêng được tiền tố họ (VD: Lý Anh, Quy Anh) hoặc từ ghép (anh em, chị em)
                    before = translated_lower[max(0, pos - 15) : pos]
                    if re.search(r"(?:lý|trần|nguyễn|lê|phạm|hoàng|huỳnh|vũ|võ|phan|trương|bùi|đặng|đỗ|ngô|dương|quy)\s+$", before):
                        continue
                    if re.search(r"\b(?:anh|chị)\s+$", before):
                        continue
                    add_issue(
                        QAIssue(
                            issue=f"Xưng hô hiện đại '{found_word}' vi phạm chính sách xưng hô cổ phong ({policy or era})",
                            found=found_word,
                            expected="huynh/muội/đệ/tỷ",
                            location=translated_text[max(0, pos - 20) : pos + 30],
                        )
                    )
                    break

        # 7. Unallowed foreign hybrid token check (e.g. "Cửu Transfer")
        allowed_bible_tokens = set()
        for c in book_bible.characters:
            for token in re.findall(r"\b[A-Za-z]+\b", c.vi_name or ""):
                allowed_bible_tokens.add(token.lower())
            for alias in getattr(c, "aliases", []) or []:
                for token in re.findall(r"\b[A-Za-z]+\b", alias):
                    allowed_bible_tokens.add(token.lower())
        for t in book_bible.terms:
            for token in re.findall(r"\b[A-Za-z]+\b", t.vi_name or ""):
                allowed_bible_tokens.add(token.lower())

        foreign_patterns = re.compile(
            r"\b[A-Za-z]*(?:[fjwzFJWZ]|(?:cl|cr|dr|fl|fr|gl|gr|pl|pr|sk|sl|sm|sn|sp|st|str|sw|tw|nsf|rns|ght|rld|tch|tion|sion|ment|ous|ble)|[bdfjklrsvwxzBDFJKLRSVWXZ]$)[A-Za-z]*\b"
        )
        for match in foreign_patterns.finditer(translated_text):
            word = match.group(0)
            if len(word) < 3:
                continue
            if word.lower() in original_lower or word.lower() in allowed_bible_tokens:
                continue
            if word.upper() in {"VIP", "NPC", "EXP", "TOP", "HOT", "AI", "OK", "3D", "2D"}:
                continue
            pos = match.start()
            add_issue(
                QAIssue(
                    issue=f"Từ ngoại lai chưa dịch '{word}' xuất hiện trong bản dịch",
                    found=word,
                    expected="Thuật ngữ tiếng Việt chuẩn",
                    location=translated_text[max(0, pos - 20) : pos + len(word) + 20],
                )
            )

        # 8. Disallowed unicode characters (Arabic, Greek, Cyrillic)
        foreign_match = re.search(r"[\u0600-\u06FF\u0750-\u077F\u0370-\u03FF\u1F00-\u1FFF\u0400-\u04FF]", translated_text)
        if foreign_match:
            pos = foreign_match.start()
            bad_char = foreign_match.group(0)
            add_issue(
                QAIssue(
                    issue=f"Ký tự ngoại lai bất thường '{bad_char}' xuất hiện trong bản dịch",
                    found=bad_char,
                    expected="Ký tự tiếng Việt hợp lệ",
                    location=translated_text[max(0, pos - 20) : pos + 20],
                )
            )

        # 9. Repeated functional words check
        stutter_words = r"(?:ra|vào|lên|xuống|trong|ngoài|của|và|đã|đang|sẽ|bị|được|ở|tại|với|cho|nhưng|thì|mà|là|những|các|không|chưa|chẳng|rất|quá|lại|đều)"
        stutter_match = re.search(rf"\b({stutter_words})\s+\1\b", translated_lower)
        if stutter_match:
            pos = stutter_match.start()
            word = stutter_match.group(1)
            add_issue(
                QAIssue(
                    issue=f"Lỗi lặp từ chức năng ('{word} {word}') gây vấp ngữ cảnh",
                    found=f"{word} {word}",
                    expected=word,
                    location=translated_text[max(0, pos - 20) : pos + len(word) * 2 + 25],
                )
            )

        # 10. Missing chapter header check
        source_has_header = bool(re.match(r"^\s*(?:chương\s+\d+|hồi\s+\d+|thứ\s+\d+\s+chương|第\s*[\d0-9一二三四五六七八九十百千万]+\s*章)", original_lower))
        if source_has_header:
            trans_has_header = bool(re.match(r"^\s*(?:chương\s+\d+|hồi\s+\d+|thứ\s+\d+\s+chương)", translated_lower))
            if not trans_has_header:
                add_issue(
                    QAIssue(
                        issue="Bản dịch bị thiếu tiêu đề chương ở dòng đầu tiên",
                        found="<thiếu tiêu đề>",
                        expected="Chương [Số]: [Tiêu đề]",
                        location=translated_text[:120],
                    )
                )

        # 11. Watermark / converter noise leakage check
        for pattern in WATERMARK_PATTERNS:
            match = pattern.search(translated_text)
            if match:
                pos = match.start()
                matched_wm = match.group(0).strip()
                if matched_wm:
                    add_issue(
                        QAIssue(
                            issue=f"Bản dịch bị dính watermark/quảng cáo rác: '{matched_wm}'",
                            found=matched_wm,
                            expected="<loại bỏ watermark>",
                            location=translated_text[max(0, pos - 20) : pos + len(matched_wm) + 20],
                        )
                    )
                    break

        # 12. Book Bible style-level forbidden patterns
        for raw_pattern in getattr(sg, "forbidden_regex", []) or []:
            if not raw_pattern:
                continue
            try:
                match = re.search(raw_pattern, translated_text, re.IGNORECASE)
            except re.error:
                # A malformed editor rule must not crash the translation gate.
                continue
            if match:
                pos = match.start()
                found = match.group(0)
                add_issue(
                    QAIssue(
                        issue=f"Bản dịch chứa mẫu bị cấm '{raw_pattern}'",
                        found=found,
                        expected="Không xuất hiện mẫu bị cấm",
                        location=translated_text[max(0, pos - 20) : pos + len(found) + 20],
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
            if self.llm_client:
                ai_report = await self.llm_client.qa_check_chunk(translated_text, book_bible)
                return QAReport(issues=rule_issues + ai_report.issues)
            return QAReport(issues=rule_issues)
        return QAReport(issues=[])

    async def translate_with_quality(
        self,
        original_text: str,
        book_bible: BookBible,
        previous_context: str = "",
        model: Optional[str] = None,
    ) -> TranslationQualityResult:
        if not self.llm_client:
            raise RuntimeError("LLMClient is required for translation.")
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
            result = corrector(**correction_kwargs)
            if inspect.isawaitable(result):
                corrected_text = await result
            elif isinstance(result, str):
                corrected_text = result
            else:
                return TranslationQualityResult(translated_text=translated_text, report=report)
        except (NotImplementedError, TypeError):
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
