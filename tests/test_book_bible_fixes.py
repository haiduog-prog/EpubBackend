import asyncio
from pathlib import Path

import pytest

from app.core.storage import StorageRepository
from app.parsers.txt_chunker import TextChunk
from app.schemas.book_bible import BookBible, BookBibleDelta, CharacterEntry
from app.services import BookBibleService, TranslationPipelineService
from app.llm.base import BaseLLMClient
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem, QAReport


class OrderedLLM(BaseLLMClient):
    def __init__(self):
        self.events = []

    async def extract_book_bible_delta(self, source_text, known_names_index, model=None):
        self.events.append(("extract", source_text))
        delta = BookBibleDelta()
        if "LateEntity" in source_text:
            delta.new_characters.append(
                CharacterEntry(original_name="LateEntity", vi_name="Thực Thể Muộn")
            )
        return delta

    async def translate_prose_chunk(self, chunk_text, book_bible, previous_context="", model=None):
        has_entity = any(c.original_name == "LateEntity" for c in book_bible.characters)
        self.events.append(("translate", chunk_text, has_entity))
        return chunk_text

    async def translate_html_json(self, input_items, book_bible, model=None):
        return [HTMLTranslationItem(id=item.id, text_vi=item.text) for item in input_items]

    async def qa_check_chunk(self, translated_chunk, book_bible, model=None):
        return QAReport()


@pytest.mark.asyncio
async def test_pipeline_extracts_window_before_translating_it(monkeypatch, tmp_path):
    chunks = [
        TextChunk(i, text, "")
        for i, text in enumerate(
            ["first", "second", "third LateEntity", "fourth", "fifth"]
        )
    ]
    monkeypatch.setattr(
        "app.services.pipeline_service.TXTChunker.chunk_text",
        lambda self, full_text: chunks,
    )

    client = OrderedLLM()
    input_path = tmp_path / "input.txt"
    input_path.write_text("placeholder", encoding="utf-8")
    output = tmp_path / "out.txt"
    await TranslationPipelineService(client).translate_txt_file(
        str(input_path),
        str(output),
    )

    late_extract = next(i for i, event in enumerate(client.events) if event[0] == "extract" and "LateEntity" in event[1])
    late_translate = next(i for i, event in enumerate(client.events) if event[0] == "translate" and "third LateEntity" in event[1])
    assert late_extract < late_translate
    assert client.events[late_translate][2] is True


def test_save_bible_merges_full_snapshots_without_losing_entities():
    repo = StorageRepository.__new__(StorageRepository)
    repo._jobs = {}
    repo._bibles = {}
    repo.firebase_enabled = False
    repo.firestore_db = None

    repo.save_bible(
        "novel-1",
        BookBible(
            novel_id="novel-1",
            characters=[CharacterEntry(original_name="A", vi_name="A")],
        ),
    )
    repo.save_bible(
        "novel-1",
        BookBible(
            novel_id="novel-1",
            characters=[CharacterEntry(original_name="B", vi_name="B")],
        ),
    )

    names = {c.original_name for c in repo.get_bible("novel-1").characters}
    assert names == {"A", "B"}


def test_alias_name_is_canonicalized_and_listed():
    bible = BookBible(
        characters=[
            CharacterEntry(
                original_name="Canonical",
                vi_name="Tên Chuẩn",
                aliases=["Biệt Danh"],
            )
        ]
    )
    delta = BookBibleDelta(
        new_characters=[
            CharacterEntry(original_name="Biệt Danh", vi_name="Tên Khác")
        ]
    )

    merged = BookBibleService.merge_delta(bible, delta)
    assert len(merged.characters) == 1
    assert merged.characters[0].vi_name == "Tên Chuẩn"
    assert "Biệt Danh" in merged.characters[0].aliases
    assert "Biệt Danh -> Tên Chuẩn" in BookBibleService.get_known_names_index(merged)
