from typing import Any, List, Optional, Protocol

from app.schemas.book_bible import BookBible, BookBibleDelta
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem, QAIssue, QAReport


class LLMClient(Protocol):
    """Application-facing LLM port; concrete SDK adapters stay outside modules."""

    async def extract_book_bible_delta(
        self,
        source_text: str,
        known_names_index: str,
        model: Optional[str] = None,
    ) -> BookBibleDelta: ...

    async def translate_prose_chunk(
        self,
        chunk_text: str,
        book_bible: BookBible,
        previous_context: str = "",
        model: Optional[str] = None,
    ) -> str: ...

    async def correct_translation_terms(
        self,
        source_text: str,
        translated_text: str,
        book_bible: BookBible,
        issues: List[QAIssue],
        model: Optional[str] = None,
    ) -> str: ...

    async def translate_html_json(
        self,
        input_items: List[HTMLInputItem],
        book_bible: BookBible,
        model: Optional[str] = None,
    ) -> List[HTMLTranslationItem]: ...

    async def qa_check_chunk(
        self,
        translated_chunk: str,
        book_bible: BookBible,
        model: Optional[str] = None,
    ) -> QAReport: ...


class BlobStore(Protocol):
    def put_bytes(self, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> Optional[str]: ...

    def get_bytes(self, object_name: str, raise_on_error: bool = False) -> Optional[bytes]: ...

    def delete_file(self, object_name: str) -> bool: ...


class JobStore(Protocol):
    def save_job(self, job: Any) -> None: ...

    def get_job(self, job_id: str) -> Optional[Any]: ...

    def list_jobs(self) -> List[Any]: ...


class BibleStore(Protocol):
    def get_bible(self, novel_id: str) -> Optional[Any]: ...

    def save_bible(self, novel_id: str, bible: Any) -> None: ...

    def merge_bible_delta(self, novel_id: str, delta: Any, **kwargs) -> Any: ...

    def delete_bible(self, novel_id: str) -> bool: ...
