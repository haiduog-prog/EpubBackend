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

    def __init__(self, min_words: int = 1500, max_words: int = 3000, context_words: int = 150):
        self.min_words = min_words
        self.max_words = max_words
        self.context_words = context_words

    def chunk_text(self, full_text: str) -> List[TextChunk]:
        paragraphs = [p.strip() for p in full_text.split("\n") if p.strip()]
        if not paragraphs:
            return []

        chunks: List[TextChunk] = []
        current_paras: List[str] = []
        current_word_count = 0
        chunk_idx = 0
        prev_context = ""

        for p in paragraphs:
            p_words = len(p.split())
            
            # Nếu thêm đoạn này mà vượt quá max_words và đã đạt min_words -> chốt chunk hiện tại
            if current_word_count >= self.min_words and (current_word_count + p_words > self.max_words):
                chunk_str = "\n\n".join(current_paras)
                chunks.append(TextChunk(
                    chunk_index=chunk_idx,
                    text=chunk_str,
                    previous_context=prev_context
                ))
                
                # Trích xuất 100-150 từ cuối làm context cho chunk tiếp theo
                all_chunk_words = chunk_str.split()
                prev_context = " ".join(all_chunk_words[-self.context_words:])
                
                # Reset
                chunk_idx += 1
                current_paras = [p]
                current_word_count = p_words
            else:
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
