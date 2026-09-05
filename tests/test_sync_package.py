import json
import os
import shutil
import zipfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.auth import AuthUser, get_current_user
from app.config import settings
from app.main import app
from app.modules.sync.sync_package_service import SyncPackageService


@pytest.fixture
def sync_client():
    """Test client with mocked auth for sync endpoints."""
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        user_id="test-user",
        claims={"sub": "test-user", "email": "test@example.com"},
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_current_user, None)


def test_get_sync_estimate_live():
    """Verify get_sync_estimate returns a valid structure from the current database."""
    est = SyncPackageService.get_sync_estimate()
    assert isinstance(est, dict)
    assert "database_type" in est
    assert "total_novels" in est
    assert "total_chapters" in est
    assert "completed_chapters" in est
    assert "database_size_mb" in est
    assert "storage_files_count" in est
    assert "storage_size_mb" in est
    assert "estimated_zip_size_mb" in est
    assert est["total_novels"] >= 0
    assert est["total_chapters"] >= 0


def test_sync_estimate_isolated(tmp_path):
    """Verify estimate calculation on an isolated project directory."""
    isolated_root = tmp_path / "proj"
    data_dir = isolated_root / "data"
    storage_novels = isolated_root / "storage" / "novels" / "test-novel"
    data_dir.mkdir(parents=True)
    storage_novels.mkdir(parents=True)

    # Create dummy database and storage files
    dummy_db = data_dir / "local_db.sqlite3"
    dummy_db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 1024)

    dummy_ch = storage_novels / "ch_001.txt"
    dummy_ch.write_text("Test chapter content", encoding="utf-8")

    est = SyncPackageService.get_sync_estimate(project_root=isolated_root)
    assert est["storage_files_count"] == 1
    assert est["database_size_mb"] >= 0


