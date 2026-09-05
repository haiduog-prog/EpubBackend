import json
import sqlite3
from pathlib import Path

import pytest

from app.modules.sync.google_drive_sync import GoogleDriveSyncError, GoogleDriveSyncService, LocalSnapshotStore


class FakeDrive:
    def __init__(self):
        self.files = {}

    def read_bytes(self, key):
        return self.files.get(key)

    def write_bytes(self, key, data, mime_type):
        self.files[key] = bytes(data)

    def delete(self, key):
        self.files.pop(key, None)


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE settings (value TEXT NOT NULL)")
        connection.execute("INSERT INTO settings(value) VALUES (?)", (value,))
        connection.commit()


def _service(project_root: Path, drive: FakeDrive, monkeypatch) -> GoogleDriveSyncService:
    service = GoogleDriveSyncService()
    monkeypatch.setattr(service, "_root", lambda: project_root)
    monkeypatch.setattr(service, "_gateway", lambda: drive)
    return service


def test_backup_only_uploads_changed_files(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "storage" / "novels" / "book-1").mkdir(parents=True)
    chapter = project / "storage" / "novels" / "book-1" / "chapter-1.html"
    chapter.write_text("one", encoding="utf-8")
    _create_database(project / "data" / "local_db.sqlite3", "one")
    drive = FakeDrive()
    service = _service(project, drive, monkeypatch)

    first = service.backup()
    assert first["status"] == "SYNCED"
    assert first["uploaded"] == 2

    chapter.write_text("two", encoding="utf-8")
    second = service.backup()
    assert second["status"] == "SYNCED"
    assert second["uploaded"] == 1
    assert json.loads(drive.files["manifest.json"])["storage"]["novels/book-1/chapter-1.html"]["sha256"]
    assert drive.files["storage/novels/book-1/chapter-1.html"] == b"two"


def test_snapshot_upload_uses_stable_file_copy(tmp_path):
    project = tmp_path / "project"
    chapter = project / "storage" / "novels" / "chapter.html"
    chapter.parent.mkdir(parents=True)
    chapter.write_text("before", encoding="utf-8")

    snapshot = LocalSnapshotStore(project).create_snapshot(tmp_path / "snapshot")
    chapter.write_text("after", encoding="utf-8")

    assert snapshot.files["novels/chapter.html"].read_text(encoding="utf-8") == "before"


def test_remote_manifest_rejects_windows_path_traversal(tmp_path, monkeypatch):
    drive = FakeDrive()
    drive.files["manifest.json"] = json.dumps(
        {
            "schema_version": 1,
            "storage": {r"novels\..\..\escape.txt": {"sha256": "bad", "size": 3}},
        }
    ).encode("utf-8")
    service = _service(tmp_path / "project", drive, monkeypatch)

    with pytest.raises(GoogleDriveSyncError, match="key không hợp lệ"):
        service.check()


def test_restore_bootstraps_new_machine_and_uses_storage_root(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "storage" / "uploads").mkdir(parents=True)
    (source / "storage" / "uploads" / "cover.jpg").write_bytes(b"cover")
    _create_database(source / "data" / "local_db.sqlite3", "source")
    drive = FakeDrive()
    _service(source, drive, monkeypatch).backup()

    destination = tmp_path / "destination"
    result = _service(destination, drive, monkeypatch).restore()

    assert result["status"] == "SYNCED"
    assert (destination / "storage" / "uploads" / "cover.jpg").read_bytes() == b"cover"
    with sqlite3.connect(destination / "data" / "local_db.sqlite3") as connection:
        assert connection.execute("SELECT value FROM settings").fetchone()[0] == "source"


