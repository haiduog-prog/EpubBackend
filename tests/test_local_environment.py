import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
import app.db.session as db_session_module
from app.db.base import Base
from app.db.models.library import ChapterModel, NovelModel
from app.db.session import PROJECT_ROOT, create_db_engine, get_engine_url, reset_db_engine
from app.infrastructure.storage.legacy_storage import (
    LocalStorageProvider,
    R2StorageProvider,
    StorageRepository,
    SupabaseStorageProvider,
    storage_repo,
)
import app.main as main_module
from app.main import app
import app.modules.library.application.epub_build_worker as epub_build_worker
from app.modules.library.application.facade import library_service
from app.schemas.library import NovelMetadata


def test_sqlite_migration_full_cycle(tmp_path, monkeypatch):
    """Xác minh chu kỳ migration base -> head -> base -> head trên SQLite tạm."""
    test_db = tmp_path / "migration_cycle.sqlite3"
    db_url = f"sqlite:///{test_db.as_posix()}"

    alembic_cfg = Config("alembic.ini")
    orig_db_url = settings.database_url
    try:
        settings.database_url = db_url
        monkeypatch.setenv("DATABASE_URL", db_url)

        # 1. Upgrade to head
        command.upgrade(alembic_cfg, "head")

        # Verify key tables exist
        engine = create_db_engine(db_url)
        with engine.connect() as conn:
            tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table';")).scalars().all()
            assert "novels" in tables
            assert "chapters" in tables
            assert "epub_build_jobs" in tables
            assert "translation_jobs" in tables
            assert "book_bibles" in tables

        # 2. Downgrade to base
        command.downgrade(alembic_cfg, "base")

        # 3. Upgrade back to head
        command.upgrade(alembic_cfg, "head")
    finally:
        settings.database_url = orig_db_url


def test_sqlite_connection_pragmas_and_cascade(tmp_path):
    """Xác minh SQLite connection event bật foreign_keys, WAL mode, busy_timeout và CASCADE delete."""
    test_db = tmp_path / "test_pragmas.sqlite3"
    db_url = f"sqlite:///{test_db.as_posix()}"

    engine = create_db_engine(db_url)

    with engine.connect() as conn:
        # Check foreign_keys
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert fk == 1, "PRAGMA foreign_keys must be ON"

        # Check journal_mode
        jm = conn.execute(text("PRAGMA journal_mode;")).scalar()
        assert str(jm).lower() == "wal", "PRAGMA journal_mode must be WAL"

        # Check busy_timeout
        bt = conn.execute(text("PRAGMA busy_timeout;")).scalar()
        assert bt == 5000, "PRAGMA busy_timeout must be 5000ms"

    # Create tables and test CASCADE delete
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        novel = NovelModel(
            novel_id="test-cascade-novel",
            title="Cascade Test",
            original_title="",
            author="Author",
            genre=[],
            description="Test description",
        )
        session.add(novel)
        session.flush()

        ch = ChapterModel(
            novel_id="test-cascade-novel",
            chapter_index=1,
            chapter_id="ch-1",
            chapter_title="Chapter 1",
            status="completed",
        )
        session.add(ch)
        session.commit()

        # Delete novel
        session.delete(novel)
        session.commit()

        # Verify chapter was deleted by cascade
        remaining_ch = session.execute(
            select(ChapterModel).where(ChapterModel.novel_id == "test-cascade-novel")
        ).scalar_one_or_none()
        assert remaining_ch is None, "Chapter must be cascaded on novel deletion"


def test_sqlite_url_resolution():
    """Xác minh relative path được resolve từ project root; memory DB và file URI giữ nguyên."""
    # 1. Relative SQLite path
    resolved = get_engine_url("sqlite:///./data/local_db.sqlite3")
    expected_path = (PROJECT_ROOT / "data" / "local_db.sqlite3").as_posix()
    assert expected_path in resolved

    # 2. In-memory
    mem = get_engine_url("sqlite:///:memory:")
    assert mem == "sqlite:///:memory:"

    # 3. URI format with query
    uri = get_engine_url("sqlite:///file:test_mem_db?mode=memory&cache=shared&uri=true")
    assert "file:test_mem_db" in uri
    assert "mode=memory" in uri


def test_sqlite_read_only_uri_remains_connectable(tmp_path):
    """WAL setup must not reject a valid read-only SQLite URI."""
    test_db = tmp_path / "read_only.sqlite3"
    raw_conn = sqlite3.connect(test_db)
    try:
        raw_conn.execute("CREATE TABLE sample (value INTEGER)")
        raw_conn.execute("INSERT INTO sample VALUES (1)")
        raw_conn.commit()
    finally:
        raw_conn.close()

    db_url = f"sqlite:///file:{test_db.as_posix()}?mode=ro&uri=true"
    engine = create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT value FROM sample")).scalar_one() == 1
    finally:
        engine.dispose()