def test_export_sync_package_isolated(tmp_path):
    """Verify export creates a valid zip with manifest, data, and storage."""
    isolated_root = tmp_path / "proj_export"
    data_dir = isolated_root / "data"
    storage_novels = isolated_root / "storage" / "novels" / "novel-slug"
    data_dir.mkdir(parents=True)
    storage_novels.mkdir(parents=True)

    dummy_db = data_dir / "local_db.sqlite3"
    dummy_db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512)

    dummy_file = storage_novels / "ch_001.txt"
    dummy_file.write_text("Hello World chapter 1", encoding="utf-8")

    zip_path = SyncPackageService.export_sync_package(
        include_db=True,
        include_storage=True,
        project_root=isolated_root,
    )

    try:
        assert os.path.exists(zip_path)
        assert zipfile.is_zipfile(zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            assert "sync_manifest.json" in namelist
            assert "data/local_db.sqlite3" in namelist
            assert "storage/novels/novel-slug/ch_001.txt" in namelist

            manifest_data = json.loads(zf.read("sync_manifest.json").decode("utf-8"))
            assert manifest_data.get("format_version") == 1
            assert manifest_data.get("include_db") is True
            assert manifest_data.get("include_storage") is True
    finally:
        if os.path.exists(zip_path):
            os.unlink(zip_path)
        parent = os.path.dirname(zip_path)
        if os.path.exists(parent) and "epub_sync_export_" in os.path.basename(parent):
            shutil.rmtree(parent, ignore_errors=True)


def test_import_sync_package_isolated(tmp_path):
    """Verify import unpacks data and storage into the target project root."""
    # 1. Create a mock sync package zip
    zip_path = tmp_path / "test_package.zip"
    with zipfile.ZipFile(str(zip_path), "w") as zf:
        manifest = {"format_version": 1, "app_env": "test"}
        zf.writestr("sync_manifest.json", json.dumps(manifest))
        zf.writestr("data/local_db.sqlite3", b"SQLite mock content")
        zf.writestr("storage/novels/my-novel/translated/ch_001.txt", "Noi dung chuong 1")

    # 2. Import into an isolated target root
    target_root = tmp_path / "target_proj"
    result = SyncPackageService.import_sync_package(
        str(zip_path),
        restore_to_postgres_if_active=False,
        project_root=target_root,
    )

    assert result["status"] == "success"
    assert result["database_restored"] is True
    assert result["sqlite_db_restored"] is True
    assert result["extracted_files_count"] >= 2

    # Verify extracted files on disk
    restored_db = target_root / "data" / "local_db.sqlite3"
    assert restored_db.exists()
    assert restored_db.read_bytes() == b"SQLite mock content"

    restored_ch = target_root / "storage" / "novels" / "my-novel" / "translated" / "ch_001.txt"
    assert restored_ch.exists()
    assert restored_ch.read_text(encoding="utf-8") == "Noi dung chuong 1"


def test_import_sync_package_zip_slip_prevention(tmp_path):
    """Verify zip slip attacks (directory traversal) are caught and rejected."""
    bad_zip = tmp_path / "malicious.zip"
    with zipfile.ZipFile(str(bad_zip), "w") as zf:
        zf.writestr("storage/novels/should-not-be-written.txt", "must not be applied")
        zf.writestr("../../etc/passwd", "evil content")

    target_root = tmp_path / "target_proj"
    with pytest.raises(ValueError, match="không an toàn"):
        SyncPackageService.import_sync_package(
            str(bad_zip),
            restore_to_postgres_if_active=False,
            project_root=target_root,
        )
    assert not (target_root / "storage" / "novels" / "should-not-be-written.txt").exists()


def test_import_sync_package_rejects_windows_path_traversal(tmp_path):
    bad_zip = tmp_path / "malicious-windows.zip"
    with zipfile.ZipFile(str(bad_zip), "w") as zf:
        zf.writestr(r"storage\novels\..\..\escape.txt", "evil content")

    with pytest.raises(ValueError, match="không an toàn"):
        SyncPackageService.import_sync_package(
            str(bad_zip),
            restore_to_postgres_if_active=False,
            project_root=tmp_path / "target_proj",
        )


def test_import_sync_package_enforces_archive_limits(tmp_path, monkeypatch):
    package = tmp_path / "oversized.zip"
    with zipfile.ZipFile(str(package), "w") as zf:
        zf.writestr("storage/novels/chapter.txt", "too large")

    monkeypatch.setattr(settings, "max_sync_package_uncompressed_bytes", 1)
    with pytest.raises(ValueError, match="Tổng dung lượng"):
        SyncPackageService.import_sync_package(
            str(package),
            restore_to_postgres_if_active=False,
            project_root=tmp_path / "target_proj",
        )


def test_api_estimate_endpoint(sync_client):
    """Verify GET /api/v1/sync/estimate returns 200 OK with expected fields."""
    res = sync_client.get("/api/v1/sync/estimate")
    assert res.status_code == 200
    data = res.json()
    assert "database_type" in data
    assert "total_novels" in data
    assert "total_chapters" in data
    assert "storage_size_mb" in data
    assert "estimated_zip_size_mb" in data


def test_api_import_invalid_file_format(sync_client, tmp_path):
    """Verify POST /api/v1/sync/import-package rejects non-zip files."""
    dummy_txt = tmp_path / "not_a_zip.txt"
    dummy_txt.write_text("hello", encoding="utf-8")

    with open(dummy_txt, "rb") as f:
        res = sync_client.post(
            "/api/v1/sync/import-package",
            files={"file": ("not_a_zip.txt", f, "text/plain")},
        )
    assert res.status_code == 400
    assert "định dạng .zip" in res.json().get("detail", "")


def test_api_import_enforces_upload_limit(sync_client, tmp_path, monkeypatch):
    package = tmp_path / "package.zip"
    package.write_bytes(b"0123456789")
    monkeypatch.setattr(settings, "max_sync_package_upload_bytes", 1)

    with package.open("rb") as file_handle:
        response = sync_client.post(
            "/api/v1/sync/import-package",
            files={"file": ("package.zip", file_handle, "application/zip")},
        )

    assert response.status_code == 413
