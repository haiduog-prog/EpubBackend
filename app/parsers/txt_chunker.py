from typing import List, Dict
from dataclasses import dataclass


@dataclass
class TextChunk:
    chunk_index: int
    text: str
    previous_context: str


class TXTChunker:
    """
    Chia chunk văn bản prose (.txt) theo ranh giới đoạn văn (~1500-3000 từ).
    Tự động trích xuất ~100-150 từ cuối của chunk trước làm PREVIOUS_CONTEXT.
    """

    def __init__(self, min_words: int = 1500, max_words: int = 3000, context_words: int = 150,
                 max_chars: int = 8000, context_chars: int = 1000):
        self.min_words = min_words
        self.max_words = max_words
        self.context_words = context_words
        if max_chars < 1 or max_words < 1:
            raise ValueError("Chunk limits must be positive.")
        self.max_chars = max_chars
        self.context_chars = context_chars

    def chunk_text(self, full_text: str) -> List[TextChunk]:
        paragraphs = []
        for paragraph in full_text.split("\n"):
            remainder = paragraph.strip()
            while remainder:
                end = min(len(remainder), self.max_chars)
                if end < len(remainder):
                    boundary = max(remainder.rfind(mark, 0, end)
                                   for mark in ("。", "！", "？", ". ", "! ", "? ", " "))
                    if boundary >= end // 2:
                        end = boundary + 1
                paragraphs.append(remainder[:end].strip())
                remainder = remainder[end:].strip()
        if not paragraphs:
            return []

        chunks: List[TextChunk] = []
        current_paras: List[str] = []
        current_word_count = 0
        current_char_count = 0
        chunk_idx = 0
        prev_context = ""

        for p in paragraphs:
            p_words = len(p.split())
            
            # Enforce the character budget even when CJK has no word separators.
            if current_paras and (
                current_word_count + p_words > self.max_words
                or current_char_count + 2 + len(p) > self.max_chars
            ):
                chunk_str = "\n\n".join(current_paras)
                chunks.append(TextChunk(
                    chunk_index=chunk_idx,
                    text=chunk_str,
                    previous_context=prev_context
                ))
                
                # Trích xuất 100-150 từ cuối làm context cho chunk tiếp theo
                all_chunk_words = chunk_str.split()
                prev_context = " ".join(all_chunk_words[-self.context_words:])[-self.context_chars:]
                
                # Reset
                chunk_idx += 1
                current_paras = [p]
                current_word_count = p_words
                current_char_count = len(p)
            else:
                current_char_count += len(p) + (2 if current_paras else 0)
                current_paras.append(p)
                current_word_count += p_words

        # Thêm chunk cuối cùng
        if current_paras:
            chunk_str = "\n\n".join(current_paras)
            chunks.append(TextChunk(
                chunk_index=chunk_idx,
                text=chunk_str,
                previous_context=prev_context
            ))

        return chunks
