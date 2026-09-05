"""Incremental local data sync for two Windows machines using Google Drive Desktop.

The application keeps using local data. This script treats the configured sync
directory as a transport folder and never runs the app against it directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = 1
STORAGE_BUCKETS = ("novels", "uploads")
DEFAULT_PORT = 8000
STATUS_FILE = "sync-status.json"
MANIFEST_FILE = "manifest.json"
LOCAL_STATE_FILE = ".local_sync_state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SyncError(RuntimeError):
    def __init__(self, status: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


@dataclass
class Comparison:
    upload: List[str] = field(default_factory=list)
    download: List[str] = field(default_factory=list)
    local_deletions: List[str] = field(default_factory=list)
    remote_deletions: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)

    @property
    def local_changed(self) -> bool:
        return bool(self.upload or self.local_deletions)

    @property
    def remote_changed(self) -> bool:
        return bool(self.download or self.remote_deletions)


def project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize_sync_root(raw_path: Path) -> Path:
    """Normalize the sync path without resolving a path on an offline drive."""
    path = raw_path.expanduser()
    drive, _ = os.path.splitdrive(str(path))
    if drive:
        drive_root = Path(f"{drive}{os.sep}")
        try:
            drive_available = drive_root.exists()
        except OSError as exc:
            raise SyncError("DRIVE_PENDING", f"Không truy cập được ổ Google Drive '{drive_root}': {exc}") from exc
        if not drive_available:
            raise SyncError(
                "DRIVE_PENDING",
                f"Không tìm thấy ổ '{drive_root}'. Hãy mở Google Drive Desktop, kiểm tra đúng drive letter "
                "bằng 'Test-Path G:\\' rồi truyền đường dẫn thư mục đồng bộ thực tế.",
            )
    try:
        return Path(os.path.abspath(path))
    except OSError as exc:
        raise SyncError("DRIVE_PENDING", f"Không chuẩn hóa được đường dẫn đồng bộ '{path}': {exc}") from exc


def local_database_path(project_root: Path) -> Path:
    return project_root / "data" / "local_db.sqlite3"


def local_storage_root(project_root: Path) -> Path:
    return project_root / "storage"


def ensure_inside(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SyncError("ERROR", f"Đường dẫn nằm ngoài thư mục cho phép: {candidate}") from exc
    return candidate_resolved


def key_to_path(root: Path, key: str) -> Path:
    parts = PurePosixPath(key).parts
    if len(parts) < 2 or parts[0] not in STORAGE_BUCKETS or any(part in {"", ".", ".."} for part in parts):
        raise SyncError("ERROR", f"Storage key không hợp lệ: {key}")
    return ensure_inside(root, root.joinpath(*parts))


def iter_storage_files(storage_root: Path) -> Iterable[tuple[str, Path]]:
    for bucket in STORAGE_BUCKETS:
        bucket_root = storage_root / bucket
        if not bucket_root.exists():
            continue
        for path in sorted(bucket_root.rglob("*")):
            if path.is_file() and not path.name.endswith(".syncing"):
                relative = path.relative_to(bucket_root).as_posix()
                yield f"{bucket}/{relative}", path


def file_metadata(path: Path, stability_attempts: int = 3, stability_wait: float = 0.2) -> Dict[str, Any]:
    """Hash a file only when its size and mtime remain stable while reading."""
    last_signature: Optional[tuple[int, int]] = None
    for attempt in range(stability_attempts):
        try:
            before = path.stat()
        except FileNotFoundError as exc:
            raise SyncError("DRIVE_PENDING", f"File biến mất trong lúc đọc: {path}") from exc
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        try:
            after = path.stat()
        except FileNotFoundError as exc:
            raise SyncError("DRIVE_PENDING", f"File biến mất sau khi đọc: {path}") from exc
        signature = (after.st_size, after.st_mtime_ns)
        if (before.st_size, before.st_mtime_ns) == signature:
            return {"size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest.hexdigest()}
        last_signature = signature
        if attempt + 1 < stability_attempts:
            time.sleep(stability_wait)
    raise SyncError("DRIVE_PENDING", f"File đang thay đổi hoặc Drive chưa ổn định: {path}", {"signature": last_signature})


def collect_storage_manifest(storage_root: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for key, path in iter_storage_files(storage_root):
        result[key] = file_metadata(path)
    return result


def sqlite_metadata(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    metadata = file_metadata(path)
    digest = hashlib.sha256()
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            for line in connection.iterdump():
                digest.update(line.encode("utf-8"))
                digest.update(b"\n")
    except sqlite3.Error as exc:
        raise SyncError("ERROR", f"Không đọc được nội dung SQLite: {path}: {exc}") from exc
    metadata["content_sha256"] = digest.hexdigest()
    return metadata


def same_content(left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]) -> bool:
    if left is None or right is None:
        return left is right
    return left.get("size") == right.get("size") and left.get("sha256") == right.get("sha256")


def same_database(left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]) -> bool:
    if left is None or right is None:
        return left is right
    left_fingerprint = left.get("content_sha256")
    right_fingerprint = right.get("content_sha256")
    if left_fingerprint and right_fingerprint:
        return left_fingerprint == right_fingerprint
    return same_content(left, right)


def load_manifest(sync_root: Path, required: bool = False) -> Optional[Dict[str, Any]]:
    path = sync_root / MANIFEST_FILE
    if not path.exists():
        if required:
            raise SyncError("ERROR", f"Chưa có {MANIFEST_FILE} trong thư mục đồng bộ: {sync_root}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError("ERROR", f"Không đọc được {path}: {exc}") from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SyncError("ERROR", f"Manifest không tương thích: schema_version={data.get('schema_version')}")
    if not isinstance(data.get("storage"), dict):
        raise SyncError("ERROR", "Manifest thiếu mục storage hợp lệ")
    return data


def local_state_path(project_root: Path) -> Path:
    return project_root / "data" / LOCAL_STATE_FILE


def load_local_state(project_root: Path) -> Optional[Dict[str, Any]]:
    path = local_state_path(project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError("ERROR", f"Không đọc được local sync state {path}: {exc}") from exc
    manifest = data.get("manifest")
    if data.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest, dict) or not isinstance(manifest.get("storage"), dict):
        raise SyncError("ERROR", f"Local sync state không hợp lệ: {path}")
    return manifest


def write_local_state(project_root: Path, manifest: Dict[str, Any]) -> None:
    write_json_atomic(
        local_state_path(project_root),
        {"schema_version": SCHEMA_VERSION, "updated_at": utc_now(), "manifest": manifest},
    )


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def write_status(sync_root: Path, status: str, summary: Dict[str, Any], error: Optional[str] = None) -> None:
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "machine": platform.node(),
        "updated_at": utc_now(),
        **summary,
    }
    if error:
        payload["error"] = error
    try:
        write_json_atomic(sync_root / STATUS_FILE, payload)
    except OSError:
        pass


def remote_storage_manifest(sync_root: Path) -> Dict[str, Dict[str, Any]]:
    return collect_storage_manifest(sync_root / "storage")


def compare_entries(
    local: Dict[str, Dict[str, Any]],
    remote: Dict[str, Dict[str, Any]],
    baseline: Dict[str, Dict[str, Any]],
) -> Comparison:
    result = Comparison()
    for key in sorted(set(local) | set(remote) | set(baseline)):
        local_entry = local.get(key)
        remote_entry = remote.get(key)
        baseline_entry = baseline.get(key)
        if same_content(local_entry, remote_entry):
            continue
        local_changed = not same_content(local_entry, baseline_entry)
        remote_changed = not same_content(remote_entry, baseline_entry)
        if local_changed and remote_changed:
            result.conflicts.append(key)
        elif local_changed:
            if local_entry is None and baseline_entry is not None:
                result.local_deletions.append(key)
            else:
                result.upload.append(key)
        elif remote_changed:
            if remote_entry is None and baseline_entry is not None:
                result.remote_deletions.append(key)
            else:
                result.download.append(key)
    return result


def compare_database(
    local: Optional[Dict[str, Any]],
    remote: Optional[Dict[str, Any]],
    baseline: Optional[Dict[str, Any]],
) -> str:
    if same_database(local, remote):
        return "unchanged"
    local_changed = not same_database(local, baseline)
    remote_changed = not same_database(remote, baseline)
    if local_changed and remote_changed:
        return "conflict"
    if local_changed:
        return "upload" if local is not None else "local-deletion"
    if remote_changed:
        return "download" if remote is not None else "remote-deletion"
    return "unchanged"


def summary_for(comparison: Comparison, database_action: str) -> Dict[str, Any]:
    return {
        "uploaded": len(comparison.upload) + (1 if database_action == "upload" else 0),
        "downloaded": len(comparison.download) + (1 if database_action == "download" else 0),
        "unchanged": 0,
        "local_deletions": len(comparison.local_deletions) + (1 if database_action == "local-deletion" else 0),
        "remote_deletions": len(comparison.remote_deletions) + (1 if database_action == "remote-deletion" else 0),
        "conflicts": comparison.conflicts + (["database/local_db.sqlite3"] if database_action == "conflict" else []),
        "files": {
            "upload": comparison.upload,
            "download": comparison.download,
            "local_deletions": comparison.local_deletions,
            "remote_deletions": comparison.remote_deletions,
        },
    }


def enrich_unchanged(summary: Dict[str, Any], local: Dict[str, Any], remote: Dict[str, Any]) -> None:
    storage_files = summary.get("files", {})
    summary["unchanged"] = max(
        0,
        len(set(local) & set(remote))
        - len(storage_files.get("upload", []))
        - len(storage_files.get("download", [])),
    )


def add_database_status(summary: Dict[str, Any], database: Optional[Dict[str, Any]]) -> None:
    if database:
        summary["database_sha256"] = database.get("content_sha256") or database.get("sha256")


def ensure_server_stopped(port: int = DEFAULT_PORT) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            raise SyncError("ERROR", f"Server vẫn đang chạy trên 127.0.0.1:{port}; hãy tắt server trước.")
    except SyncError:
        raise
    except OSError:
        return


def copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".syncing", dir=str(destination.parent))
    os.close(fd)
    try:
        shutil.copy2(source, temp_name)
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def remove_storage_file(root: Path, key: str) -> None:
    path = key_to_path(root, key)
    if path.exists():
        path.unlink()


def sqlite_backup(source: Path, destination: Path) -> None:
    if not source.exists():
        raise SyncError("ERROR", f"Không tìm thấy database: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".sqlite3", dir=str(destination.parent))
    os.close(fd)
    try:
        source_connection = sqlite3.connect(str(source))
        destination_connection = sqlite3.connect(temp_name)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
        os.replace(temp_name, destination)
    except sqlite3.Error as exc:
        raise SyncError("ERROR", f"SQLite backup thất bại: {exc}") from exc
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def rollback_path(project_root: Path) -> Path:
    return project_root / "data" / "sync-rollbacks" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def backup_local_database(project_root: Path, rollback_root: Path) -> None:
    source = local_database_path(project_root)
    if source.exists():
        sqlite_backup(source, rollback_root / "data" / "local_db.sqlite3")
        for suffix in ("-wal", "-shm"):
            auxiliary = Path(f"{source}{suffix}")
            if auxiliary.exists():
                copy_atomic(auxiliary, rollback_root / "data" / auxiliary.name)


def build_manifest(sync_root: Path, machine: Optional[str] = None) -> Dict[str, Any]:
    database = sync_root / "database" / "local_db.sqlite3"
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "machine": machine or platform.node(),
        "storage": remote_storage_manifest(sync_root),
        "database": sqlite_metadata(database),
    }


def result_status_for(comparison: Comparison, database_action: str) -> str:
    if comparison.conflicts or database_action == "conflict":
        return "CONFLICT"
    if comparison.remote_changed or database_action in {"download", "remote-deletion"}:
        return "DRIVE_PENDING"
    if comparison.local_changed or database_action in {"upload", "local-deletion"}:
        return "CHANGES_PENDING"
    return "READY"


def check(project_root: Path, sync_root: Path) -> Dict[str, Any]:
    manifest = load_manifest(sync_root)
    local_state = load_local_state(project_root)
    local_storage = collect_storage_manifest(local_storage_root(project_root))
    remote_storage = remote_storage_manifest(sync_root)
    baseline_manifest = local_state or (manifest if manifest is not None else {"storage": {}, "database": None})
    baseline_storage = baseline_manifest.get("storage", {})
    comparison = compare_entries(local_storage, remote_storage, baseline_storage)
    local_db = sqlite_metadata(local_database_path(project_root))
    remote_db = sqlite_metadata(sync_root / "database" / "local_db.sqlite3")
    database_action = compare_database(local_db, remote_db, baseline_manifest.get("database"))
    summary = summary_for(comparison, database_action)
    enrich_unchanged(summary, local_storage, remote_storage)
    add_database_status(summary, remote_db or local_db)
    if manifest is not None and local_state is None:
        status = "DRIVE_PENDING"
        summary["message"] = "Máy này chưa có local baseline; hãy restore trước khi backup hoặc chạy server."
    elif manifest is None:
        status = "CHANGES_PENDING" if local_storage or local_db else "READY"
        summary["message"] = "Chưa có manifest; hãy chạy backup trên máy có dữ liệu gốc."
    else:
        status = result_status_for(comparison, database_action)
    summary["status"] = status
    write_status(sync_root, status, summary)
    print_result(status, summary)
    return summary


def raise_if_unsafe_for_backup(comparison: Comparison, database_action: str, allow_deletions: bool) -> None:
    if comparison.conflicts or database_action == "conflict":
        raise SyncError("CONFLICT", "Phát hiện dữ liệu đã thay đổi ở cả local và Drive.", summary_for(comparison, database_action))
    if comparison.remote_changed or database_action in {"download", "remote-deletion"}:
        raise SyncError("DRIVE_PENDING", "Drive có thay đổi mới; hãy restore trước khi backup máy này.", summary_for(comparison, database_action))
    if (comparison.local_deletions or database_action == "local-deletion") and not allow_deletions:
        raise SyncError("CHANGES_PENDING", "Có file bị xóa local; dùng --allow-deletions nếu muốn xóa trên Drive.", summary_for(comparison, database_action))


def backup(project_root: Path, sync_root: Path, allow_deletions: bool = False, port: int = DEFAULT_PORT) -> Dict[str, Any]:
    ensure_server_stopped(port)
    sync_root.mkdir(parents=True, exist_ok=True)
    (sync_root / "storage").mkdir(parents=True, exist_ok=True)
    (sync_root / "database").mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(sync_root)
    local_state = load_local_state(project_root)
    if manifest is not None and local_state is None:
        raise SyncError("DRIVE_PENDING", "Máy này chưa restore snapshot hiện tại; hãy restore trước khi backup.")
    baseline_manifest = local_state or {"storage": {}, "database": None}
    baseline_storage = baseline_manifest.get("storage", {})
    local_storage = collect_storage_manifest(local_storage_root(project_root))
    remote_storage = remote_storage_manifest(sync_root)
    comparison = compare_entries(local_storage, remote_storage, baseline_storage)
    database_action = compare_database(
        sqlite_metadata(local_database_path(project_root)),
        sqlite_metadata(sync_root / "database" / "local_db.sqlite3"),
        baseline_manifest.get("database"),
    )
    raise_if_unsafe_for_backup(comparison, database_action, allow_deletions)
    pending = summary_for(comparison, database_action)
    write_status(sync_root, "SYNCING_UP", pending)
    for key in comparison.upload:
        copy_atomic(key_to_path(local_storage_root(project_root), key), key_to_path(sync_root / "storage", key))
    if allow_deletions:
        for key in comparison.local_deletions:
            remove_storage_file(sync_root / "storage", key)
    if database_action == "upload":
        sqlite_backup(local_database_path(project_root), sync_root / "database" / "local_db.sqlite3")
    elif database_action == "local-deletion" and allow_deletions:
        database_path = sync_root / "database" / "local_db.sqlite3"
        if database_path.exists():
            database_path.unlink()
    final_manifest = build_manifest(sync_root)
    write_json_atomic(sync_root / MANIFEST_FILE, final_manifest)
    write_local_state(project_root, final_manifest)
    final_summary = summary_for(comparison, database_action)
    final_summary["status"] = "SYNCED"
    final_summary["unchanged"] = max(0, len(final_manifest["storage"]) - len(comparison.upload))
    add_database_status(final_summary, final_manifest.get("database"))
    write_status(sync_root, "SYNCED", final_summary)
    print_result("SYNCED", final_summary)
    return final_summary


def prepare_rollback(project_root: Path, comparison: Comparison, database_action: str) -> Path:
    rollback = rollback_path(project_root)
    rollback.mkdir(parents=True, exist_ok=True)
    for key in set(comparison.download + comparison.remote_deletions):
        local_path = key_to_path(local_storage_root(project_root), key)
        if local_path.exists():
            copy_atomic(local_path, key_to_path(rollback / "storage", key))
    if database_action in {"download", "conflict", "remote-deletion"}:
        backup_local_database(project_root, rollback)
    state = local_state_path(project_root)
    if state.exists():
        copy_atomic(state, rollback / "data" / state.name)
    return rollback


def restore(
    project_root: Path,
    sync_root: Path,
    allow_deletions: bool = False,
    force: bool = False,
    port: int = DEFAULT_PORT,
) -> Dict[str, Any]:
    ensure_server_stopped(port)
    manifest = load_manifest(sync_root, required=True)
    local_state = load_local_state(project_root)
    local_storage = collect_storage_manifest(local_storage_root(project_root))
    remote_storage = remote_storage_manifest(sync_root)
    database_action = compare_database(
        sqlite_metadata(local_database_path(project_root)),
        sqlite_metadata(sync_root / "database" / "local_db.sqlite3"),
        (local_state or {"database": None}).get("database"),
    )
    if local_state is None and not local_storage and not sqlite_metadata(local_database_path(project_root)):
        comparison = Comparison(upload=[], download=sorted(remote_storage))
        database_action = "download" if (sync_root / "database" / "local_db.sqlite3").exists() else "unchanged"
    else:
        baseline_manifest = local_state or {"storage": {}, "database": None}
        comparison = compare_entries(local_storage, remote_storage, baseline_manifest.get("storage", {}))
        database_action = compare_database(
            sqlite_metadata(local_database_path(project_root)),
            sqlite_metadata(sync_root / "database" / "local_db.sqlite3"),
            baseline_manifest.get("database"),
        )
    if comparison.conflicts or database_action == "conflict":
        if not force:
            raise SyncError("CONFLICT", "Phát hiện conflict; restore bị dừng để bảo toàn dữ liệu.", summary_for(comparison, database_action))
    if comparison.local_changed or database_action in {"upload", "local-deletion"}:
        if not force:
            raise SyncError("CHANGES_PENDING", "Local có thay đổi chưa backup; dùng --force để restore và ghi đè sau khi tạo rollback.", summary_for(comparison, database_action))
    if comparison.remote_deletions and not allow_deletions:
        raise SyncError("CHANGES_PENDING", "Drive có file đã xóa; dùng --allow-deletions để áp dụng xóa local.", summary_for(comparison, database_action))
    if database_action == "remote-deletion" and not allow_deletions:
        raise SyncError("CHANGES_PENDING", "Database trên Drive đã bị xóa; cần --allow-deletions để áp dụng.", summary_for(comparison, database_action))
    if force:
        comparison.download = sorted(
            set(remote_storage) - set(local_storage)
            | {
                key
                for key in set(remote_storage) & set(local_storage)
                if not same_content(remote_storage[key], local_storage[key])
            }
        )
        comparison.remote_deletions = sorted(set(local_storage) - set(remote_storage))
    pending = summary_for(comparison, database_action)
    write_status(sync_root, "SYNCING_DOWN", pending)
    rollback = prepare_rollback(project_root, comparison, database_action)
    for key in comparison.download:
        copy_atomic(key_to_path(sync_root / "storage", key), key_to_path(local_storage_root(project_root), key))
    if allow_deletions:
        for key in comparison.remote_deletions:
            remove_storage_file(local_storage_root(project_root), key)
    if database_action == "download" or (force and (sync_root / "database" / "local_db.sqlite3").exists()):
        local_db = local_database_path(project_root)
        local_db.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            auxiliary = Path(f"{local_db}{suffix}")
            if auxiliary.exists():
                copy_atomic(auxiliary, rollback / "data" / auxiliary.name)
                auxiliary.unlink()
        copy_atomic(sync_root / "database" / "local_db.sqlite3", local_db)
    elif database_action == "remote-deletion" and allow_deletions:
        local_db = local_database_path(project_root)
        if local_db.exists():
            local_db.unlink()
    final_summary = summary_for(comparison, database_action)
    final_summary["rollback"] = str(rollback)
    final_summary["status"] = "SYNCED"
    final_summary["unchanged"] = max(0, len(remote_storage) - len(comparison.download))
    add_database_status(final_summary, manifest.get("database"))
    write_local_state(project_root, manifest)
    write_status(sync_root, "SYNCED", final_summary)
    print_result("SYNCED", final_summary)
    return final_summary


def print_result(status: str, summary: Dict[str, Any]) -> None:
    print(f"[{status}]")
    print(f"Uploaded: {summary.get('uploaded', 0)}")
    print(f"Downloaded: {summary.get('downloaded', 0)}")
    print(f"Unchanged: {summary.get('unchanged', 0)}")
    print(f"Local deletions: {summary.get('local_deletions', 0)}")
    print(f"Remote deletions: {summary.get('remote_deletions', 0)}")
    conflicts = summary.get("conflicts", [])
    if conflicts:
        print(f"Conflicts: {len(conflicts)}")
        for item in conflicts[:10]:
            print(f"  - {item}")
    if summary.get("message"):
        print(summary["message"])


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Đồng bộ dữ liệu local EpubBackend qua Google Drive Desktop")
    parser.add_argument("action", choices=("backup", "check", "restore"))
    parser.add_argument(
        "--sync-root",
        type=Path,
        default=os.getenv("EPUB_SYNC_ROOT"),
        help="Thư mục EpubBackendSync trên Google Drive; mặc định từ EPUB_SYNC_ROOT",
    )
    parser.add_argument("--project-root", type=Path, default=project_root_from_script())
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port local cần kiểm tra trước backup/restore")
    parser.add_argument("--allow-deletions", action="store_true", help="Cho phép áp dụng xóa theo hướng đồng bộ")
    parser.add_argument("--force", action="store_true", help="Restore remote và ghi đè local sau khi tạo rollback")
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.sync_root:
        print("[ERROR] Thiếu --sync-root hoặc biến môi trường EPUB_SYNC_ROOT.", file=sys.stderr)
        return 2
    sync_root: Optional[Path] = None
    try:
        project_root = args.project_root.resolve()
        sync_root = normalize_sync_root(args.sync_root)
        if args.action == "check":
            check(project_root, sync_root)
        elif args.action == "backup":
            backup(project_root, sync_root, args.allow_deletions, args.port)
        else:
            restore(project_root, sync_root, args.allow_deletions, args.force, args.port)
        return 0
    except SyncError as exc:
        summary = exc.details or {}
        summary["status"] = exc.status
        if sync_root is not None:
            try:
                write_status(sync_root, exc.status, summary, str(exc))
            except OSError:
                pass
        print(f"[{exc.status}] {exc}", file=sys.stderr)
        for key in ("conflicts", "files"):
            if summary.get(key):
                print(json.dumps({key: summary[key]}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as exc:
        if sync_root is not None:
            try:
                write_status(sync_root, "ERROR", {}, str(exc))
            except OSError:
                pass
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    configure_console()
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
