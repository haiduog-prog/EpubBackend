from abc import ABC, abstractmethod
from typing import List, Optional
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem, QAIssue, QAReport, SemanticReviewReport


class BaseLLMClient(ABC):
    """
    Abstract Interface cho tất cả các LLM Providers (Anthropic, Gemini, etc.)
    """

    @abstractmethod
    async def extract_book_bible_delta(
        self,
        source_text: str,
        known_names_index: str,
        model: Optional[str] = None
    ) -> BookBibleDelta:
        """Prompt 1: Trích xuất Book Bible Delta dạng JSON hợp lệ"""
        pass

    @abstractmethod
    async def translate_prose_chunk(
        self,
        chunk_text: str,
        book_bible: BookBible,
        previous_context: str = "",
        model: Optional[str] = None
    ) -> str:
        """Prompt 2: Dịch văn bản prose txt/epub"""
        pass

    async def correct_translation_terms(
        self,
        source_text: str,
        translated_text: str,
        book_bible: BookBible,
        issues: List[QAIssue],
        model: Optional[str] = None,
    ) -> str:
        """Correct only deterministic translation issues when the adapter supports it."""
        raise NotImplementedError("This LLM adapter does not support translation correction.")

    @abstractmethod
    async def translate_html_json(
        self,
        input_items: List[HTMLInputItem],
        book_bible: BookBible,
        model: Optional[str] = None
    ) -> List[HTMLTranslationItem]:
        """Prompt 3: Dịch mảng JSON trích từ HTML gốc"""
        pass

    @abstractmethod
    async def qa_check_chunk(
        self,
        translated_chunk: str,
        book_bible: BookBible,
        model: Optional[str] = None
    ) -> QAReport:
        """Prompt 4: Kiểm tra QA nhất quán (Tên riêng, xưng hô, thuật ngữ)"""
        pass

    async def semantic_review_chapter(
        self,
        source_text: str,
        translated_text: str,
        book_bible: BookBible,
        model: Optional[str] = None,
    ) -> SemanticReviewReport:
        """Review a complete chapter and return exact local replacement patches."""
        raise NotImplementedError("This LLM adapter does not support semantic chapter review.")
