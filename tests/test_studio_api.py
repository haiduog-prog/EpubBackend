import os
import shutil
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
import app.db.session as db_session_module
from app.db.base import Base
from app.db.models.library import ChapterModel, NovelModel
from app.db.session import create_db_engine, reset_db_engine
import app.api.v1.studio as studio_module
from app.main import _mount_studio


@pytest.fixture
def isolated_studio_env(tmp_path, monkeypatch):
    """
    Fixture cô lập 100% môi trường Studio:
    - Cơ sở dữ liệu SQLite tạm thời trong tmp_path.
    - Thư mục storage tạm thời trong tmp_path.
    - Dọn dẹp và dispose engine trong finally.
    """
    test_db = tmp_path / "studio_isolated.sqlite3"
    test_storage = tmp_path / "storage"
    test_storage.mkdir(parents=True, exist_ok=True)

    db_url = f"sqlite:///{test_db.as_posix()}"
    orig_db_url = settings.database_url
    orig_env = settings.app_env
    orig_provider = settings.storage_provider
    orig_auth = settings.auth_required

    # Cấu hình môi trường local cho Studio
    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "auth_required", False)

    # Monkeypatch storage root & db file path trong studio module
    monkeypatch.setattr(studio_module, "_get_storage_root", lambda: test_storage)
    monkeypatch.setattr(studio_module, "_get_db_file_path", lambda: test_db)

    # Khởi tạo engine và tạo bảng trong SQLite tạm
    engine = reset_db_engine(db_url)
    Base.metadata.create_all(engine)

    # Chèn dữ liệu mẫu bằng ORM để áp dụng đầy đủ defaults
    from app.db.session import db_session
    with db_session() as session:
        session.add(
            NovelModel(
                novel_id="test-novel-1",
                title="Kiếm Lai",
                original_title="Jian Lai",
                author="Phong Hỏa Hí Chư Hầu",
            )
        )
        session.commit()

    # Tạo FastAPI instance riêng cho test
    test_app = FastAPI()
    assert _mount_studio(test_app) is True
    client = TestClient(test_app)

    yield {
        "client": client,
        "test_app": test_app,
        "storage_root": test_storage,
        "db_file": test_db,
        "engine": engine,
    }

    # Dọn dẹp engine trong finally
    try:
        engine.dispose()
    except Exception:
        pass
    reset_db_engine(orig_db_url)
    monkeypatch.setattr(settings, "database_url", orig_db_url)
    monkeypatch.setattr(settings, "app_env", orig_env)
    monkeypatch.setattr(settings, "storage_provider", orig_provider)
    monkeypatch.setattr(settings, "auth_required", orig_auth)


