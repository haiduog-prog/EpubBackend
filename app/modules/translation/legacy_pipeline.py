import inspect
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.modules.shared.ports import LLMClient
from app.parsers.epub_parser import EPUBParser
from app.parsers.txt_chunker import TXTChunker
from app.parsers.text_sanitizer import (
    clean_raw_text,
    extract_chapter_title_prefix,
    reattach_chapter_title,
    split_chapter_sections,
)
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.llm.errors import StructuredOutputError
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem, QAIssue, QAReport
from app.modules.book_bible.domain.address_resolver import AddressRuleResolver
from app.modules.book_bible.application.facade import BookBibleService
from app.modules.book_bible.domain.review_policy import HybridPolicyEngine
from app.modules.translation.application.qa_service import (
    QAService,
    TranslationQualityError,
    TranslationQualityResult,
)

logger = logging.getLogger("EpubBackend.PipelineService")


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class LegacyTranslationPipelineService:
    """Extract theo cua so, ghi observation truoc khi dich cua so do."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        self.qa_service = QAService(llm_client)
        self.policy = HybridPolicyEngine()
        self.last_quality_report = QAReport(issues=[])
        self.last_correction_attempted = False

    def _reset_quality_state(self) -> None:
        self.last_quality_report = QAReport(issues=[])
        self.last_correction_attempted = False

    def _record_quality_result(self, result: TranslationQualityResult) -> None:
        self.last_correction_attempted = (
            self.last_correction_attempted or result.correction_attempted
        )
        known = {
            (issue.found.casefold(), issue.expected.casefold(), issue.issue)
            for issue in self.last_quality_report.issues
        }
        issues: List[QAIssue] = list(self.last_quality_report.issues)
        for issue in result.report.issues:
            key = (issue.found.casefold(), issue.expected.casefold(), issue.issue)
            if key not in known:
                known.add(key)
                issues.append(issue)
        self.last_quality_report = QAReport(issues=issues)

    async def _translate_prose_checked(
        self,
        chunk_text: str,
        book_bible: BookBible,
        previous_context: str = "",
        model: Optional[str] = None,
    ) -> str:
        result = await self.qa_service.translate_with_quality(
            original_text=chunk_text,
            book_bible=book_bible,
            previous_context=previous_context,
            model=model,
        )
        self._record_quality_result(result)
        return result.translated_text

    async def _extract_delta_fail_open(
        self, source_text: str, known_index: str
    ) -> BookBibleDelta:
        try:
            return await self.llm_client.extract_book_bible_delta(source_text, known_index)
        except StructuredOutputError as exc:
            logger.warning("Book Bible enrichment không hợp lệ; tiếp tục dịch với delta rỗng: %s", exc)
            return BookBibleDelta()

    async def extract_initial_book_bible(
        self,
        sample_text: str,
        existing_bible: Optional[BookBible] = None,
        chapter_index: Optional[int] = None,
        chapter_id: str = "initial",
        chunk_id: str = "initial",
    ) -> BookBible:
        bible = existing_bible or BookBible()
        logger.info(
            "[TIMING] stage=book_bible_extract.start chapter=%s chunk=%s text_chars=%d",
            chapter_id,
            chunk_id,
            len(sample_text),
        )
        started = time.perf_counter()
        known_index = BookBibleService.get_known_names_index(bible)
        delta = await self._extract_delta_fail_open(sample_text, known_index)
        logger.info(
            "[TIMING] stage=book_bible_extract.end chapter=%s chunk=%s elapsed_ms=%.1f "
            "new_chars=%d observations=%d",
            chapter_id,
            chunk_id,
            _elapsed_ms(started),
            len(delta.new_characters),
            len(delta.address_observations),
        )

        merge_started = time.perf_counter()
        bible, pending_ids = self.policy.apply_delta(
            bible, delta, chapter_index, chapter_id, chunk_id
        )
        logger.info(
            "[TIMING] stage=book_bible_merge.end chapter=%s chunk=%s elapsed_ms=%.1f "
            "characters=%d observations=%d pending=%d",
            chapter_id,
            chunk_id,
            _elapsed_ms(merge_started),
            len(bible.characters),
            len(bible.address_observations),
            len(pending_ids),
        )
        return bible

    async def translate_direct_text(
        self,
        text: str,
        bible: Optional[BookBible] = None,
        chapter_index: Optional[int] = None,
        chapter_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Tuple[str, BookBible]:
        self._reset_quality_state()
        bible = bible or BookBible()
        request_id = chapter_id or "direct-text"
        started = time.perf_counter()

        # Proactively sanitize converter watermarks & separate chapter title
        cleaned_text = clean_raw_text(text)
        title_prefix, body_text = extract_chapter_title_prefix(cleaned_text)
        work_text = body_text if body_text else cleaned_text

        bible = await self.extract_initial_book_bible(
            work_text,
            bible,
            chapter_index=chapter_index,
            chapter_id=request_id,
            chunk_id=request_id,
        )
        resolver_started = time.perf_counter()
        effective_bible = AddressRuleResolver.apply(bible, chapter_index)
        filtered_bible = BookBibleService.filter_bible_for_text(effective_bible, work_text)
        logger.info(
            "[TIMING] stage=book_bible_resolve.end chapter=%s elapsed_ms=%.1f "
            "visible_chars=%d visible_observations=%d",
            request_id,
            _elapsed_ms(resolver_started),
            len(filtered_bible.characters),
            len(filtered_bible.address_observations),
        )
        translate_started = time.perf_counter()
        translated_text = await self._translate_prose_checked(
            chunk_text=work_text,
            book_bible=filtered_bible,
            previous_context="",
            model=model,
        )
        if title_prefix:
            translated_text = reattach_chapter_title(title_prefix, translated_text, chapter_index=chapter_index)
        logger.info(
            "[TIMING] stage=translate_direct.end chapter=%s elapsed_ms=%.1f total_elapsed_ms=%.1f",
            request_id,
            _elapsed_ms(translate_started),
            _elapsed_ms(started),
        )
        return translated_text, bible

    async def _notify_bible_updated(
        self,
        callback: Optional[Callable[[BookBible], Any]],
        bible: BookBible,
    ) -> None:
        if callback:
            started = time.perf_counter()
            result = callback(bible.model_copy(deep=True))
            if inspect.isawaitable(result):
                await result
            logger.info(
                "[TIMING] stage=book_bible_persist.end novel=%s elapsed_ms=%.1f "
                "revision=%d observations=%d",
                bible.novel_id,
                _elapsed_ms(started),
                bible.bible_revision,
                len(bible.address_observations),
            )

    async def translate_txt_file(
        self,
        input_path: str,
        output_path: str,
        bible: Optional[BookBible] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        on_bible_updated: Optional[Callable[[BookBible], Any]] = None,
        chapter_index_offset: int = 0,
        chapter_id_prefix: str = "txt",
        model: Optional[str] = None,
    ) -> BookBible:
        self._reset_quality_state()
        with open(input_path, "r", encoding="utf-8", errors="ignore") as file:
            full_text = file.read()
        cleaned_text = clean_raw_text(full_text)
        sections = split_chapter_sections(cleaned_text)
        has_titles = any(title for title, _ in sections)
        work_units = []
        titled_chapter = 0
        for section_index, (title, body) in enumerate(sections):
            section_chunks = TXTChunker().chunk_text(body)
            if not section_chunks:
                continue
            if has_titles and title:
                section_chapter_index = chapter_index_offset + titled_chapter
                titled_chapter += 1
            else:
                section_chapter_index = chapter_index_offset
            for chunk in section_chunks:
                unit_index = len(work_units)
                work_units.append(
                    (
                        chunk,
                        title if title and chunk.chunk_index == 0 else None,
                        section_chapter_index if has_titles else chapter_index_offset + unit_index,
                        f"{chapter_id_prefix}-{section_index}-{chunk.chunk_index}"
                        if has_titles
                        else f"{chapter_id_prefix}-{unit_index}",
                    )
                )

        if not work_units:
            raise ValueError("File TXT rong hoac khong doc duoc van ban.")

        logger.info(
            "[TIMING] stage=txt_pipeline.start chunks=%d input_chars=%d",
            len(work_units),
            len(full_text),
        )
        bible = bible or BookBible()
        if progress_callback:
            progress_callback(5.0, "Dang trich xuat Book Bible theo tung chunk...")

        translated_parts: List[str] = []
        total_chunks = len(work_units)

        async def translate_chunk(index: int) -> None:
            chunk, title, current_index, unit_id = work_units[index]
            if progress_callback:
                pct = 10.0 + (index / total_chunks) * 85.0
                progress_callback(pct, f"Dang dich chunk {index + 1}/{total_chunks}...")
            resolver_started = time.perf_counter()
            effective_bible = AddressRuleResolver.apply(bible, current_index)
            filtered_bible = BookBibleService.filter_bible_for_text(
                effective_bible, chunk.text
            )
            resolve_ms = _elapsed_ms(resolver_started)
            translate_started = time.perf_counter()
            translated = await self._translate_prose_checked(
                chunk_text=chunk.text,
                book_bible=filtered_bible,
                previous_context=chunk.previous_context,
                model=model,
            )
            if title:
                translated = reattach_chapter_title(
                    title, translated, chapter_index=current_index
                )
            translated_parts.append(translated)
            logger.info(
                "[TIMING] stage=translate_txt_chunk.end chunk=%d chapter=%d "
                "resolve_ms=%.1f ai_ms=%.1f text_chars=%d bible_chars=%d",
                index,
                current_index,
                resolve_ms,
                _elapsed_ms(translate_started),
                len(chunk.text),
                len(filtered_bible.characters),
            )

        for index, (chunk, title, current_index, unit_id) in enumerate(work_units):
            known_index = BookBibleService.get_known_names_index(bible)
            extract_started = time.perf_counter()
            delta = await self._extract_delta_fail_open(chunk.text, known_index)
            logger.info(
                "[TIMING] stage=book_bible_extract.end chunk=%d elapsed_ms=%.1f "
                "text_chars=%d new_chars=%d observations=%d",
                index,
                _elapsed_ms(extract_started),
                len(chunk.text),
                len(delta.new_characters),
                len(delta.address_observations),
            )
            merge_started = time.perf_counter()
            bible, pending_ids = self.policy.apply_delta(
                bible,
                delta,
                chapter_index=current_index,
                chapter_id=unit_id,
                chunk_id=unit_id,
            )
            logger.info(
                "[TIMING] stage=book_bible_merge.end chunk=%d elapsed_ms=%.1f "
                "pending=%d revision=%d",
                index,
                _elapsed_ms(merge_started),
                len(pending_ids),
                bible.bible_revision,
            )
            await self._notify_bible_updated(on_bible_updated, bible)
            await translate_chunk(index)

        if self.last_quality_report.issues:
            raise TranslationQualityError(self.last_quality_report.issues)

        output_started = time.perf_counter()
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        final_translated = "\n\n".join(translated_parts)
        with open(output_path, "w", encoding="utf-8") as file:
            file.write(final_translated)
        logger.info(
            "[TIMING] stage=txt_output.end elapsed_ms=%.1f output=%s",
            _elapsed_ms(output_started),
            output_path,
        )
        return bible

    async def translate_epub_file(
        self,
        input_path: str,
        output_path: str,
        bible: Optional[BookBible] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        on_bible_updated: Optional[Callable[[BookBible], Any]] = None,
        chapter_index_offset: int = 0,
        chapter_id_prefix: str = "epub",
        model: Optional[str] = None,
    ) -> BookBible:
        self._reset_quality_state()
        chapters = EPUBParser.read_epub_chapters(input_path)
        if not chapters:
            raise ValueError("Khong tim thay chuong HTML hop le trong file EPUB.")

        logger.info("[TIMING] stage=epub_pipeline.start chapters=%d", len(chapters))
        bible = bible or BookBible()
        if progress_callback:
            progress_callback(5.0, "Dang phan tich Book Bible theo tung chuong...")

        translated_chapters: Dict[str, List[HTMLTranslationItem]] = {}
        total_chapters = len(chapters)

        async def translate_chapter(
            index: int,
            chapter_id: str,
            input_items: List[HTMLInputItem],
        ) -> None:
            if progress_callback:
                pct = 10.0 + (index / total_chapters) * 80.0
                progress_callback(
                    pct,
                    f"Dang dich chuong {index + 1}/{total_chapters} ({chapter_id})...",
                )
            chapter_started = time.perf_counter()
            full_text = " ".join(item.text for item in input_items)
            resolver_started = time.perf_counter()
            effective_bible = AddressRuleResolver.apply(
                bible, chapter_index_offset + index
            )
            filtered_bible = BookBibleService.filter_bible_for_text(
                effective_bible, full_text
            )
            resolve_ms = _elapsed_ms(resolver_started)
            translations: List[HTMLTranslationItem] = []
            ai_total_started = time.perf_counter()
            batch_count = 0
            for batch_start in range(0, len(input_items), 40):
                batch_count += 1
                batch_items = input_items[batch_start : batch_start + 40]
                batch_started = time.perf_counter()
                html_kwargs = {
                    "input_items": batch_items,
                    "book_bible": filtered_bible,
                }
                if model is not None:
                    html_kwargs["model"] = model
                batch_translations = await self.llm_client.translate_html_json(**html_kwargs)
                translations_by_id = {item.id: item for item in batch_translations}
                for source_item in batch_items:
                    translation = translations_by_id.get(
                        source_item.id,
                        HTMLTranslationItem(id=source_item.id, text_vi=source_item.text),
                    )
                    checked = await self.qa_service.correct_and_recheck(
                        source_item.text,
                        translation.text_vi,
                        filtered_bible,
                        model=model,
                    )
                    self._record_quality_result(checked)
                    translations.append(
                        HTMLTranslationItem(id=translation.id, text_vi=checked.translated_text)
                    )
                logger.info(
                    "[TIMING] stage=translate_epub_batch.end chapter=%s batch=%d "
                    "batch_items=%d ai_ms=%.1f",
                    chapter_id,
                    batch_count,
                    len(batch_items),
                    _elapsed_ms(batch_started),
                )
            translated_chapters[chapter_id] = translations
            logger.info(
                "[TIMING] stage=translate_epub_chapter.end chapter=%s batches=%d "
                "resolve_ms=%.1f ai_total_ms=%.1f total_ms=%.1f text_chars=%d",
                chapter_id,
                batch_count,
                resolve_ms,
                _elapsed_ms(ai_total_started),
                _elapsed_ms(chapter_started),
                len(full_text),
            )

        for index, (chapter_id, input_items, _) in enumerate(chapters):
            extract_text = " ".join(item.text for item in input_items)
            if extract_text:
                known_index = BookBibleService.get_known_names_index(bible)
                extract_started = time.perf_counter()
                delta = await self._extract_delta_fail_open(extract_text, known_index)
                logger.info(
                    "[TIMING] stage=book_bible_extract.end chapter=%d elapsed_ms=%.1f "
                    "text_chars=%d new_chars=%d observations=%d",
                    index,
                    _elapsed_ms(extract_started),
                    len(extract_text),
                    len(delta.new_characters),
                    len(delta.address_observations),
                )
                merge_started = time.perf_counter()
                bible, pending_ids = self.policy.apply_delta(
                    bible,
                    delta,
                    chapter_index=chapter_index_offset + index,
                    chapter_id=chapter_id,
                    chunk_id=chapter_id,
                )
                logger.info(
                    "[TIMING] stage=book_bible_merge.end chapter=%d elapsed_ms=%.1f pending=%d",
                    index,
                    _elapsed_ms(merge_started),
                    len(pending_ids),
                )
                await self._notify_bible_updated(on_bible_updated, bible)
            if input_items:
                await translate_chapter(index, chapter_id, input_items)

        if self.last_quality_report.issues:
            raise TranslationQualityError(self.last_quality_report.issues)

        if progress_callback:
            progress_callback(95.0, "Dang dong goi file EPUB...")
        output_started = time.perf_counter()
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        EPUBParser.rebuild_epub(input_path, output_path, translated_chapters)
        logger.info(
            "[TIMING] stage=epub_output.end elapsed_ms=%.1f output=%s",
            _elapsed_ms(output_started),
            output_path,
        )
        return bible

