from typing import List
from app.schemas.book_bible import BookBible
from app.schemas.translation import QAIssue, QAReport
from app.llm.base import BaseLLMClient


class QAService:
    """
    Hệ thống kiểm tra nhất quán bản dịch (Theo Mục 4):
    - Bước 1: Fast rule-based dictionary check (tra soát tên gốc sót trong bản dịch) - Chạy mặc định cho mọi chunk vì rất rẻ.
    - Bước 2: Chỉ gọi AI QA (Prompt 4) khi rule-based check phát hiện bất thường hoặc khi được yêu cầu.
    """

    def __init__(self, llm_client: BaseLLMClient):
        self.llm_client = llm_client

    def fast_rule_check(self, original_text: str, translated_text: str, book_bible: BookBible) -> List[QAIssue]:
        """
        So khớp tên riêng/thuật ngữ bằng dictionary lookup (literal string matching).
        """
        issues: List[QAIssue] = []

        for char in book_bible.characters:
            if char.original_name and char.original_name.lower() in original_text.lower():
                # Nếu tên gốc xuất hiện ở bản gốc, nhưng bản dịch chứa nguyên văn tên gốc thay vì vi_name
                if char.original_name.lower() in translated_text.lower() and char.original_name.lower() != char.vi_name.lower():
                    pos = translated_text.lower().find(char.original_name.lower())
                    start = max(0, pos - 20)
                    end = min(len(translated_text), pos + len(char.original_name) + 20)
                    loc_snippet = translated_text[start:end]

                    issues.append(QAIssue(
                        issue=f"Tên gốc '{char.original_name}' bị lọt vào bản dịch thay vì '{char.vi_name}'",
                        found=char.original_name,
                        expected=char.vi_name,
                        location=loc_snippet
                    ))

        for term in book_bible.terms:
            if term.original_name and term.original_name.lower() in original_text.lower():
                if term.original_name.lower() in translated_text.lower() and term.original_name.lower() != term.vi_name.lower():
                    pos = translated_text.lower().find(term.original_name.lower())
                    start = max(0, pos - 20)
                    end = min(len(translated_text), pos + len(term.original_name) + 20)
                    loc_snippet = translated_text[start:end]

                    issues.append(QAIssue(
                        issue=f"Thuật ngữ gốc '{term.original_name}' bị lọt vào bản dịch thay vì '{term.vi_name}'",
                        found=term.original_name,
                        expected=term.vi_name,
                        location=loc_snippet
                    ))

        return issues

    async def verify_chunk(
        self,
        original_text: str,
        translated_text: str,
        book_bible: BookBible,
        force_ai: bool = False
    ) -> QAReport:
        """
        Thực hiện QA: Chạy rule-based pre-check trước.
        Chỉ gọi Prompt 4 AI QA khi phát hiện điểm bất thường hoặc khi force_ai=True.
        """
        rule_issues = self.fast_rule_check(original_text, translated_text, book_bible)
        
        # Mục 4: "chỉ gọi AI khi phát hiện bất thường hoặc người dùng báo lỗi"
        if rule_issues or force_ai:
            ai_report = await self.llm_client.qa_check_chunk(translated_text, book_bible)
            all_issues = rule_issues + ai_report.issues
            return QAReport(issues=all_issues)

        return QAReport(issues=[])
