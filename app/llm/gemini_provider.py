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


class GeminiProvider(BaseLLMClient):
    """
    LLM Client triển khai cho Google Gemini API (google-genai SDK).
    Tự động ưu tiên gemini-flash-latest, gemini-flash-lite-latest và tự động Fallback.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        raw_key = api_key or settings.gemini_api_key
        if not raw_key:
            raise ValueError("Chưa cấu hình Gemini API Key. Vui lòng tạo Key tại https://aistudio.google.com/app/apikey")
        
        self.api_key = raw_key.strip()
        self.default_model = model or settings.default_gemini_model
        self.client = genai.Client(api_key=self.api_key)
        self.working_model: Optional[str] = None
        self.failed_models: Set[str] = set()

    def _call_with_fallback(
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

        candidate_models = []
        if target_model:
            candidate_models.append(target_model)

        # 2026 Official Active Gemini Models (gemini-flash-latest, gemini-flash-lite-latest, gemini-pro-latest, gemini-3.5-flash)
        default_candidates = [
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            "gemini-pro-latest",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash"
        ]
        for m in default_candidates:
            if m not in candidate_models:
                candidate_models.append(m)

        def is_text_generation_model(name: str) -> bool:
            lower = name.lower()
            if any(bad in lower for bad in ["-tts", "tts", "embedding", "imagen", "veo", "audio"]):
                return False
            return True

        candidate_models = [
            m for m in candidate_models
            if is_text_generation_model(m) and m not in self.failed_models
        ]

        if self.working_model and self.working_model not in self.failed_models:
            if self.working_model in candidate_models:
                candidate_models.remove(self.working_model)
            candidate_models.insert(0, self.working_model)

        if target_model and target_model not in self.failed_models:
            if target_model in candidate_models:
                candidate_models.remove(target_model)
            candidate_models.insert(0, target_model)

        last_exception = None
        for m in candidate_models:
            try:
                logger.info(f"Calling Gemini generate_content with model: {m}")
                res = self.client.models.generate_content(
                    model=m,
                    contents=contents,
                    config=config
                )
                self.working_model = m
                return res
            except Exception as e:
                err_msg = str(e)
                if any(k in err_msg for k in ["404", "NOT_FOUND", "not found", "no longer available"]):
                    self.failed_models.add(m)
                    logger.debug(f"Gemini model '{m}' returned 404/not available. Blacklisting for session.")
                    last_exception = e
                    continue
                elif any(k in err_msg for k in ["429", "RESOURCE_EXHAUSTED", "quota"]):
                    logger.warning(f"Gemini model '{m}' hit rate/quota limit (429). Trying next candidate...")
                    last_exception = e
                    continue
                elif any(k in err_msg for k in ["400", "INVALID_ARGUMENT", "modalities"]):
                    self.failed_models.add(m)
                    logger.warning(f"Gemini model '{m}' rejected modalities (400). Blacklisting for session.")
                    last_exception = e
                    continue
                raise e

        if last_exception:
            err_str = str(last_exception)
            if any(k in err_str for k in ["429", "RESOURCE_EXHAUSTED", "quota"]):
                raise ValueError(
                    "API Key Google Gemini của bạn đã hết hạn ngạch truy cập miễn phí trong ngày (429 Quota Exceeded). Vui lòng tạo API Key mới tại https://aistudio.google.com/app/apikey hoặc chuyển Provider sang Claude API (Anthropic)."
                )
            if any(k in err_str for k in ["404", "NOT_FOUND", "not found"]):
                raise ValueError(
                    f"API Key Google Gemini không kết nối được tới dịch vụ Gemini (Lỗi 404 NOT_FOUND). Vui lòng kiểm tra lại API Key từ https://aistudio.google.com/app/apikey."
                )
            raise last_exception

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

        response = self._call_with_fallback(
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction="Bạn là biên tập viên phân tích tiểu thuyết trích xuất Book Bible JSON hợp lệ.",
                response_mime_type="application/json",
                response_schema=BookBibleDelta
            ),
            preferred_model=model
        )
        if response.parsed:
            return response.parsed
        return BookBibleDelta.model_validate_json(response.text)

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

        response = self._call_with_fallback(
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

        response = self._call_with_fallback(
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_content,
                response_mime_type="application/json",
                response_schema=HTMLTranslationOutput
            ),
            preferred_model=model
        )
        if response.parsed:
            raw_translations = response.parsed.translations
        else:
            parsed_data = HTMLTranslationOutput.model_validate_json(response.text)
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

        response = self._call_with_fallback(
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction="Bạn là trợ lý QA kiểm tra nhất quán bản dịch.",
                response_mime_type="application/json",
                response_schema=QAReport
            ),
            preferred_model=model
        )
        if response.parsed:
            return response.parsed
        return QAReport.model_validate_json(response.text)
