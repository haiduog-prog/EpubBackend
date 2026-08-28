import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.modules.book_bible.domain.address_term_policy import cjk_sequences
from app.schemas.book_bible import BookBible

logger = logging.getLogger("EpubBackend.TranslationCache")


class DirectTranslationCache:
    """Atomic local cache adapter for repeated direct-text translations."""

    CACHE_VERSION = 2
    TRANSLATION_POLICY_VERSION = "cjk-qa-v1"

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir or "storage/cache/direct-text")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _key(
        novel_id: str,
        text: str,
        chapter_index: Optional[int],
        chapter_id: Optional[str],
        provider: str,
        model: Optional[str],
    ) -> str:
        payload = json.dumps(
            {
                "novel_id": novel_id,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "chapter_index": chapter_index,
                "chapter_id": chapter_id or "",
                "provider": provider,
                "model": model or "",
                "translation_policy_version": DirectTranslationCache.TRANSLATION_POLICY_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(
        self,
        novel_id: str,
        text: str,
        chapter_index: Optional[int],
        chapter_id: Optional[str],
        provider: str,
        model: Optional[str],
        current_bible_revision: int,
    ) -> Optional[Dict[str, Any]]:
        key = self._key(novel_id, text, chapter_index, chapter_id, provider, model)
        path = self.cache_dir / f"{key}.json"
        with self._lock:
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("[CACHE] invalid key=%s error=%s", key[:12], exc)
                return None
            if (
                data.get("cache_version") != self.CACHE_VERSION
                or data.get("translation_policy_version") != self.TRANSLATION_POLICY_VERSION
                or data.get("qa_status") != "passed"
            ):
                return None
            cache_timestamp = data.get("created_at")
            if cache_timestamp is None:
                try:
                    cache_timestamp = path.stat().st_mtime
                except OSError:
                    return None
            try:
                is_expired = (
                    settings.cache_ttl_seconds > 0
                    and time.time() - float(cache_timestamp) > settings.cache_ttl_seconds
                )
            except (TypeError, ValueError):
                is_expired = True
            if is_expired:
                path.unlink(missing_ok=True)
                return None
            source_revision = data.get("source_bible_revision")
            result_revision = data.get("result_bible_revision")
            if current_bible_revision not in {source_revision, result_revision}:
                return None
            return data

    def put(
        self,
        novel_id: str,
        text: str,
        chapter_index: Optional[int],
        chapter_id: Optional[str],
        provider: str,
        model: Optional[str],
        source_bible_revision: int,
        translated_text: str,
        book_bible: BookBible,
    ) -> None:
        if cjk_sequences(translated_text):
            logger.warning("[CACHE] refusing to persist translation with CJK output")
            return
        key = self._key(novel_id, text, chapter_index, chapter_id, provider, model)
        path = self.cache_dir / f"{key}.json"
        temporary_path = path.with_suffix(".tmp")
        data = {
            "cache_version": self.CACHE_VERSION,
            "translation_policy_version": self.TRANSLATION_POLICY_VERSION,
            "qa_status": "passed",
            "created_at": time.time(),
            "source_bible_revision": source_bible_revision,
            "result_bible_revision": book_bible.bible_revision,
            "translated_text": translated_text,
            "book_bible": book_bible.model_dump(mode="json"),
        }
        with self._lock:
            temporary_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary_path.replace(path)
            max_entries = max(1, settings.cache_max_entries)
            cache_files = sorted(
                self.cache_dir.glob("*.json"),
                key=lambda item: item.stat().st_mtime,
            )
            for stale_path in cache_files[:-max_entries]:
                stale_path.unlink(missing_ok=True)


__all__ = ["DirectTranslationCache"]
