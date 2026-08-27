import asyncio
import json
import logging
import re
import time
from typing import List, Optional, Dict, Any, Set, Iterable
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
from app.llm.errors import (
    GeminiModelUnavailableError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiServiceUnavailableError,
    StructuredOutputError,
)

logger = logging.getLogger("EpubBackend.GeminiProvider")

# Global Circuit Breaker for temporarily failing/unavailable models
# Maps model_name -> expiration_timestamp (float)
_GLOBAL_MODEL_COOLDOWNS: Dict[str, float] = {}
COOLDOWN_DURATION_SECONDS = 60.0  # Short local circuit-breaker window; 429 may override it.
FAST_CANDIDATE_TIMEOUT_SECONDS = 45.0  # Avoid treating normal model latency as an outage.


def _parse_model_pool(raw_pool: Any) -> List[str]:
    if isinstance(raw_pool, str):
        values: Iterable[Any] = raw_pool.split(",")
    elif isinstance(raw_pool, (list, tuple, set)):
        values = raw_pool
    else:
        values = ()
    result: List[str] = []
    for value in values:
        model_name = str(value).strip()
        if model_name and model_name not in result:
            result.append(model_name)
    return result


def _parse_retry_after(details: Any) -> Optional[float]:
    """Read google.rpc RetryInfo.retryDelay from an SDK error payload."""

    def walk(value: Any) -> Optional[float]:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"retrydelay", "retry_after", "retry_after_seconds"}:
                    parsed = _duration_seconds(child)
                    if parsed is not None:
                        return parsed
                parsed = walk(child)
                if parsed is not None:
                    return parsed
        elif isinstance(value, (list, tuple)):
            for child in value:
                parsed = walk(child)
                if parsed is not None:
                    return parsed
        return None

    def _duration_seconds(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*", str(value))
        if not match:
            return None
        amount = float(match.group(1))
        unit = match.group(2) or "s"
        return amount / 1000.0 if unit == "ms" else amount * {"s": 1.0, "m": 60.0, "h": 3600.0}[unit]

    return walk(details)


def _quota_scope(details: Any) -> Optional[str]:
    try:
        serialized = json.dumps(details, ensure_ascii=False, default=str)
    except Exception:
        serialized = str(details)
    matches = re.findall(r'"(?:quotaMetric|quotaId|quotaLocation|quotaLimit)"\s*:\s*"([^"]+)"', serialized)
    return "; ".join(dict.fromkeys(matches)) or None


def _is_project_wide_quota(error: GeminiRateLimitError) -> bool:
    scope = (error.quota_scope or "").upper()
    if not scope:
        return False
    # Per-day and per-project limits are shared by models. Falling back to a
    # second model for these limits only burns more quota and cannot succeed.
    return ("PERDAY" in scope or "PERPROJECT" in scope) and "PERMODEL" not in scope


def _normalise_provider_error(exc: BaseException, model: str) -> GeminiProviderError:
    """Convert google-genai errors into stable, typed application errors."""

    code = getattr(exc, "code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    status = getattr(exc, "status", None)
    status_text = str(status or "").upper()
    message = str(getattr(exc, "message", None) or exc)
    details = getattr(exc, "details", None)
    search_text = f"{status_text} {message} {details}".upper()
    retry_after = _parse_retry_after(details)
    quota_scope = _quota_scope(details)

    if code == 429 or re.search(r"\b429\b", search_text) or status_text in {"RESOURCE_EXHAUSTED", "TOO_MANY_REQUESTS"} or "RESOURCE_EXHAUSTED" in search_text or "TOO MANY REQUESTS" in search_text or "QUOTA" in search_text:
        return GeminiRateLimitError(
            message,
            code=code or 429,
            status=status_text or "RESOURCE_EXHAUSTED",
            model=model,
            retryable=True,
            retry_after_seconds=retry_after,
            quota_scope=quota_scope,
            details=details,
        )
    if code in {408, 500, 502, 503, 504} or re.search(r"\b(?:408|500|502|503|504)\b", search_text) or status_text in {"UNAVAILABLE", "DEADLINE_EXCEEDED", "INTERNAL", "BAD_GATEWAY", "GATEWAY_TIMEOUT"} or "HIGH DEMAND" in search_text:
        return GeminiServiceUnavailableError(
            message,
            code=code or 503,
            status=status_text or "UNAVAILABLE",
            model=model,
            retryable=True,
            retry_after_seconds=retry_after,
            details=details,
        )
    if code == 404 or re.search(r"\b404\b", search_text) or status_text in {"NOT_FOUND", "MODEL_NOT_FOUND"} or "IS NOT FOUND FOR API VERSION" in search_text:
        return GeminiModelUnavailableError(
            message,
            code=code or 404,
            status=status_text or "NOT_FOUND",
            model=model,
            retryable=False,
            details=details,
        )
    return GeminiProviderError(
        message,
        code=code,
        status=status_text or None,
        model=model,
        retryable=False,
        details=details,
    )


def _with_attempts(error: GeminiProviderError, attempts: List[str]) -> GeminiProviderError:
    return type(error)(
        str(error),
        code=error.code,
        status=error.status,
        model=error.model,
        retryable=error.retryable,
        retry_after_seconds=error.retry_after_seconds,
        quota_scope=error.quota_scope,
        attempts=attempts,
        details=error.details,
    )


def _clean_json_str(text: str) -> str:
    if not text:
        return "{}"
    t = text.strip()
    if "```json" in t:
        t = t.split("```json", 1)[1]
        if "```" in t:
            t = t.split("```", 1)[0]
    elif "```" in t:
        t = t.split("```", 1)[1]
        if "```" in t:
            t = t.split("```", 1)[0]
    t = t.strip()
    # Remove trailing commas before closing curly or square bracket
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return t


class GeminiProvider(BaseLLMClient):
    """
    LLM Client triển khai cho Google Gemini API (google-genai SDK).
    Tự động ưu tiên gemini-flash-latest, gemini-flash-lite-latest kèm Fast Timeout và Circuit Breaker.
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
        self.model_pool = _parse_model_pool(getattr(settings, "gemini_model_pool", ""))
        if not self.model_pool:
            self.model_pool = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"]
        self.candidate_timeout_seconds = max(
            1.0,
            float(getattr(settings, "gemini_candidate_timeout_seconds", FAST_CANDIDATE_TIMEOUT_SECONDS)),
        )
        self.cooldown_duration_seconds = max(
            1.0,
            float(getattr(settings, "gemini_cooldown_seconds", COOLDOWN_DURATION_SECONDS)),
        )
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
            "gemini-2.0-flash-lite": "gemini-flash-lite-latest",
        }
        if target_model:
            target_model = target_model.strip()
        if target_model in legacy_map:
            target_model = legacy_map[target_model]

        now = time.time()
        # Filter out models currently in active global cooldown
        def is_cooldown(m: str) -> bool:
            return now < _GLOBAL_MODEL_COOLDOWNS.get(m, 0.0)

        # The pool is configurable because model access differs by project/key.
        ordered_pool = list(getattr(self, "model_pool", []) or [])
        if not ordered_pool:
            ordered_pool = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"]

        candidates: List[str] = []
        cooldown_expiries: List[float] = []
        failed_models = getattr(self, "failed_models", set())
        # 1. If preferred model requested and not on cooldown/failed
        if target_model and target_model not in failed_models:
            if is_cooldown(target_model):
                cooldown_expiries.append(_GLOBAL_MODEL_COOLDOWNS[target_model])
            else:
                candidates.append(target_model)
        # 2. If working_model known and not on cooldown/failed
        working_model = getattr(self, "working_model", None)
        if working_model and working_model not in candidates and working_model not in failed_models:
            if is_cooldown(working_model):
                cooldown_expiries.append(_GLOBAL_MODEL_COOLDOWNS[working_model])
            else:
                candidates.append(working_model)
        # 3. Add other active candidates not on cooldown
        for m in ordered_pool:
            if m in candidates or m in failed_models:
                continue
            if is_cooldown(m):
                cooldown_expiries.append(_GLOBAL_MODEL_COOLDOWNS[m])
            else:
                candidates.append(m)

        # Never bypass a cooldown: doing so turns a provider quota event into
        # a burst of retries and makes every subsequent request fail as well.
        if not candidates:
            if cooldown_expiries:
                retry_after = max(0.0, min(cooldown_expiries) - time.time())
                raise GeminiServiceUnavailableError(
                    "Tất cả model Gemini đang tạm thời bị giới hạn hoặc không khả dụng.",
                    code=503,
                    status="UNAVAILABLE",
                    retryable=True,
                    retry_after_seconds=retry_after,
                    attempts=[],
                )
            raise GeminiModelUnavailableError(
                "Không có model Gemini khả dụng cho API key/project hiện tại.",
                code=404,
                status="NOT_FOUND",
                retryable=False,
                attempts=[],
            )

        attempts: List[str] = []
        last_error: Optional[GeminiProviderError] = None
        for candidate_model in candidates:
            try:
                # Fast timeout so hanging Google requests (503 spikes) don't block the user for minutes
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=candidate_model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=float(getattr(self, "candidate_timeout_seconds", FAST_CANDIDATE_TIMEOUT_SECONDS))
                )
                self.working_model = candidate_model
                # Successful call clears cooldown for this model
                _GLOBAL_MODEL_COOLDOWNS.pop(candidate_model, None)
                return response
            except asyncio.TimeoutError:
                timeout_seconds = float(getattr(self, "candidate_timeout_seconds", FAST_CANDIDATE_TIMEOUT_SECONDS))
                last_error = GeminiServiceUnavailableError(
                    f"Gemini model '{candidate_model}' không phản hồi trong {timeout_seconds:g} giây.",
                    code=504,
                    status="DEADLINE_EXCEEDED",
                    model=candidate_model,
                    retryable=True,
                )
                attempts.append(f"{candidate_model}: timeout")
                logger.warning(
                    "Gemini model '%s' phản hồi quá %ss. Tạm cooldown %ss và chuyển model dự phòng.",
                    candidate_model,
                    timeout_seconds,
                    float(getattr(self, "cooldown_duration_seconds", COOLDOWN_DURATION_SECONDS)),
                )
                _GLOBAL_MODEL_COOLDOWNS[candidate_model] = time.time() + float(
                    getattr(self, "cooldown_duration_seconds", COOLDOWN_DURATION_SECONDS)
                )
                continue
            except Exception as e:
                error = _normalise_provider_error(e, candidate_model)
                last_error = error
                attempts.append(f"{candidate_model}: {error.status or type(e).__name__}")
                logger.warning("Gemini model '%s' call failed (%s): %s", candidate_model, error.status or type(e).__name__, error)

                if isinstance(error, GeminiModelUnavailableError):
                    failed_models.add(candidate_model)
                    continue
                if error.retryable:
                    retry_after = error.retry_after_seconds
                    if retry_after is None:
                        retry_after = float(getattr(self, "cooldown_duration_seconds", COOLDOWN_DURATION_SECONDS))
                    if isinstance(error, GeminiRateLimitError) and _is_project_wide_quota(error):
                        # A per-day/per-project limit applies to every model;
                        # stop immediately and cooldown this request's pool.
                        expiry = time.time() + max(1.0, retry_after)
                        for pool_model in candidates:
                            _GLOBAL_MODEL_COOLDOWNS[pool_model] = expiry
                        raise _with_attempts(error, attempts)
                    _GLOBAL_MODEL_COOLDOWNS[candidate_model] = time.time() + max(1.0, retry_after)
                    continue
                raise _with_attempts(error, attempts)

        if last_error is not None:
            raise _with_attempts(last_error, attempts)
        raise GeminiModelUnavailableError(
            "Không có model Gemini khả dụng cho API key/project hiện tại.",
            code=404,
            status="NOT_FOUND",
            attempts=attempts,
        )

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

        try:
            response = await self._call_with_fallback(
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction="Bạn là biên tập viên phân tích tiểu thuyết. Hãy trích xuất Book Bible JSON hợp lệ theo đúng cấu trúc yêu cầu.",
                    response_mime_type="application/json",
                ),
                preferred_model=model
            )
            raw_text = getattr(response, "text", "") or "{}"
            json_text = _clean_json_str(raw_text)
            return BookBibleDelta.model_validate_json(json_text)
        except Exception as err:
            if isinstance(err, GeminiProviderError):
                raise
            logger.warning("Trích xuất BookBibleDelta gặp lỗi structured output (%s). Đánh dấu scan chưa hoàn tất.", err)
            raise StructuredOutputError(
                "BookBibleDelta không hợp lệ",
                operation="extract_book_bible_delta",
                details=str(err),
            ) from err

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

        try:
            response = await self._call_with_fallback(
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_content,
                    response_mime_type="application/json",
                ),
                preferred_model=model
            )
            raw_text = getattr(response, "text", "") or "{}"
            json_text = _clean_json_str(raw_text)
            parsed_data = HTMLTranslationOutput.model_validate_json(json_text)
        except Exception as err:
            if isinstance(err, GeminiProviderError):
                raise
            logger.warning("HTML translation JSON parse failed (%s), falling back to input text.", err)
            parsed_data = HTMLTranslationOutput(translations=[HTMLTranslationItem(id=item.id, text_vi=item.text) for item in input_items])

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

        try:
            response = await self._call_with_fallback(
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction="Bạn là trợ lý QA kiểm tra nhất quán bản dịch.",
                    response_mime_type="application/json",
                ),
                preferred_model=model
            )
            raw_text = getattr(response, "text", "") or "{}"
            json_text = _clean_json_str(raw_text)
            return QAReport.model_validate_json(json_text)
        except Exception as qa_err:
            if isinstance(qa_err, GeminiProviderError):
                raise
            logger.warning("QA check parse failed (%s), returning default consistent QAReport", qa_err)
            return QAReport(is_consistent=True, issues=[])
