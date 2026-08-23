import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.dependencies import require_write_access
from app.config import settings
from app.infrastructure.cache.direct_translation import DirectTranslationCache
from app.infrastructure.jobs.limiter import limited_background_work
from app.infrastructure.storage.legacy_storage import LocalStorageProvider
from app.modules.translation.qa_api import check_qa_endpoint
from app.schemas.book_bible import BookBible


def test_local_storage_rejects_traversal_and_absolute_paths(tmp_path):
    provider = LocalStorageProvider(str(tmp_path / "storage"))

    assert provider.put_bytes("../escape.txt", b"blocked") is None
    assert provider.put_bytes("C:/escape.txt", b"blocked") is None
    assert provider.get_bytes("../../etc/passwd") is None
    assert provider.file_exists("../escape.txt") is False
    assert provider.delete_file("../escape.txt") is False
    assert not (tmp_path / "escape.txt").exists()


def test_write_access_fails_closed_outside_local_environments(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("BOOK_BIBLE_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "book_bible_write_token", "")

    with pytest.raises(HTTPException) as error:
        require_write_access(None)

    assert error.value.status_code == 503


def test_write_access_uses_constant_time_token_check(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BOOK_BIBLE_WRITE_TOKEN", "write-secret")

    require_write_access("write-secret")
    with pytest.raises(HTTPException) as error:
        require_write_access("wrong-secret")
    assert error.value.status_code == 403


def test_direct_translation_cache_expires_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cache_ttl_seconds", 60)
    cache = DirectTranslationCache(str(tmp_path))
    bible = BookBible(novel_id="cache-test")
    cache.put(
        novel_id="cache-test",
        text="hello",
        chapter_index=1,
        chapter_id="ch-1",
        provider="gemini",
        model="model-a",
        source_bible_revision=1,
        translated_text="xin chao",
        book_bible=bible,
    )

    cache_file = next(Path(tmp_path).glob("*.json"))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["created_at"] = 0
    cache_file.write_text(json.dumps(payload), encoding="utf-8")

    assert cache.get(
        novel_id="cache-test",
        text="hello",
        chapter_index=1,
        chapter_id="ch-1",
        provider="gemini",
        model="model-a",
        current_bible_revision=1,
    ) is None


def test_qa_endpoint_rejects_oversized_inputs(monkeypatch):
    monkeypatch.setattr(settings, "max_text_input_chars", 3)

    with pytest.raises(HTTPException) as error:
        asyncio.run(check_qa_endpoint("abcd", "ok"))

    assert error.value.status_code == 413


def test_background_limiter_rebinds_between_event_loops():
    @limited_background_work
    async def work():
        return "ok"

    assert asyncio.run(work()) == "ok"
    assert asyncio.run(work()) == "ok"


def test_ui_uses_backend_token_without_persisting_it():
    html = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")

    assert "backend-write-token" in html
    assert "X-Book-Bible-Client-Key" in html
    assert "localStorage.setItem('epub_api_key'" not in html
    assert "/api/v1/character-profiles/" not in html
    assert "${p.original_name} → ${p.vi_name}" not in html
    assert "${t.original_name} → ${t.vi_name}" not in html
    assert 'onclick="openNovelDetail(\'${' not in html
    assert 'onclick="deleteNovel(\'${' not in html
    assert "${ch.chapter_title}" not in html
    assert "safeMediaUrl" in html
