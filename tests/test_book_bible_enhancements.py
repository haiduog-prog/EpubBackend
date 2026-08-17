import os
import asyncio
import tempfile
import pytest
from typing import Optional, List, Dict
from app.schemas.book_bible import (
    BookBible,
    BookBibleDelta,
    CharacterEntry,
    AddressTerm,
    PlaceEntry,
    TermEntry
)
from app.services import BookBibleService, TranslationPipelineService
from app.core.storage import StorageRepository
from app.llm.base import BaseLLMClient
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem


class MockLLMClient(BaseLLMClient):
    """
    Mock LLM client for testing Book Bible pipeline logic deterministically.
    """
    def __init__(self):
        self.extracted_texts: List[str] = []

    async def extract_book_bible_delta(self, text: str, known_names_index: str) -> BookBibleDelta:
        self.extracted_texts.append(text)
        delta = BookBibleDelta()

        if "MiddleEntity" in text:
            delta.new_characters.append(CharacterEntry(original_name="MiddleEntity", vi_name="Thực Thể Giữa"))
        if "EndEntity" in text:
            delta.new_characters.append(CharacterEntry(original_name="EndEntity", vi_name="Thực Thể Cuối"))
        if "DeepEntity" in text:
            delta.new_characters.append(CharacterEntry(original_name="DeepEntity", vi_name="Thực Thể Sâu"))

        return delta

    async def translate_prose_chunk(self, chunk_text: str, book_bible: BookBible, previous_context: str = "") -> str:
        if "FAIL_TRIGGER" in chunk_text:
            raise RuntimeError("Dịch lỗi giữa chừng!")
        return f"[Dịch]: {chunk_text}"

    async def translate_html_json(self, input_items: List[HTMLInputItem], book_bible: BookBible) -> List[HTMLTranslationItem]:
        return [HTMLTranslationItem(id=item.id, text=f"[Dịch]: {item.text}") for item in input_items]

    async def qa_check_chunk(self, original_text: str, translated_text: str, book_bible: BookBible) -> str:
        return translated_text


# Requirement 1: TXT 5-10 chunks, entity only in chunk 3/4 and final chunk
@pytest.mark.asyncio
async def test_txt_unextracted_buffer_and_final_sweep():
    mock_llm = MockLLMClient()
    pipeline = TranslationPipelineService(mock_llm)

    chunk_texts = [
        "Chunk 1: Khởi đầu bình thường.",
        "Chunk 2: Vẫn là khởi đầu.",
        "Chunk 3: Xuất hiện MiddleEntity ở đây.",
        "Chunk 4: Diễn biến bình thường.",
        "Chunk 5: Cuối cùng xuất hiện EndEntity tại đây."
    ]
    full_text = "\n\n".join(chunk_texts)

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False, suffix=".txt") as f_in:
        f_in.write(full_text)
        in_path = f_in.name

    out_path = in_path + ".out.txt"

    try:
        updated_bible = await pipeline.translate_txt_file(
            input_path=in_path,
            output_path=out_path
        )

        char_names = [c.original_name for c in updated_bible.characters]
        assert "MiddleEntity" in char_names, "Entity ở chunk 3 không được bỏ sót"
        assert "EndEntity" in char_names, "Entity ở chunk 5 (final sweep) không được bỏ sót"
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)
        if os.path.exists(out_path):
            os.remove(out_path)


# Requirement 2: EPUB entity beyond 2500 chars of a chapter
@pytest.mark.asyncio
async def test_epub_entity_beyond_2500_chars():
    mock_llm = MockLLMClient()
    pipeline = TranslationPipelineService(mock_llm)

    # Simulation text longer than 2500 chars with DeepEntity at char 2800
    long_text_prefix = "A" * 2700
    ch_text = f"{long_text_prefix} DeepEntity is found here."

    sample_bible = await pipeline.extract_initial_book_bible(ch_text)
    char_names = [c.original_name for c in sample_bible.characters]

    assert "DeepEntity" in char_names
    assert len(mock_llm.extracted_texts[0]) > 2500, "Extract text không được cắt ngắn ở 2500 chars"


