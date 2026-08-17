import inspect
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.llm.base import BaseLLMClient
from app.parsers.epub_parser import EPUBParser
from app.parsers.txt_chunker import TXTChunker
from app.schemas.book_bible import BookBible
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem
from app.services.address_rule_resolver import AddressRuleResolver
from app.services.book_bible_service import BookBibleService
from app.services.hybrid_policy_service import HybridPolicyEngine
from app.services.qa_service import QAService

logger = logging.getLogger("EpubBackend.PipelineService")


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class TranslationPipelineService:
    """Extract theo cua so, ghi observation truoc khi dich cua so do."""

    def __init__(self, llm_client: BaseLLMClient):
        self.llm_client = llm_client
        self.qa_service = QAService(llm_client)
        self.policy = HybridPolicyEngine()

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
        delta = await self.llm_client.extract_book_bible_delta(sample_text, known_index)
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
    ) -> Tuple[str, BookBible]:
        bible = bible or BookBible()
        request_id = chapter_id or "direct-text"
        started = time.perf_counter()
        bible = await self.extract_initial_book_bible(
            text,
            bible,
            chapter_index=chapter_index,
            chapter_id=request_id,
            chunk_id=request_id,
        )
        resolver_started = time.perf_counter()
        effective_bible = AddressRuleResolver.apply(bible, chapter_index)
        filtered_bible = BookBibleService.filter_bible_for_text(effective_bible, text)
        logger.info(
            "[TIMING] stage=book_bible_resolve.end chapter=%s elapsed_ms=%.1f "
            "visible_chars=%d visible_observations=%d",
            request_id,
            _elapsed_ms(resolver_started),
            len(filtered_bible.characters),
            len(filtered_bible.address_observations),
        )
        translate_started = time.perf_counter()
        translated_text = await self.llm_client.translate_prose_chunk(
            chunk_text=text,
            book_bible=filtered_bible,
            previous_context="",
        )
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
    ) -> BookBible:
        with open(input_path, "r", encoding="utf-8", errors="ignore") as file:
            full_text = file.read()
        chunks = TXTChunker().chunk_text(full_text)
        if not chunks:
            raise ValueError("File TXT rong hoac khong doc duoc van ban.")

        logger.info(
            "[TIMING] stage=txt_pipeline.start chunks=%d input_chars=%d",
            len(chunks),
            len(full_text),
        )
        bible = bible or BookBible()
        if progress_callback:
            progress_callback(5.0, "Dang trich xuat Book Bible ban dau...")

        sample_text = "\n\n".join(chunk.text for chunk in chunks[:2])
        bible = await self.extract_initial_book_bible(
            sample_text,
            bible,
            chapter_index=chapter_index_offset,
            chapter_id=f"{chapter_id_prefix}-initial",
            chunk_id=f"{chapter_id_prefix}-initial",
        )
        await self._notify_bible_updated(on_bible_updated, bible)

        translated_parts: List[str] = []
        total_chunks = len(chunks)

        async def translate_chunk(index: int) -> None:
            chunk = chunks[index]
            current_index = chapter_index_offset + index
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
            translated = await self.llm_client.translate_prose_chunk(
                chunk_text=chunk.text,
                book_bible=filtered_bible,
                previous_context=chunk.previous_context,
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

        initial_count = min(2, total_chunks)
        for index in range(initial_count):
            await translate_chunk(index)

        for start in range(initial_count, total_chunks, 3):
            window = chunks[start : start + 3]
            extract_text = "\n\n".join(chunk.text for chunk in window)
            known_index = BookBibleService.get_known_names_index(bible)
            extract_started = time.perf_counter()
            delta = await self.llm_client.extract_book_bible_delta(
                extract_text, known_index
            )
            logger.info(
                "[TIMING] stage=book_bible_extract.end window=%d-%d elapsed_ms=%.1f "
                "text_chars=%d new_chars=%d observations=%d",
                start,
                start + len(window) - 1,
                _elapsed_ms(extract_started),
                len(extract_text),
                len(delta.new_characters),
                len(delta.address_observations),
            )
            merge_started = time.perf_counter()
            bible, pending_ids = self.policy.apply_delta(
                bible,
                delta,
                chapter_index=chapter_index_offset + start,
                chapter_id=f"{chapter_id_prefix}-{start}",
                chunk_id=f"{chapter_id_prefix}-{start}",
            )
            logger.info(
                "[TIMING] stage=book_bible_merge.end window=%d-%d elapsed_ms=%.1f "
                "pending=%d revision=%d",
                start,
                start + len(window) - 1,
                _elapsed_ms(merge_started),
                len(pending_ids),
                bible.bible_revision,
            )
            await self._notify_bible_updated(on_bible_updated, bible)
            for index in range(start, start + len(window)):
                await translate_chunk(index)

        output_started = time.perf_counter()
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n\n".join(translated_parts))
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
    ) -> BookBible:
        chapters = EPUBParser.read_epub_chapters(input_path)
        if not chapters:
            raise ValueError("Khong tim thay chuong HTML hop le trong file EPUB.")

        logger.info("[TIMING] stage=epub_pipeline.start chapters=%d", len(chapters))
        bible = bible or BookBible()
        if progress_callback:
            progress_callback(5.0, "Dang phan tich Book Bible cho file EPUB...")
        sample_texts = [
            item.text for _, items, _ in chapters[:2] for item in items[:10]
        ]
        bible = await self.extract_initial_book_bible(
            "\n".join(sample_texts),
            bible,
            chapter_index=chapter_index_offset,
            chapter_id=f"{chapter_id_prefix}-initial",
            chunk_id=f"{chapter_id_prefix}-initial",
        )
        await self._notify_bible_updated(on_bible_updated, bible)

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
                translations.extend(
                    await self.llm_client.translate_html_json(
                        input_items=batch_items,
                        book_bible=filtered_bible,
                    )
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

        initial_count = min(2, total_chapters)
        for index in range(initial_count):
            chapter_id, input_items, _ = chapters[index]
            if input_items:
                await translate_chapter(index, chapter_id, input_items)

        for start in range(initial_count, total_chapters, 3):
            window = chapters[start : start + 3]
            extract_text = "\n\n".join(
                " ".join(item.text for item in input_items)
                for _, input_items, _ in window
                if input_items
            )
            if extract_text:
                known_index = BookBibleService.get_known_names_index(bible)
                extract_started = time.perf_counter()
                delta = await self.llm_client.extract_book_bible_delta(
                    extract_text, known_index
                )
                logger.info(
                    "[TIMING] stage=book_bible_extract.end window=%d-%d elapsed_ms=%.1f "
                    "text_chars=%d new_chars=%d observations=%d",
                    start,
                    start + len(window) - 1,
                    _elapsed_ms(extract_started),
                    len(extract_text),
                    len(delta.new_characters),
                    len(delta.address_observations),
                )
                merge_started = time.perf_counter()
                bible, pending_ids = self.policy.apply_delta(
                    bible,
                    delta,
                    chapter_index=chapter_index_offset + start,
                    chapter_id=f"{chapter_id_prefix}-{start}",
                    chunk_id=f"{chapter_id_prefix}-{start}",
                )
                logger.info(
                    "[TIMING] stage=book_bible_merge.end window=%d-%d elapsed_ms=%.1f pending=%d",
                    start,
                    start + len(window) - 1,
                    _elapsed_ms(merge_started),
                    len(pending_ids),
                )
                await self._notify_bible_updated(on_bible_updated, bible)
            for offset, (chapter_id, input_items, _) in enumerate(window, start):
                if input_items:
                    await translate_chapter(offset, chapter_id, input_items)

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