def test_storage_provider_local_precedence(monkeypatch):
    """Xác minh STORAGE_PROVIDER=local luôn trả về local_provider kể cả khi cloud active."""
    repo = StorageRepository()

    # Mock cloud providers as active
    monkeypatch.setattr(SupabaseStorageProvider, "is_active", property(lambda self: True))
    monkeypatch.setattr(R2StorageProvider, "is_active", property(lambda self: True))
    monkeypatch.setattr(settings, "storage_provider", "local")

    assert repo.active_provider == repo.local_provider
    assert repo.active_provider_name == "local"

    # Verify is_blob_active preserves cloud-only semantics
    assert repo.is_blob_active is True  # because mocked cloud active


def test_local_storage_resolve_path_and_traversal(tmp_path, monkeypatch):
    """Xác minh resolve_local_path và cơ chế chặn path traversal."""
    monkeypatch.chdir(tmp_path)
    storage_root = tmp_path / "storage"
    provider = LocalStorageProvider(base_dir=str(storage_root))

    # Write a legitimate file
    provider.put_bytes("novels/novel-1/test.txt", b"Hello World")

    # Resolve safe existing file
    resolved = provider.resolve_local_path("novels/novel-1/test.txt")
    assert resolved is not None
    assert resolved.is_file()
    assert resolved.read_bytes() == b"Hello World"

    # Non-existent file returns None
    assert provider.resolve_local_path("novels/novel-1/missing.txt") is None

    # Path traversal attempts
    assert provider.resolve_local_path("../outside.txt") is None
    assert provider.resolve_local_path("/etc/passwd") is None
    assert provider.resolve_local_path("C:/Windows/system32") is None

    # Public path resolution must never use the legacy data/ fallback.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "local_db.sqlite3").write_bytes(b"private database")
    assert provider.resolve_local_path("data/local_db.sqlite3") is None


def test_static_storage_security(tmp_path, monkeypatch):
    """Xác minh /storage không bao giờ trả về file database hay file ngoài storage/."""
    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "app_env", "local")
    storage_root = tmp_path / "storage"
    test_app = FastAPI()
    assert main_module._mount_local_storage(test_app, storage_root) is True
    client = TestClient(test_app)

    # 1. Attempt to access database through /storage
    res_db = client.get("/storage/local_db.sqlite3")
    assert res_db.status_code == 404

    # 2. Attempt path traversal to reach data directory
    res_traverse = client.get("/storage/../data/local_db.sqlite3")
    assert res_traverse.status_code in (400, 404)

    # 3. Create a test file in storage and ensure it is served correctly
    test_cover = storage_root / "test_static_cover.txt"
    test_cover.write_text("static cover content", encoding="utf-8")
    res_cover = client.get("/storage/test_static_cover.txt")
    assert res_cover.status_code == 200
    assert res_cover.text == "static cover content"


def test_static_storage_is_not_mounted_in_production(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "app_env", "production")
    test_app = FastAPI()

    assert main_module._mount_local_storage(test_app, tmp_path / "storage") is False
    assert all(getattr(route, "path", None) != "/storage" for route in test_app.routes)


def test_epub_worker_reads_the_current_session_engine(monkeypatch):
    current_engine = MagicMock()
    monkeypatch.setattr(db_session_module, "engine", current_engine)

    assert epub_build_worker._get_current_engine() is current_engine


def test_epub_export_local_cache_hit(monkeypatch, tmp_path):
    """Xác minh export EPUB trả cache ngay bằng FileResponse khi local file đã tồn tại."""
    client = TestClient(app)

    # Setup mock novel in service
    mock_novel = NovelMetadata(
        novel_id="test-cache-hit",
        title="Test Cache Hit Novel",
        original_title="",
        author="Author",
        genre=[],
        description="",
        current_epub_key="novels/test-cache-hit/full.epub",
    )
    monkeypatch.setattr(library_service, "get_novel", lambda nid: mock_novel if nid == "test-cache-hit" else None)
    monkeypatch.setattr(settings, "storage_provider", "local")

    # Create dummy local file
    dummy_file = tmp_path / "full.epub"
    dummy_file.write_bytes(b"PK\x03\x04dummy_epub_content")
    monkeypatch.setattr(storage_repo, "resolve_local_path", lambda key: dummy_file if key == mock_novel.current_epub_key else None)

    # Spy build_and_publish_epub to ensure it's NOT called
    build_called = False
    def _mock_build(*args, **kwargs):
        nonlocal build_called
        build_called = True
        return {"output_path": str(dummy_file), "built_revision": 1}
    monkeypatch.setattr(library_service.export_service, "build_and_publish_epub", _mock_build)

    response = client.get("/api/v1/library/novels/test-cache-hit/export/epub")
    assert response.status_code == 200
    assert response.content == b"PK\x03\x04dummy_epub_content"
    assert build_called is False, "build_and_publish_epub must NOT be called on local cache hit"
    assert "attachment" in response.headers.get("content-disposition", "")
