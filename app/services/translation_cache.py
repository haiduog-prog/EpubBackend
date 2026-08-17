import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.schemas.book_bible import BookBible

logger = logging.getLogger("EpubBackend.TranslationCache")


class DirectTranslationCache:
    """Local idempotency cache for repeated identical direct-text requests."""

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
                logger.info("[CACHE] miss key=%s reason=not_found", key[:12])
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result_revision = data.get("result_bible_revision")
                source_revision = data.get("source_bible_revision")
                if current_bible_revision not in {source_revision, result_revision}:
                    logger.info(
                        "[CACHE] miss key=%s reason=bible_changed current_revision=%s "
                        "source_revision=%s result_revision=%s",
                        key[:12],
                        current_bible_revision,
                        source_revision,
                        result_revision,
                    )
                    return None
                logger.info(
                    "[CACHE] hit key=%s bible_revision=%s",
                    key[:12],
                    current_bible_revision,
                )
                return data
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("[CACHE] invalid key=%s error=%s", key[:12], exc)
                return None

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
        key = self._key(novel_id, text, chapter_index, chapter_id, provider, model)
        path = self.cache_dir / f"{key}.json"
        data = {
            "source_bible_revision": source_bible_revision,
            "result_bible_revision": book_bible.bible_revision,
            "translated_text": translated_text,
            "book_bible": book_bible.model_dump(mode="json"),
        }
        temporary_path = path.with_suffix(".tmp")
        with self._lock:
            temporary_path.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary_path.replace(path)
        logger.info(
            "[CACHE] stored key=%s source_revision=%s result_revision=%s",
            key[:12],
            source_bible_revision,
            book_bible.bible_revision,
        )

