"""Incremental Google Drive synchronization for the local EpubBackend data.

The app continues to use local files and SQLite. Google Drive is used as a
transport folder through the Drive API, so Android callers do not need a
Windows drive letter or Google Drive Desktop.
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import shutil
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional, Protocol

from app.config import settings


SCHEMA_VERSION = 1
STORAGE_BUCKETS = ("novels", "uploads")
MANIFEST_KEY = "manifest.json"
STATUS_KEY = "sync-status.json"
DATABASE_KEY = "database/local_db.sqlite3"
LOCAL_STATE_FILE = ".google_drive_sync_state.json"
LOCAL_STATUS_FILE = ".google_drive_sync_status.json"
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]
DRIVE_SCOPE = DRIVE_SCOPES[0]


class GoogleDriveSyncError(RuntimeError):
    def __init__(self, status: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _same(left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]) -> bool:
    if left is None or right is None:
        return left is right
    if left.get("content_sha256") and right.get("content_sha256"):
        return left["content_sha256"] == right["content_sha256"]
    return left.get("sha256") == right.get("sha256") and left.get("size") == right.get("size")


def _safe_key(key: str) -> str:
    path = PurePosixPath(key)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise GoogleDriveSyncError("ERROR", f"Sync key không hợp lệ: {key}")
    allowed_roots = {"storage", "database", *STORAGE_BUCKETS}
    if key != MANIFEST_KEY and key != STATUS_KEY and (path.parts[0] not in allowed_roots):
        raise GoogleDriveSyncError("ERROR", f"Sync key ngoài phạm vi cho phép: {key}")
    return "/".join(path.parts)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    connection = None
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        for line in connection.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
    except sqlite3.Error as exc:
        raise GoogleDriveSyncError("ERROR", f"Không đọc được SQLite snapshot: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    return digest.hexdigest()


def _metadata(path: Path, database: bool = False) -> Dict[str, Any]:
    stat = path.stat()
    result: Dict[str, Any] = {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _hash_file(path),
    }
    if database:
        result["content_sha256"] = _sqlite_content_hash(path)
    return result


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.syncing")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class DriveDataSource(Protocol):
    def read_bytes(self, key: str) -> Optional[bytes]: ...
    def write_bytes(self, key: str, data: bytes, mime_type: str) -> None: ...
    def delete(self, key: str) -> None: ...


class GoogleDriveDataSource:
    """Small Drive API v3 adapter using a configured service account or OAuth token."""

    def __init__(self, root_folder_id: str, credentials: Any):
        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError, ResumableUploadError
            from googleapiclient.http import MediaIoBaseUpload
        except ImportError as exc:
            raise GoogleDriveSyncError(
                "ERROR",
                "Thiếu thư viện Google Drive API. Cài google-api-python-client và google-auth.",
            ) from exc
        self._build = build
        self._http_error = HttpError
        self._resumable_upload_error = ResumableUploadError
        self._media_upload = MediaIoBaseUpload
        self.root_folder_id = root_folder_id
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _translate_error(self, exc: Exception) -> GoogleDriveSyncError:
        if isinstance(exc, GoogleDriveSyncError):
            return exc
        msg = str(exc)
        if "storageQuotaExceeded" in msg or "Service Accounts do not have storage quota" in msg:
            return GoogleDriveSyncError(
                "QUOTA_EXCEEDED",
                "Google Drive từ chối upload vì tài khoản Service Account có 0 byte hạn ngạch trên Drive cá nhân. "
                "Vui lòng dùng OAuth 2.0 (tài khoản cá nhân) bằng script 'python scripts/google_drive_login.py' "
                "hoặc dùng Google Workspace Shared Drive (Bộ nhớ dùng chung).",
                details={"reason": "storageQuotaExceeded"},
            )
        if isinstance(exc, self._http_error):
            status = getattr(exc.resp, "status", None)
            if status in (401, 403):
                return GoogleDriveSyncError(
                    "AUTH_REQUIRED",
                    f"Lỗi quyền truy cập Google Drive: {msg}",
                    details={"http_status": status},
                )
            if status == 404:
                return GoogleDriveSyncError(
                    "NOT_FOUND",
                    f"Không tìm thấy thư mục hoặc tệp trên Google Drive: {msg}",
                    details={"http_status": status},
                )
        return GoogleDriveSyncError("ERROR", f"Lỗi Google Drive: {msg}")

    def _find(self, parent_id: str, name: str, folder: bool = False) -> Optional[Dict[str, Any]]:
        try:
            escaped_name = name.replace("'", "\\'")
            query = f"'{parent_id}' in parents and name = '{escaped_name}' and trashed = false"
            if folder:
                query += " and mimeType = 'application/vnd.google-apps.folder'"
            result = self.service.files().list(
                q=query,
                fields="files(id,name,mimeType)",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files = result.get("files", [])
            return files[0] if files else None
        except Exception as exc:
            raise self._translate_error(exc) from exc

    def _parent_and_name(self, key: str, create: bool = True) -> tuple[str, str]:
        parts = PurePosixPath(_safe_key(key)).parts
        parent_id = self.root_folder_id
        for part in parts[:-1]:
            item = self._find(parent_id, part, folder=True)
            if item is None:
                if not create:
                    return "", parts[-1]
                try:
                    item = self.service.files().create(
                        body={"name": part, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
                        fields="id,name,mimeType",
                        supportsAllDrives=True,
                    ).execute()
                except Exception as exc:
                    raise self._translate_error(exc) from exc
            parent_id = item["id"]
        return parent_id, parts[-1]

    def read_bytes(self, key: str) -> Optional[bytes]:
        parent_id, name = self._parent_and_name(key, create=False)
        if not parent_id:
            return None
        item = self._find(parent_id, name)
        if item is None:
            return None
        try:
            return self.service.files().get(
                fileId=item["id"], alt="media", supportsAllDrives=True
            ).execute()
        except self._http_error as exc:
            if getattr(exc, "resp", None) is not None and exc.resp.status == 404:
                return None
            raise self._translate_error(exc) from exc
        except Exception as exc:
            raise self._translate_error(exc) from exc

    def write_bytes(self, key: str, data: bytes, mime_type: str) -> None:
        try:
            parent_id, name = self._parent_and_name(key)
            existing = self._find(parent_id, name)
            media = self._media_upload(io.BytesIO(data), mimetype=mime_type, resumable=True)
            if existing:
                self.service.files().update(
                    fileId=existing["id"], media_body=media, supportsAllDrives=True
                ).execute()
            else:
                self.service.files().create(
                    body={"name": name, "parents": [parent_id]},
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
        except Exception as exc:
            raise self._translate_error(exc) from exc

    def delete(self, key: str) -> None:
        try:
            parent_id, name = self._parent_and_name(key, create=False)
            if not parent_id:
                return
            existing = self._find(parent_id, name)
            if existing:
                self.service.files().delete(fileId=existing["id"], supportsAllDrives=True).execute()
        except Exception as exc:
            raise self._translate_error(exc) from exc


def _credentials_from_settings() -> Any:
    try:
        from google.oauth2.credentials import Credentials
        from google.oauth2 import service_account
    except ImportError as exc:
        raise GoogleDriveSyncError("ERROR", "Thiếu thư viện google-auth.") from exc

    raw_json = (settings.google_drive_credentials_json or "").strip()
    credentials_file = (settings.google_drive_credentials_file or "").strip()
    if raw_json:
        try:
            info = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise GoogleDriveSyncError("ERROR", "GOOGLE_DRIVE_CREDENTIALS_JSON không phải JSON hợp lệ.") from exc
        if info.get("type") == "service_account":
            return service_account.Credentials.from_service_account_info(
                info, scopes=DRIVE_SCOPES
            )
        return Credentials.from_authorized_user_info(info, scopes=None)
    if credentials_file:
        path = Path(credentials_file)
        if not path.exists():
            raise GoogleDriveSyncError("ERROR", f"Không tìm thấy file credential Google Drive: {path}")
        info = json.loads(path.read_text(encoding="utf-8"))
        if info.get("type") == "service_account":
            return service_account.Credentials.from_service_account_file(
                str(path), scopes=DRIVE_SCOPES
            )
        return Credentials.from_authorized_user_file(str(path), scopes=None)
    if settings.google_drive_refresh_token and settings.google_drive_client_id and settings.google_drive_client_secret:
        return Credentials(
            token=None,
            refresh_token=settings.google_drive_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_drive_client_id,
            client_secret=settings.google_drive_client_secret,
            scopes=DRIVE_SCOPES,
        )
    raise GoogleDriveSyncError(
        "AUTH_REQUIRED",
        "Chưa cấu hình credential Google Drive. Cần GOOGLE_DRIVE_CREDENTIALS_JSON/FILE hoặc refresh token.",
    )



@dataclass
class LocalSnapshot:
    manifest: Dict[str, Any]
    files: Dict[str, Path]


class LocalSnapshotStore:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.storage_root = project_root / "storage"
        self.data_root = project_root / "data"

    def state_path(self) -> Path:
        return self.data_root / LOCAL_STATE_FILE

    def status_path(self) -> Path:
        return self.data_root / LOCAL_STATUS_FILE

    def load_state(self) -> Optional[Dict[str, Any]]:
        path = self.state_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("manifest")
        except (OSError, json.JSONDecodeError) as exc:
            raise GoogleDriveSyncError("ERROR", f"Không đọc được local sync state: {exc}") from exc

    def save_state(self, manifest: Dict[str, Any]) -> None:
        _write_json(self.state_path(), {"schema_version": SCHEMA_VERSION, "updated_at": utc_now(), "manifest": manifest})

    def save_status(self, payload: Dict[str, Any]) -> None:
        _write_json(self.status_path(), payload)

    def create_snapshot(self, temporary_root: Path) -> LocalSnapshot:
        files: Dict[str, Path] = {}
        storage: Dict[str, Dict[str, Any]] = {}
        for bucket in STORAGE_BUCKETS:
            bucket_root = self.storage_root / bucket
            if not bucket_root.exists():
                continue
            for path in sorted(bucket_root.rglob("*")):
                if not path.is_file() or path.name.endswith(".syncing"):
                    continue
                key = f"{bucket}/{path.relative_to(bucket_root).as_posix()}"
                files[key] = path
                storage[key] = _metadata(path)

        database = self.data_root / "local_db.sqlite3"
        database_meta = None
        if database.exists():
            snapshot_db = temporary_root / "local_db.sqlite3"
            snapshot_db.parent.mkdir(parents=True, exist_ok=True)
            source_connection = None
            target_connection = None
            try:
                source_connection = sqlite3.connect(str(database))
                target_connection = sqlite3.connect(str(snapshot_db))
                source_connection.backup(target_connection)
            except sqlite3.Error as exc:
                raise GoogleDriveSyncError("ERROR", f"Không tạo được SQLite snapshot: {exc}") from exc
            finally:
                if source_connection is not None:
                    source_connection.close()
                if target_connection is not None:
                    target_connection.close()
            files[DATABASE_KEY] = snapshot_db
            database_meta = _metadata(snapshot_db, database=True)
        return LocalSnapshot(
            manifest={"schema_version": SCHEMA_VERSION, "created_at": utc_now(), "machine": os.environ.get("COMPUTERNAME", "android-backend"), "storage": storage, "database": database_meta},
            files=files,
        )


class GoogleDriveSyncService:
    def __init__(self):
        self._lock = threading.Lock()

    def _root(self) -> Path:
        return Path(settings.google_drive_project_root or Path(__file__).resolve().parents[3])

    def _gateway(self) -> DriveDataSource:
        if not settings.google_drive_sync_enabled:
            raise GoogleDriveSyncError("ERROR", "Google Drive sync đang tắt (GOOGLE_DRIVE_SYNC_ENABLED=false).")
        folder_id = settings.google_drive_sync_folder_id.strip()
        if not folder_id:
            raise GoogleDriveSyncError("ERROR", "Thiếu GOOGLE_DRIVE_SYNC_FOLDER_ID.")
        return GoogleDriveDataSource(folder_id, _credentials_from_settings())

    @staticmethod
    def _read_manifest(drive: DriveDataSource) -> Optional[Dict[str, Any]]:
        raw = drive.read_bytes(MANIFEST_KEY)
        if raw is None:
            return None
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GoogleDriveSyncError("ERROR", f"manifest.json trên Drive không hợp lệ: {exc}") from exc
        if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("storage"), dict):
            raise GoogleDriveSyncError("ERROR", "manifest.json không tương thích.")
        return manifest

    @staticmethod
    def _canonical_storage_key(key: str) -> str:
        return key.removeprefix("storage/")

    @classmethod
    def _entries(cls, manifest: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        if not manifest:
            return {}
        result = {cls._canonical_storage_key(key): value for key, value in manifest.get("storage", {}).items()}
        if manifest.get("database") is not None:
            result[DATABASE_KEY] = manifest["database"]
        return result

    @staticmethod
    def _to_drive_key(key: str) -> str:
        if key in {MANIFEST_KEY, STATUS_KEY, DATABASE_KEY} or key.startswith("database/"):
            return key
        if key.startswith("storage/"):
            return key
        return f"storage/{key}"

    @staticmethod
    def _to_local_target(store: LocalSnapshotStore, key: str) -> Path:
        if key == DATABASE_KEY or key.startswith("database/"):
            return store.data_root / "local_db.sqlite3"
        return store.storage_root / key.removeprefix("storage/")

    def _compare(self, local: Dict[str, Dict[str, Any]], remote: Dict[str, Dict[str, Any]], baseline: Dict[str, Dict[str, Any]]) -> Dict[str, list[str]]:
        result = {"upload": [], "download": [], "local_deletions": [], "remote_deletions": [], "conflicts": []}
        for key in sorted(set(local) | set(remote) | set(baseline)):
            local_changed = not _same(local.get(key), baseline.get(key))
            remote_changed = not _same(remote.get(key), baseline.get(key))
            if local_changed and remote_changed:
                if not _same(local.get(key), remote.get(key)):
                    result["conflicts"].append(key)
            elif local_changed:
                (result["upload"] if key in local else result["local_deletions"]).append(key)
            elif remote_changed:
                (result["download"] if key in remote else result["remote_deletions"]).append(key)
        return result

    def _finish(self, store: LocalSnapshotStore, status: str, summary: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"schema_version": SCHEMA_VERSION, "status": status, "updated_at": utc_now(), **summary}
        store.save_status(payload)
        return payload

    def status(self) -> Dict[str, Any]:
        store = LocalSnapshotStore(self._root())
        path = store.status_path()
        if not settings.google_drive_sync_enabled:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "DISABLED",
                "message": "Google Drive sync đang tắt (GOOGLE_DRIVE_SYNC_ENABLED=false). Cần cấu hình biến môi trường để kích hoạt.",
                "updated_at": None,
            }
        if not settings.google_drive_sync_folder_id.strip():
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "AUTH_REQUIRED",
                "message": "Thiếu cấu hình GOOGLE_DRIVE_SYNC_FOLDER_ID.",
                "updated_at": None,
            }
        if not path.exists():
            return {"schema_version": SCHEMA_VERSION, "status": "READY", "updated_at": None}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GoogleDriveSyncError("ERROR", f"Không đọc được trạng thái sync: {exc}") from exc

    def check(self) -> Dict[str, Any]:
        with self._lock:
            store = LocalSnapshotStore(self._root())
            with tempfile.TemporaryDirectory(prefix="epub-sync-") as temporary:
                local = store.create_snapshot(Path(temporary))
            drive = self._gateway()
            remote_manifest = self._read_manifest(drive)
            comparison = self._compare(self._entries(local.manifest), self._entries(remote_manifest), self._entries(store.load_state()))
            status = "CONFLICT" if comparison["conflicts"] else ("DRIVE_PENDING" if comparison["download"] or comparison["remote_deletions"] else ("CHANGES_PENDING" if comparison["upload"] or comparison["local_deletions"] else "READY"))
            return self._finish(store, status, {**comparison, "counts": {key: len(value) for key, value in comparison.items()}})

    def backup(self, allow_deletions: bool = False) -> Dict[str, Any]:
        with self._lock:
            store = LocalSnapshotStore(self._root())
            drive = self._gateway()
            remote_manifest = self._read_manifest(drive)
            state = store.load_state()
            if remote_manifest is not None and state is None:
                raise GoogleDriveSyncError("DRIVE_PENDING", "Thiết bị chưa restore snapshot hiện tại; hãy restore trước khi backup.")
            with tempfile.TemporaryDirectory(prefix="epub-sync-") as temporary:
                local = store.create_snapshot(Path(temporary))
                comparison = self._compare(self._entries(local.manifest), self._entries(remote_manifest), self._entries(state))
                if comparison["conflicts"]:
                    raise GoogleDriveSyncError("CONFLICT", "Phát hiện conflict; backup bị dừng để bảo toàn dữ liệu.", comparison)
                if comparison["download"] or comparison["remote_deletions"]:
                    raise GoogleDriveSyncError("DRIVE_PENDING", "Drive có thay đổi mới; hãy restore trước khi backup.", comparison)
                if comparison["local_deletions"] and not allow_deletions:
                    raise GoogleDriveSyncError("CHANGES_PENDING", "Có file bị xóa local; cần xác nhận allowDeletions.", comparison)
                for key in comparison["upload"]:
                    path = local.files[key]
                    drive_key = self._to_drive_key(key)
                    drive.write_bytes(drive_key, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                for key in comparison["local_deletions"]:
                    drive_key = self._to_drive_key(key)
                    drive.delete(drive_key)
                drive.write_bytes(MANIFEST_KEY, json.dumps(local.manifest, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")
                store.save_state(local.manifest)
                return self._finish(store, "SYNCED", {**comparison, "uploaded": len(comparison["upload"]), "deleted": len(comparison["local_deletions"])})

    def restore(self, allow_deletions: bool = False, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            store = LocalSnapshotStore(self._root())
            drive = self._gateway()
            remote_manifest = self._read_manifest(drive)
            if remote_manifest is None:
                raise GoogleDriveSyncError("ERROR", "Chưa có manifest.json trên Google Drive.")
            with tempfile.TemporaryDirectory(prefix="epub-sync-") as temporary:
                local = store.create_snapshot(Path(temporary))
                state = store.load_state()
                empty_local = not self._entries(local.manifest)
                baseline = self._entries(state)
                comparison = self._compare(self._entries(local.manifest), self._entries(remote_manifest), baseline)
                if state is None and empty_local:
                    comparison = {"upload": [], "download": sorted(self._entries(remote_manifest)), "local_deletions": [], "remote_deletions": [], "conflicts": []}
                if force:
                    remote_keys = set(self._entries(remote_manifest))
                    local_keys = set(self._entries(local.manifest))
                    comparison["remote_deletions"] = sorted(local_keys - remote_keys)
                if comparison["conflicts"] and not force:
                    raise GoogleDriveSyncError("CONFLICT", "Phát hiện conflict; restore bị dừng.", comparison)
                if (comparison["upload"] or comparison["local_deletions"]) and not force:
                    raise GoogleDriveSyncError("CHANGES_PENDING", "Local có thay đổi chưa backup; cần force để restore.", comparison)
                if comparison["remote_deletions"] and not allow_deletions:
                    raise GoogleDriveSyncError("CHANGES_PENDING", "Drive có file đã xóa; cần xác nhận allowDeletions.", comparison)
                keys_to_download = sorted(self._entries(remote_manifest)) if force else comparison["download"]
                rollback = store.data_root / "sync-rollbacks" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                for key in keys_to_download:
                    drive_key = self._to_drive_key(key)
                    data = drive.read_bytes(drive_key)
                    if data is None:
                        raise GoogleDriveSyncError("DRIVE_PENDING", f"File biến mất trong lúc restore: {key}")
                    target = self._to_local_target(store, key)
                    if target.exists():
                        backup_target = rollback / target.relative_to(store.project_root)
                        backup_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary_target = target.with_name(f".{target.name}.syncing")
                    temporary_target.write_bytes(data)
                    expected_entry = self._entries(remote_manifest).get(key, {})
                    if key == DATABASE_KEY:
                        actual_content_hash = _sqlite_content_hash(temporary_target)
                        expected_content_hash = expected_entry.get("content_sha256")
                        if expected_content_hash and actual_content_hash != expected_content_hash:
                            temporary_target.unlink(missing_ok=True)
                            raise GoogleDriveSyncError(
                                "ERROR",
                                f"Checksum logic (content_sha256) không khớp khi restore database: {actual_content_hash} != {expected_content_hash}",
                            )
                        expected_sha = expected_entry.get("sha256")
                        if expected_sha and not expected_content_hash and _hash_file(temporary_target) != expected_sha:
                            temporary_target.unlink(missing_ok=True)
                            raise GoogleDriveSyncError("ERROR", f"Checksum file (sha256) không khớp khi restore database: {key}")
                    else:
                        expected_sha = expected_entry.get("sha256")
                        if expected_sha and _hash_file(temporary_target) != expected_sha:
                            temporary_target.unlink(missing_ok=True)
                            raise GoogleDriveSyncError("ERROR", f"Checksum không khớp khi restore: {key}")
                    os.replace(temporary_target, target)
                if allow_deletions:
                    for key in comparison["remote_deletions"]:
                        target = self._to_local_target(store, key)
                        if target.exists():
                            backup_target = rollback / target.relative_to(store.project_root)
                            backup_target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(target, backup_target)
                            target.unlink()
                store.save_state(remote_manifest)
                return self._finish(store, "SYNCED", {**comparison, "downloaded": len(keys_to_download), "rollback": str(rollback)})


google_drive_sync_service = GoogleDriveSyncService()