# Requirement 3: Concurrent requests to same novel_id
@pytest.mark.asyncio
async def test_concurrent_requests_same_novel_id():
    import threading
    repo = StorageRepository.__new__(StorageRepository)
    repo._jobs = {}
    repo._bibles = {}
    repo._locks = {}
    repo._locks_guard = threading.Lock()
    repo.firebase_enabled = False
    repo.firestore_db = None


    novel_id = "novel-concurrent-test"

    delta1 = BookBibleDelta(new_characters=[CharacterEntry(original_name="Char1", vi_name="Nhân Vật 1")])
    delta2 = BookBibleDelta(new_characters=[CharacterEntry(original_name="Char2", vi_name="Nhân Vật 2")])

    async def request1():
        await asyncio.sleep(0.01)
        repo.merge_bible_delta(novel_id, delta1)

    async def request2():
        await asyncio.sleep(0.01)
        repo.merge_bible_delta(novel_id, delta2)

    await asyncio.gather(request1(), request2())

    final_bible = repo.get_bible(novel_id)
    assert final_bible is not None
    names = [c.original_name for c in final_bible.characters]
    assert "Char1" in names, "Char1 không được mất do concurrent overwrite"
    assert "Char2" in names, "Char2 không được mất do concurrent overwrite"


# Requirement 4: Canonical vi_name not overwritten by LLM
def test_canonical_vi_name_not_overwritten():
    bible = BookBible(
        characters=[CharacterEntry(original_name="Xiao Yan", vi_name="Tiêu Viêm")]
    )
    delta = BookBibleDelta(
        new_characters=[CharacterEntry(original_name="Xiao Yan", vi_name="Tiêu Diễm")]
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert merged.characters[0].vi_name == "Tiêu Viêm", "Canonical vi_name phải được giữ nguyên"


# Requirement 5: Text using alias or title only
def test_filter_bible_matches_alias_and_title():
    bible = BookBible(
        characters=[
            CharacterEntry(
                original_name="Yao Chen",
                vi_name="Dược Trần",
                aliases=["Dược Lão"],
                address_terms=[
                    AddressTerm(with_person="Tiêu Viêm", self="ta", other="Sư phụ", context="bái sư")
                ]
            )
        ]
    )

    # Chunk text contains neither 'Yao Chen' nor 'Dược Trần', only alias 'Dược Lão' / title 'Sư phụ'
    chunk_text = "Sư phụ nhìn đồ nhi và mỉm cười nói: Dược Lão sẽ giúp ngươi."
    filtered = BookBibleService.filter_bible_for_text(bible, chunk_text)

    assert len(filtered.characters) == 1
    assert filtered.characters[0].original_name == "Yao Chen"


# Requirement 6: Checkpoint persisted on process error
@pytest.mark.asyncio
async def test_bible_checkpoint_persisted_on_error():
    mock_llm = MockLLMClient()
    pipeline = TranslationPipelineService(mock_llm)

    chunk_texts = [
        "Chunk 1: Có MiddleEntity tại đây.",
        "Chunk 2: Vẫn bình thường.",
        "Chunk 3: Gặp FAIL_TRIGGER gây lỗi!"
    ]
    full_text = "\n\n".join(chunk_texts)

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False, suffix=".txt") as f_in:
        f_in.write(full_text)
        in_path = f_in.name

    out_path = in_path + ".out.txt"
    persisted_bibles = []

    def on_bible_updated(bible: BookBible):
        persisted_bibles.append(bible.model_copy(deep=True))

    try:
        with pytest.raises(RuntimeError, match="Dịch lỗi giữa chừng!"):
            await pipeline.translate_txt_file(
                input_path=in_path,
                output_path=out_path,
                on_bible_updated=on_bible_updated
            )

        assert len(persisted_bibles) > 0, "Book Bible checkpoint phải được lưu trước khi bị crash"
        last_persisted = persisted_bibles[-1]
        char_names = [c.original_name for c in last_persisted.characters]
        assert "MiddleEntity" in char_names, "Entity từ chunk 1 đã được persist thành công"
    finally:
        if os.path.exists(in_path):
            os.remove(in_path)
        if os.path.exists(out_path):
            os.remove(out_path)