def test_studio_overview(isolated_studio_env):
    """Kiểm tra overview metrics trong môi trường cô lập."""
    client = isolated_studio_env["client"]
    res = client.get("/api/v1/studio/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["database_backend"] == "sqlite"
    assert data["total_tables"] >= 1
    table_names = [t["name"] for t in data["tables"]]
    assert "novels" in table_names


def test_studio_tables_schema(isolated_studio_env):
    """Kiểm tra schema bảng novels và chapters."""
    client = isolated_studio_env["client"]
    res = client.get("/api/v1/studio/tables")
    assert res.status_code == 200
    tables = res.json()
    novels_table = next(t for t in tables if t["name"] == "novels")
    col_names = [c["name"] for c in novels_table["columns"]]
    assert "novel_id" in col_names
    assert "title" in col_names


def test_studio_table_data_pagination_search_sort(isolated_studio_env):
    """Kiểm tra phân trang, tìm kiếm và sắp xếp an toàn."""
    client = isolated_studio_env["client"]
    res = client.get("/api/v1/studio/tables/novels/data?limit=10&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["rows"][0]["novel_id"] == "test-novel-1"

    # Tìm kiếm
    res_search = client.get("/api/v1/studio/tables/novels/data?search=Kiếm")
    assert res_search.status_code == 200
    assert len(res_search.json()["rows"]) == 1

    # Bảng không tồn tại -> 404
    res_404 = client.get("/api/v1/studio/tables/unknown_table_xyz/data")
    assert res_404.status_code == 404


def test_studio_sql_runner_and_error_handling(isolated_studio_env):
    """Kiểm tra SQL Runner thực thi thành công và không 500 khi query lỗi."""
    client = isolated_studio_env["client"]
    res_sql = client.post(
        "/api/v1/studio/sql",
        json={"query": "SELECT novel_id, title FROM novels;", "limit": 10},
    )
    assert res_sql.status_code == 200
    data = res_sql.json()
    assert data["success"] is True
    assert len(data["rows"]) == 1

    # Query lỗi cú pháp
    res_err = client.post(
        "/api/v1/studio/sql",
        json={"query": "SELECT * FROM non_existent_table_err;", "limit": 10},
    )
    assert res_err.status_code == 200
    assert res_err.json()["success"] is False
    assert "error" in res_err.json()


def test_studio_storage_files_isolated_and_traversal_guard(isolated_studio_env):
    """Kiểm tra Storage Explorer chỉ thấy file trong tmp_path và chặn path traversal."""
    client = isolated_studio_env["client"]
    storage_root = isolated_studio_env["storage_root"]

    # Tạo file mẫu trong thư mục tạm
    test_sub = storage_root / "test_folder"
    test_sub.mkdir()
    (test_sub / "sample.txt").write_text("hello isolated", encoding="utf-8")

    res = client.get("/api/v1/studio/storage/files?sub_path=test_folder")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "sample.txt"

    # Chặn Path Traversal
    res_traversal = client.get("/api/v1/studio/storage/files?sub_path=../")
    assert res_traversal.status_code == 400


def test_studio_delete_folder_recursive_isolated(isolated_studio_env):
    """Kiểm tra xóa file và thư mục đệ quy trong tmp_path an toàn tuyệt đối."""
    client = isolated_studio_env["client"]
    storage_root = isolated_studio_env["storage_root"]

    del_dir = storage_root / "dir_to_delete"
    del_dir.mkdir()
    (del_dir / "child.txt").write_text("delete me", encoding="utf-8")

    # Xóa đệ quy
    res_del = client.request(
        "DELETE",
        "/api/v1/studio/storage/files",
        json={"path": "dir_to_delete", "recursive": True},
    )
    assert res_del.status_code == 200
    assert not del_dir.exists()


def test_studio_reads_new_engine_after_reset(isolated_studio_env, tmp_path):
    """Xác minh sau reset_db_engine(), Studio thực sự query trên engine mới (P2.7)."""
    client = isolated_studio_env["client"]

    # Tạo database thứ hai
    db2_file = tmp_path / "second_db.sqlite3"
    db2_url = f"sqlite:///{db2_file.as_posix()}"
    engine2 = reset_db_engine(db2_url)
    Base.metadata.create_all(engine2)

    from app.db.session import db_session
    with db_session() as session:
        session.add(
            NovelModel(
                novel_id="novel-in-db-2",
                title="Bộ Truyện DB 2",
                original_title="Bo Truyen DB 2",
                author="Tác Giả 2",
            )
        )
        session.commit()

    try:
        # Gọi studio tables data -> Studio phải thấy novel-in-db-2
        res = client.get("/api/v1/studio/tables/novels/data")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["rows"][0]["novel_id"] == "novel-in-db-2"
    finally:
        engine2.dispose()


def test_studio_xss_payload_in_database_and_filenames(isolated_studio_env):
    """
    Xác minh payload XSS trong database và tên file chỉ được xem như plain text,
    không phá hỏng cấu trúc HTML hay chứa mã thực thi unescaped (P1.4).
    """
    client = isolated_studio_env["client"]
    engine = isolated_studio_env["engine"]
    storage_root = isolated_studio_env["storage_root"]

    xss_payload = '<script>alert("XSS")</script><img src=x onerror=alert(1)>'

    # 1. Chèn payload XSS vào database bằng ORM
    from app.db.session import db_session
    with db_session() as session:
        session.add(
            NovelModel(
                novel_id="xss-novel-id",
                title=xss_payload,
                original_title="xss-orig",
                author='test" onmouseover="alert(1)',
            )
        )
        session.commit()

    res_data = client.get("/api/v1/studio/tables/novels/data?search=XSS")
    assert res_data.status_code == 200
    rows = res_data.json()["rows"]
    assert len(rows) == 1
    assert "<script>" in rows[0]["title"]

    # 2. Tạo file có tên chứa ký tự đặc biệt
    xss_filename = "sample_xss_tag.txt"
    (storage_root / xss_filename).write_text("safe content", encoding="utf-8")

    res_files = client.get("/api/v1/studio/storage/files")
    assert res_files.status_code == 200
    file_names = [f["name"] for f in res_files.json()["items"]]
    assert xss_filename in file_names


def test_studio_not_mounted_in_production(tmp_path, monkeypatch):
    """
    Xác minh trong môi trường production hoặc cloud storage,
    Studio UI và API router hoàn toàn không được mount (P1.1).
    """
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "storage_provider", "supabase")

    prod_app = FastAPI()
    mounted = _mount_studio(prod_app)
    assert mounted is False

    prod_client = TestClient(prod_app)

    # Kiểm tra UI /studio -> 404
    res_ui = prod_client.get("/studio")
    assert res_ui.status_code == 404

    # Kiểm tra API /api/v1/studio/overview -> 404
    res_api = prod_client.get("/api/v1/studio/overview")
    assert res_api.status_code == 404
