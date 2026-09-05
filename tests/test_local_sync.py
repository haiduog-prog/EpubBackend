import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "local_sync.py"
SPEC = importlib.util.spec_from_file_location("local_sync", MODULE_PATH)
local_sync = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["local_sync"] = local_sync
SPEC.loader.exec_module(local_sync)


@pytest.fixture(autouse=True)
def mock_ensure_server_stopped(monkeypatch):
    monkeypatch.setattr(local_sync, "ensure_server_stopped", lambda *args, **kwargs: None)


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    (project / "storage" / "novels").mkdir(parents=True)
    (project / "storage" / "uploads").mkdir(parents=True)
    (project / "storage" / "cache").mkdir(parents=True)
    connection = sqlite3.connect(project / "data" / "local_db.sqlite3")
    connection.execute("CREATE TABLE novels (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    connection.execute("INSERT INTO novels VALUES ('book-1', 'Một truyện')")
    connection.commit()
    connection.close()
    (project / "storage" / "novels" / "book-1.txt").write_text("chapter one", encoding="utf-8")
    (project / "storage" / "cache" / "ignored.tmp").write_text("cache", encoding="utf-8")
    return project


def test_backup_creates_manifest_and_excludes_cache(tmp_path):
    project = make_project(tmp_path)
    sync_root = tmp_path / "drive" / "EpubBackendSync"

    result = local_sync.backup(project, sync_root)

    assert result["status"] == "SYNCED"
    manifest = json.loads((sync_root / "manifest.json").read_text(encoding="utf-8"))
    assert "novels/book-1.txt" in manifest["storage"]
    assert not (sync_root / "storage" / "cache").exists()
    restored_db = sqlite3.connect(sync_root / "database" / "local_db.sqlite3")
    assert restored_db.execute("SELECT title FROM novels").fetchone()[0] == "Một truyện"
    restored_db.close()


def test_check_without_existing_manifest_reports_pending_changes(tmp_path):
    project = make_project(tmp_path)
    sync_root = tmp_path / "drive"

    result = local_sync.check(project, sync_root)

    assert result["status"] == "CHANGES_PENDING"


def test_backup_updates_only_changed_storage_file(tmp_path):
    project = make_project(tmp_path)
    sync_root = tmp_path / "drive"
    local_sync.backup(project, sync_root)

    (project / "storage" / "novels" / "book-1.txt").write_text("chapter changed", encoding="utf-8")
    result = local_sync.backup(project, sync_root)

    assert result["uploaded"] == 1
    assert result["downloaded"] == 0
    assert (sync_root / "storage" / "novels" / "book-1.txt").read_text(encoding="utf-8") == "chapter changed"


def test_check_detects_remote_change_and_restore_applies_it(tmp_path):
    project = make_project(tmp_path)
    sync_root = tmp_path / "drive"
    local_sync.backup(project, sync_root)

    remote_file = sync_root / "storage" / "novels" / "book-1.txt"
    remote_file.write_text("changed on other machine", encoding="utf-8")
    check_result = local_sync.check(project, sync_root)
    assert check_result["status"] == "DRIVE_PENDING"

    restore_result = local_sync.restore(project, sync_root)
    assert restore_result["status"] == "SYNCED"
    assert (project / "storage" / "novels" / "book-1.txt").read_text(encoding="utf-8") == "changed on other machine"
    assert list((project / "data" / "sync-rollbacks").iterdir())


def test_restore_bootstraps_an_empty_machine(tmp_path):
    source_project = make_project(tmp_path / "source")
    sync_root = tmp_path / "drive"
    local_sync.backup(source_project, sync_root)

    new_project = tmp_path / "new-machine"
    (new_project / "storage" / "novels").mkdir(parents=True)
    (new_project / "storage" / "uploads").mkdir(parents=True)
    result = local_sync.restore(new_project, sync_root)

    assert result["status"] == "SYNCED"
    assert (new_project / "data" / "local_db.sqlite3").exists()
    assert (new_project / "storage" / "novels" / "book-1.txt").read_text(encoding="utf-8") == "chapter one"
    assert (new_project / "data" / ".local_sync_state.json").exists()


def test_conflict_is_blocked_without_overwriting_either_side(tmp_path):
    project = make_project(tmp_path)
    sync_root = tmp_path / "drive"
    local_sync.backup(project, sync_root)
    local_file = project / "storage" / "novels" / "book-1.txt"
    remote_file = sync_root / "storage" / "novels" / "book-1.txt"
    local_file.write_text("local version", encoding="utf-8")
    remote_file.write_text("remote version", encoding="utf-8")

    with pytest.raises(local_sync.SyncError) as error:
        local_sync.backup(project, sync_root)

    assert error.value.status == "CONFLICT"
    assert local_file.read_text(encoding="utf-8") == "local version"
    assert remote_file.read_text(encoding="utf-8") == "remote version"
