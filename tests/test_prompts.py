import pytest
from app.schemas.book_bible import BookBible
from app.prompts import (
    PROMPT_2_TRANSLATE_CHUNK_SYSTEM,
    PROMPT_2_TRANSLATE_CHUNK_USER
)


def test_prompt_cache_structure():
    bible = BookBible()
    system_text = PROMPT_2_TRANSLATE_CHUNK_SYSTEM.format(book_bible_json=bible.model_dump_json())
    user_text = PROMPT_2_TRANSLATE_CHUNK_USER.format(previous_context="bối cảnh trước", chunk_text="nội dung cần dịch")

    # Book bible block should be inside system text (at the TOP)
    assert "<book_bible>" in system_text
    assert "</book_bible>" in system_text
    
    # Dynamic context and chunk text should be inside user text (at the END)
    assert "<previous_context>" in user_text
    assert "<text_to_translate>" in user_text
