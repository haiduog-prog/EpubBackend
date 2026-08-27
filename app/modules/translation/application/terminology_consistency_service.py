"""Deterministic terminology scanning and validation for chapter translation.

The service deliberately keeps the source-of-truth in Book Bible and exposes
small pure helpers so the chapter workflow and regression tests can share the
same matching rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

from app.llm.errors import GeminiProviderError
from app.modules.book_bible.application.facade import BookBibleService
from app.modules.shared.ports import LLMClient
from app.schemas.book_bible import BookBible, TermEntry
from app.schemas.translation import QAIssue


@dataclass(frozen=True)
class TerminologyScanResult:
    bible: BookBible
    complete: bool
    windows_scanned: int
    errors: List[str] = field(default_factory=list)


class TerminologyConsistencyService:
    """Book-Bible scan plus zero-cost canonical-name checks."""

    NAMED_TERM_CATEGORIES = frozenset(
        {"beast_species", "spirit_beast", "race", "species", "faction", "organization", "identity"}
    )

    @classmethod
    def requires_canonical(cls, term: TermEntry) -> bool:
        original = (term.original_name or "").strip()
        return len(original) >= 2 and (
            term.category.casefold() in cls.NAMED_TERM_CATEGORIES or any("\u3400" <= char <= "\u9fff" for char in original)
        )

    DEFAULT_THRESHOLD = 15_000
    DEFAULT_WINDOW_SIZE = 8_000
    DEFAULT_OVERLAP = 800

    @staticmethod
    def build_windows(
        text: str,
        *,
        threshold: int = DEFAULT_THRESHOLD,
        window_size: int = DEFAULT_WINDOW_SIZE,
        overlap: int = DEFAULT_OVERLAP,
    ) -> List[str]:
        """Return one full window for normal chapters or overlapping windows.

        A window is always non-empty. Invalid overlap/size values are clamped
        instead of producing an infinite loop.
        """
        if not text:
            return []
        if len(text) <= max(1, threshold):
            return [text]
        size = max(1, window_size)
        shared = min(max(0, overlap), size - 1)
        step = max(1, size - shared)
        return [text[start : start + size] for start in range(0, len(text), step)]

    @staticmethod
    def _contains(haystack: str, needle: str) -> bool:
        return bool(needle) and needle.casefold() in (haystack or "").casefold()

    @classmethod
    def check_translation(
        cls,
        original_text: str,
        translated_text: str,
        bible: BookBible,
    ) -> List[QAIssue]:
        """Check every source term whose canonical name must survive translation."""
        issues: List[QAIssue] = []
        seen: set[Tuple[str, str, str]] = set()

        def add(issue: QAIssue) -> None:
            key = (issue.found.casefold(), issue.expected.casefold(), issue.issue)
            if key not in seen:
                seen.add(key)
                issues.append(issue)

        for term in bible.terms:
            if not cls._contains(original_text, term.original_name):
                continue
            if cls.requires_canonical(term) and term.vi_name and not cls._contains(translated_text, term.vi_name):
                add(
                    QAIssue(
                        issue=(
                            f"Thiếu thuật ngữ canonical '{term.vi_name}' cho tên gốc "
                            f"'{term.original_name}'"
                        ),
                        found="<missing>",
                        expected=term.vi_name,
                        location=translated_text[:240],
                    )
                )
            for forbidden in term.forbidden_variants:
                if cls._contains(translated_text, forbidden):
                    add(
                        QAIssue(
                            issue=(
                                f"Phát hiện biến thể cấm '{forbidden}', phải dùng "
                                f"'{term.vi_name}'"
                            ),
                            found=forbidden,
                            expected=term.vi_name,
                            location=translated_text[:240],
                        )
                    )
        return issues

    @classmethod
    async def scan_full_chapter(
        cls,
        llm_client: LLMClient,
        bible: BookBible,
        source_text: str,
        *,
        chapter_index: Optional[int] = None,
        model: Optional[str] = None,
        threshold: int = DEFAULT_THRESHOLD,
        window_size: int = DEFAULT_WINDOW_SIZE,
        overlap: int = DEFAULT_OVERLAP,
    ) -> TerminologyScanResult:
        """Extract all windows and merge only after each extraction succeeds."""
        windows = cls.build_windows(
            source_text,
            threshold=threshold,
            window_size=window_size,
            overlap=overlap,
        )
        if not windows:
            return TerminologyScanResult(bible=bible, complete=True, windows_scanned=0)

        working = bible.model_copy(deep=True)
        errors: List[str] = []
        scanned = 0
        try:
            for window in windows:
                known_names = BookBibleService.get_known_names_index(working)
                delta = await llm_client.extract_book_bible_delta(
                    window, known_names, model=model
                )
                if delta:
                    working = BookBibleService.merge_delta(
                        working, delta, chapter_index=chapter_index
                    )
                scanned += 1
        except GeminiProviderError:
            raise
        except Exception as exc:  # caller decides whether to publish or draft
            errors.append(str(exc))
            return TerminologyScanResult(
                bible=bible,
                complete=False,
                windows_scanned=scanned,
                errors=errors,
            )
        return TerminologyScanResult(
            bible=working,
            complete=True,
            windows_scanned=scanned,
        )


__all__ = ["TerminologyConsistencyService", "TerminologyScanResult"]
