import json
import logging
from typing import List, Optional, Dict, Any, Type, TypeVar
from pydantic import BaseModel
import anthropic

from app.config import settings
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem, HTMLTranslationOutput, QAIssue, QAReport
from app.prompts.templates import (
    PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA,
    PROMPT_2_TRANSLATE_CHUNK_SYSTEM,
    PROMPT_2_TRANSLATE_CHUNK_USER,
    PROMPT_3_TRANSLATE_HTML_SYSTEM,
    PROMPT_3_TRANSLATE_HTML_USER,
    PROMPT_4_QA_CHECK,
    PROMPT_5_CORRECT_TERMINOLOGY,
)
from app.llm.base import BaseLLMClient

logger = logging.getLogger("EpubBackend.AnthropicProvider")
T = TypeVar("T", bound=BaseModel)


class AnthropicProvider(BaseLLMClient):
    """
    LLM Client triển khai cho Anthropic Claude API.
    Hỗ trợ Prompt Caching breakpoints và Structured Outputs.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or settings.anthropic_api_key
        self.default_model = model or settings.default_anthropic_model
        if not self.api_key:
            raise ValueError("Chưa cấu hình Anthropic API Key (sk-ant-...).")
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

    async def extract_book_bible_delta(
        self,
        source_text: str,
        known_names_index: str,
        model: Optional[str] = None
    ) -> BookBibleDelta:
        prompt = PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA.format(
            known_names_index=known_names_index,
            source_text=source_text
        )
        target_model = model or self.default_model
        return await self._call_structured(
            model=target_model,
            system="Bạn là biên tập viên trích xuất Book Bible dạng JSON hợp lệ.",
            messages=[{"role": "user", "content": prompt}],
            response_model=BookBibleDelta
        )

    async def translate_prose_chunk(
        self,
        chunk_text: str,
        book_bible: BookBible,
        previous_context: str = "",
        model: Optional[str] = None
    ) -> str:
        book_bible_json_str = book_bible.model_dump_json(indent=2)
        system_content = PROMPT_2_TRANSLATE_CHUNK_SYSTEM.format(
            book_bible_json=book_bible_json_str
        )
        user_content = PROMPT_2_TRANSLATE_CHUNK_USER.format(
            previous_context=previous_context if previous_context else "(Đoạn đầu truyện / Không có)",
            chunk_text=chunk_text
        )

        target_model = model or self.default_model
        system_blocks = [
            {
                "type": "text",
                "text": system_content,
                "cache_control": {"type": "ephemeral"} if settings.enable_prompt_caching else None
            }
        ]
        if not settings.enable_prompt_caching:
            system_blocks[0].pop("cache_control", None)

        response = await self.client.messages.create(
            model=target_model,
            max_tokens=4096,
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}]
        )

        result_text = ""
        for block in response.content:
            if block.type == "text":
                result_text += block.text

        return result_text.strip()

    async def correct_translation_terms(
        self,
        source_text: str,
        translated_text: str,
        book_bible: BookBible,
        issues: List[QAIssue],
        model: Optional[str] = None,
    ) -> str:
        prompt = PROMPT_5_CORRECT_TERMINOLOGY.format(
            book_bible_json=book_bible.model_dump_json(indent=2),
            issues_json=json.dumps([issue.model_dump() for issue in issues], ensure_ascii=False, indent=2),
            source_text=source_text,
            translated_text=translated_text,
        )
        target_model = model or self.default_model
        response = await self.client.messages.create(
            model=target_model,
            max_tokens=4096,
            system="Bạn là biên tập viên hiệu đính bản dịch tiếng Việt. Chỉ sửa đúng các issue được nêu.",
            messages=[{"role": "user", "content": prompt}],
        )
        result_text = ""
        for block in response.content:
            if block.type == "text":
                result_text += block.text
        return result_text.strip() or translated_text

    async def translate_html_json(
        self,
        input_items: List[HTMLInputItem],
        book_bible: BookBible,
        model: Optional[str] = None
    ) -> List[HTMLTranslationItem]:
        book_bible_json_str = book_bible.model_dump_json(indent=2)
        system_content = PROMPT_3_TRANSLATE_HTML_SYSTEM.format(
            book_bible_json=book_bible_json_str
        )
        input_json_str = json.dumps([item.model_dump() for item in input_items], ensure_ascii=False, indent=2)
        user_content = PROMPT_3_TRANSLATE_HTML_USER.format(
            input_json_array=input_json_str
        )

        target_model = model or self.default_model
        system_blocks = [
            {
                "type": "text",
                "text": system_content,
                "cache_control": {"type": "ephemeral"} if settings.enable_prompt_caching else None
            }
        ]
        if not settings.enable_prompt_caching:
            system_blocks[0].pop("cache_control", None)

        output_obj = await self._call_structured(
            model=target_model,
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
            response_model=HTMLTranslationOutput
        )

        # Cross-constraint validation (Section 3): Verify set of IDs and element count
        out_map = {item.id: item.text_vi for item in output_obj.translations}
        aligned_translations: List[HTMLTranslationItem] = []

        for item in input_items:
            if item.id in out_map and out_map[item.id].strip():
                aligned_translations.append(HTMLTranslationItem(id=item.id, text_vi=out_map[item.id]))
            else:
                logger.warning(f"Anthropic LLM skipped HTML ID '{item.id}'. Falling back to original text.")
                aligned_translations.append(HTMLTranslationItem(id=item.id, text_vi=item.text))

        return aligned_translations

    async def qa_check_chunk(
        self,
        translated_chunk: str,
        book_bible: BookBible,
        model: Optional[str] = None
    ) -> QAReport:
        book_bible_json_str = book_bible.model_dump_json(indent=2)
        prompt = PROMPT_4_QA_CHECK.format(
            book_bible_json=book_bible_json_str,
            translated_chunk=translated_chunk
        )

        target_model = model or self.default_model
        return await self._call_structured(
            model=target_model,
            system="Bạn là trợ lý QA kiểm tra nhất quán bản dịch.",
            messages=[{"role": "user", "content": prompt}],
            response_model=QAReport
        )

    async def _call_structured(
        self,
        model: str,
        system: Any,
        messages: List[Dict[str, Any]],
        response_model: Type[T]
    ) -> T:
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        extra_instruction = f"\n\nBẮT BUỘC TRẢ VỀ JSON HỢP LỆ THEO SCHEMA SAU:\n{schema_json}"
        
        last_msg = messages[-1]
        if isinstance(last_msg["content"], str):
            last_msg["content"] += extra_instruction

        response = await self.client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=messages
        )

        raw_text = ""
        for block in response.content:
            if block.type == "text":
                raw_text += block.text

        clean_json_text = raw_text.strip()
        if clean_json_text.startswith("```"):
            lines = clean_json_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_json_text = "\n".join(lines).strip()

        try:
            parsed_data = json.loads(clean_json_text)
            return response_model.model_validate(parsed_data)
        except Exception as e:
            logger.error(f"Failed to parse JSON response: {e}. Raw text: {raw_text}")
            start_idx = clean_json_text.find("{")
            end_idx = clean_json_text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                try:
                    substring_json = clean_json_text[start_idx:end_idx+1]
                    return response_model.model_validate(json.loads(substring_json))
                except Exception:
                    pass
            raise ValueError(f"Could not parse valid {response_model.__name__} from LLM response.")
