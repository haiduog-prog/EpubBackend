import json
import logging
from typing import List, Optional, Dict, Any, Set
from google import genai
from google.genai import types

from app.config import settings
from app.schemas.book_bible import BookBible, BookBibleDelta
from app.schemas.translation import HTMLInputItem, HTMLTranslationItem, HTMLTranslationOutput, QAReport
from app.prompts.templates import (
    PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA,
    PROMPT_2_TRANSLATE_CHUNK_SYSTEM,
    PROMPT_2_TRANSLATE_CHUNK_USER,
    PROMPT_3_TRANSLATE_HTML_SYSTEM,
    PROMPT_3_TRANSLATE_HTML_USER,
    PROMPT_4_QA_CHECK
)
from app.llm.base import BaseLLMClient

logger = logging.getLogger("EpubBackend.GeminiProvider")


def _clean_json_str(text: str) -> str:
    t = text.strip()
    if t.startswith("```json"):
        t = t[7:]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


class GeminiProvider(BaseLLMClient):
    """
    LLM Client triển khai cho Google Gemini API (google-genai SDK).
    Tự động ưu tiên gemini-flash-latest, gemini-flash-lite-latest và tự động Fallback.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        request_timeout_seconds: Optional[float] = None,
    ):
        raw_key = api_key or settings.gemini_api_key
        if not raw_key:
            raise ValueError("Chưa cấu hình Gemini API Key. Vui lòng tạo Key tại https://aistudio.google.com/app/apikey")
        
        self.api_key = raw_key.strip()
        self.default_model = model or settings.default_gemini_model
        if request_timeout_seconds is not None and request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than 0.")
        http_options = None
        if request_timeout_seconds is not None:
            http_options = types.HttpOptions(timeout=max(1, round(request_timeout_seconds * 1000)))
        self.client = genai.Client(api_key=self.api_key, http_options=http_options)
        self.working_model: Optional[str] = None
        self.failed_models: Set[str] = set()

    async def aclose(self) -> None:
        await self.client.aio.aclose()

    async def _call_with_fallback(
        self,
        contents: Any,
        config: types.GenerateContentConfig,
        preferred_model: Optional[str] = None
    ) -> Any:
        target_model = preferred_model or self.default_model
        legacy_map = {
            "auto": None,
            "gemini-1.5-flash": "gemini-flash-latest",
            "gemini-1.5-flash-latest": "gemini-flash-latest",
            "gemini-1.5-flash-002": "gemini-flash-latest",
            "gemini-1.5-flash-001": "gemini-flash-latest",
            "gemini-2.0-flash": "gemini-flash-latest",
            "gemini-2.0-flash-exp": "gemini-flash-latest",
            "gemini-1.5-pro": "gemini-pro-latest",
            "gemini-1.5-pro-latest": "gemini-pro-latest",
            "gemini-2.0-flash-lite": "gemini-flash-lite-latest"
        }
        if target_model in legacy_map:
            target_model = legacy_map[target_model]

        # Prepare candidates list
        candidates = []
        if self.working_model and self.working_model not in self.failed_models:
            candidates.append(self.working_model)
        if target_model and target_model not in candidates and target_model not in self.failed_models:
            candidates.append(target_model)
        for m in ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-pro", "gemini-flash-lite-latest", "gemini-pro-latest"]:
            if m not in candidates and m not in self.failed_models:
                candidates.append(m)

        for candidate_model in candidates:
            try:
                response = await self.client.aio.models.generate_content(
                    model=candidate_model,
                    contents=contents,
                    config=config,
                )
                self.working_model = candidate_model
                return response
            except Exception as e:
                err_str = str(e)
                logger.warning(f"Gemini model '{candidate_model}' call failed: {err_str}")
                self.failed_models.add(candidate_model)

                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                    logger.warning(f"Model {candidate_model} exhausted quota (429). Attempting fallback to next available model.")
                    continue
                elif "404" in err_str or "NOT_FOUND" in err_str or "is not found for API version" in err_str:
                    logger.warning(f"Model {candidate_model} not found or unsupported. Attempting fallback.")
                    continue
                else:
                    if len(candidates) > 1:
                        logger.warning(f"Model {candidate_model} error ({err_str}). Trying alternative candidate.")
                        continue
                    raise e

        raise ValueError("No working Gemini text generation model found for this API Key. Please verify your API Key at https://aistudio.google.com/app/apikey")

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

        response = await self._call_with_fallback(
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction="Bạn là biên tập viên phân tích tiểu thuyết. Hãy trích xuất Book Bible JSON hợp lệ theo đúng cấu trúc yêu cầu.",
                response_mime_type="application/json",
            ),
            preferred_model=model
        )
        json_text = _clean_json_str(response.text)
        return BookBibleDelta.model_validate_json(json_text)

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

        response = await self._call_with_fallback(
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_content
            ),
            preferred_model=model
        )
        return response.text.strip()

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

        response = await self._call_with_fallback(
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_content,
                response_mime_type="application/json",
            ),
            preferred_model=model
        )
        json_text = _clean_json_str(response.text)
        parsed_data = HTMLTranslationOutput.model_validate_json(json_text)
        raw_translations = parsed_data.translations

        out_map = {item.id: item.text_vi for item in raw_translations}
        aligned_translations: List[HTMLTranslationItem] = []

        for item in input_items:
            if item.id in out_map and out_map[item.id].strip():
                aligned_translations.append(HTMLTranslationItem(id=item.id, text_vi=out_map[item.id]))
            else:
                logger.warning(f"Gemini LLM skipped HTML ID '{item.id}'. Falling back to original text.")
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

        response = await self._call_with_fallback(
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction="Bạn là trợ lý QA kiểm tra nhất quán bản dịch.",
                response_mime_type="application/json",
            ),
            preferred_model=model
        )
        json_text = _clean_json_str(response.text)
        return QAReport.model_validate_json(json_text)
