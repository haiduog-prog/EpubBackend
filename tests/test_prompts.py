import pytest
from app.schemas.book_bible import BookBible
from app.prompts import (
    PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA,
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


def test_extraction_and_translation_prompts_keep_address_terms_in_vietnamese():
    extraction_text = PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA.format(
        known_names_index="known",
        source_text="source",
    )
    translation_text = PROMPT_2_TRANSLATE_CHUNK_SYSTEM.format(
        book_bible_json=BookBible().model_dump_json()
    )

    assert '"self"/"self_term" và "other"/"other_term" BẮT BUỘC là cách xưng hô tiếng Việt' in extraction_text
    assert '老师' in extraction_text and 'sư phụ' in extraction_text
    assert "không được chứa bất kỳ chữ Hán/CJK nào" in translation_text