def test_conflict_does_not_overwrite_local_or_drive(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "storage" / "novels").mkdir(parents=True)
    chapter = project / "storage" / "novels" / "chapter.html"
    chapter.write_text("base", encoding="utf-8")
    drive = FakeDrive()
    service = _service(project, drive, monkeypatch)
    service.backup()

    chapter.write_text("local", encoding="utf-8")
    remote_manifest = json.loads(drive.files["manifest.json"])
    remote_key = "novels/chapter.html"
    drive_key = "storage/novels/chapter.html"
    drive.files[drive_key] = b"remote"
    remote_manifest["storage"][remote_key]["sha256"] = "remote-sha"
    remote_manifest["storage"][remote_key]["size"] = len(b"remote")
    drive.files["manifest.json"] = json.dumps(remote_manifest).encode("utf-8")

    try:
        service.backup()
    except Exception as exc:
        assert getattr(exc, "status", None) == "CONFLICT"
    else:
        raise AssertionError("backup should stop on conflict")
    assert chapter.read_text(encoding="utf-8") == "local"
    assert drive.files[drive_key] == b"remote"


def test_restore_fails_when_database_content_sha256_mismatches(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _create_database(source / "data" / "local_db.sqlite3", "original")
    drive = FakeDrive()
    _service(source, drive, monkeypatch).backup()

    # Corrupt the remote database with different data but valid SQLite structure
    corrupt_db_path = tmp_path / "corrupt.sqlite3"
    _create_database(corrupt_db_path, "tampered")
    drive.files["database/local_db.sqlite3"] = corrupt_db_path.read_bytes()

    destination = tmp_path / "destination"
    service = _service(destination, drive, monkeypatch)
    try:
        service.restore()
    except Exception as exc:
        assert getattr(exc, "status", None) == "ERROR"
        assert "Checksum" in str(exc)
    else:
        raise AssertionError("restore should have failed due to content_sha256 mismatch")


def test_force_restore_respects_allow_deletions(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "storage" / "novels").mkdir(parents=True)
    chapter_a = source / "storage" / "novels" / "chapter-a.html"
    chapter_a.write_text("chapter-a", encoding="utf-8")
    drive = FakeDrive()
    _service(source, drive, monkeypatch).backup()

    destination = tmp_path / "destination"
    dest_service = _service(destination, drive, monkeypatch)
    dest_service.restore()

    # Destination adds a local file that remote doesn't have
    chapter_extra = destination / "storage" / "novels" / "chapter-extra.html"
    chapter_extra.write_text("extra", encoding="utf-8")

    # Force restore with allow_deletions=False should block because local has files to delete
    try:
        dest_service.restore(force=True, allow_deletions=False)
    except Exception as exc:
        assert getattr(exc, "status", None) == "CHANGES_PENDING"
        assert "allowDeletions" in str(exc)
    else:
        raise AssertionError("force restore must require allow_deletions when remote deletions are pending")
    assert chapter_extra.exists()

    # Now force restore with allow_deletions=True should succeed and clean up extra file (with rollback)
    result = dest_service.restore(force=True, allow_deletions=True)
    assert result["status"] == "SYNCED"
    assert not chapter_extra.exists()


def test_restore_supports_legacy_manifest_storage_prefix(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "storage" / "novels").mkdir(parents=True)
    (source / "storage" / "novels" / "legacy.html").write_text("legacy text", encoding="utf-8")
    _create_database(source / "data" / "local_db.sqlite3", "legacy")
    drive = FakeDrive()
    _service(source, drive, monkeypatch).backup()

    # Modify manifest to simulate legacy format with 'storage/' prefix in storage keys
    manifest = json.loads(drive.files["manifest.json"])
    storage_entries = manifest["storage"]
    legacy_storage = {f"storage/{k}": v for k, v in storage_entries.items()}
    manifest["storage"] = legacy_storage
    drive.files["manifest.json"] = json.dumps(manifest).encode("utf-8")

    destination = tmp_path / "destination"
    result = _service(destination, drive, monkeypatch).restore()
    assert result["status"] == "SYNCED"
    assert (destination / "storage" / "novels" / "legacy.html").read_text(encoding="utf-8") == "legacy text"

