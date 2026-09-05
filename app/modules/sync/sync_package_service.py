"""
Sync Package Service (Database & Storage Export/Import)
========================================================
Exports and imports complete offline sync packages (.zip) containing:
1. data/ (SQLite database with all PostgreSQL rows synchronized, Google Drive sync metadata)
2. storage/ (novels, chapters, EPUBs, covers, Book Bibles, uploads)
Enables 100% offline data portability between office and home machines.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import inspect, text

from app.config import settings
from app.db.session import PROJECT_ROOT, db_session, engine

logger = logging.getLogger("EpubBackend.SyncPackageService")

IGNORED_FILE_EXTS = {".tmp", ".syncing", ".lock"}
IGNORED_DIR_NAMES = {"__pycache__", ".git", "cache", "outputs"}
STORAGE_EXPORT_SUBDIRS = ("novels", "uploads")
SYNC_METADATA_TABLES = {"alembic_version"}
SYNC_TABLE_PRIORITY = [
    "novels",
    "chapters",
    "book_bibles",
    "import_jobs",
    "translation_jobs",
    "epub_build_jobs",
    "profile_books",
    "profile_editions",
    "profile_chapter_mappings",
    "profile_events",
    "profile_evidence",
    "profile_submissions",
    "reader_user_settings",
    "reader_progress",
]


class SyncPackageService:
    """Handles creating and restoring sync zip packages for offline machine migration."""

    @staticmethod
    def _normalize_archive_member(member: str) -> Optional[str]:
        raw = str(member or "").replace("\\", "/")
        if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            raise ValueError(f"Tệp nén chứa đường dẫn không an toàn: {member}")

        path = PurePosixPath(raw)
        if any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"Tệp nén chứa đường dẫn không an toàn: {member}")

        normalized = "/".join(path.parts)
        if normalized == "sync_manifest.json":
            return normalized
        if not path.parts or path.parts[0] not in {"data", "storage"}:
            return None
        return normalized

    @classmethod
    def _validate_zip(cls, zip_file_path: str, zf: zipfile.ZipFile) -> List[Tuple[zipfile.ZipInfo, str]]:
        archive_size = os.path.getsize(zip_file_path)
        if archive_size > settings.max_sync_package_upload_bytes:
            raise ValueError(
                f"Sync package vượt quá giới hạn {settings.max_sync_package_upload_bytes // (1024 * 1024)} MB."
            )

        infos = zf.infolist()
        if len(infos) > settings.max_sync_package_entries:
            raise ValueError("Sync package chứa quá nhiều tệp.")

        total_uncompressed = 0
        normalized_members: List[Tuple[zipfile.ZipInfo, str]] = []
        seen: set[str] = set()
        for info in infos:
            normalized = cls._normalize_archive_member(info.filename)
            if normalized is None:
                continue
            if normalized in seen:
                raise ValueError(f"Sync package chứa tệp trùng lặp: {normalized}")
            seen.add(normalized)
            if not info.is_dir():
                if info.file_size > settings.max_sync_package_entry_bytes:
                    raise ValueError(f"Tệp trong sync package quá lớn: {normalized}")
                total_uncompressed += info.file_size
                if total_uncompressed > settings.max_sync_package_uncompressed_bytes:
                    raise ValueError("Tổng dung lượng giải nén của sync package vượt giới hạn.")
            normalized_members.append((info, normalized))

        manifest_info = next((info for info, name in normalized_members if name == "sync_manifest.json"), None)
        if manifest_info is not None:
            try:
                manifest = json.loads(zf.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                raise ValueError("sync_manifest.json không hợp lệ.") from exc
            if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
                raise ValueError("sync_manifest.json không tương thích.")
        return normalized_members

    @classmethod
    def _stage_zip_members(
        cls,
        zf: zipfile.ZipFile,
        members: List[Tuple[zipfile.ZipInfo, str]],
        staging_root: Path,
    ) -> List[Tuple[str, Path]]:
        staged: List[Tuple[str, Path]] = []
        for info, normalized in members:
            staged_path = staging_root / Path(*PurePosixPath(normalized).parts)
            if info.is_dir():
                staged_path.mkdir(parents=True, exist_ok=True)
                continue
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            copied = 0
            with zf.open(info) as source_stream, staged_path.open("wb") as destination_stream:
                while chunk := source_stream.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > settings.max_sync_package_entry_bytes:
                        raise ValueError(f"Tệp trong sync package quá lớn: {normalized}")
                    destination_stream.write(chunk)
            if copied != info.file_size:
                raise ValueError(f"Kích thước tệp không khớp trong sync package: {normalized}")
            staged.append((normalized, staged_path))
        return staged

    @classmethod
    def get_sync_estimate(cls, project_root: Optional[Path] = None) -> Dict[str, Any]:
        """Calculates estimated sizes and record counts for the sync package."""
        root = project_root or PROJECT_ROOT
        data_dir = root / "data"
        storage_dir = root / "storage"

        db_type = engine.dialect.name
        total_novels = 0
        total_chapters = 0
        completed_chapters = 0

        try:
            with db_session() as s:
                total_novels = s.execute(text("SELECT count(1) FROM novels")).scalar() or 0
                total_chapters = s.execute(text("SELECT count(1) FROM chapters")).scalar() or 0
                completed_chapters = s.execute(
                    text("SELECT count(1) FROM chapters WHERE status = 'completed'")
                ).scalar() or 0
        except Exception as e:
            logger.warning("Failed querying novel/chapter counts: %s", e)

        storage_bytes = 0
        file_count = 0
        for sub in STORAGE_EXPORT_SUBDIRS:
            p = storage_dir / sub
            if p.exists():
                for walk_root, dirs, files in os.walk(p):
                    dirs[:] = [d for d in dirs if d not in IGNORED_DIR_NAMES]
                    for f in files:
                        if not any(f.endswith(ext) for ext in IGNORED_FILE_EXTS):
                            fp = os.path.join(walk_root, f)
                            storage_bytes += os.path.getsize(fp)
                            file_count += 1

        db_bytes = 0
        if (data_dir / "local_db.sqlite3").exists():
            db_bytes = (data_dir / "local_db.sqlite3").stat().st_size
        elif db_type == "postgresql":
            db_bytes = max(5 * 1024 * 1024, total_chapters * 2 * 1024)

        return {
            "database_type": db_type,
            "total_novels": total_novels,
            "total_chapters": total_chapters,
            "completed_chapters": completed_chapters,
            "database_size_mb": round(db_bytes / (1024 * 1024), 2),
            "storage_files_count": file_count,
            "storage_size_mb": round(storage_bytes / (1024 * 1024), 2),
            "estimated_zip_size_mb": round((db_bytes + storage_bytes * 0.65) / (1024 * 1024), 2),
        }

    @classmethod
    def export_sync_package(
        cls,
        include_db: bool = True,
        include_storage: bool = True,
        project_root: Optional[Path] = None,
    ) -> str:
        """
        Creates a .zip package containing data/ and storage/ directories.
        Returns the absolute path to the generated temporary zip file.
        """
        root = project_root or PROJECT_ROOT
        t0 = time.time()
        temp_dir = tempfile.mkdtemp(prefix="epub_sync_export_")
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"epub_backend_sync_{timestamp_str}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)

        staging_data_dir = os.path.join(temp_dir, "data")
        os.makedirs(staging_data_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                # 1. Database packaging
                if include_db:
                    logger.info("Starting database packaging for sync...")
                    cls._prepare_database_for_export(staging_data_dir, zf, project_root=root)

                # 2. Storage packaging
                if include_storage:
                    logger.info("Starting storage packaging for sync...")
                    cls._package_storage_files(
                        zf,
                        project_root=root,
                        use_cloud_storage=project_root is None,
                    )

                # 3. Add package manifest
                manifest = {
                    "format_version": 1,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_dialect": engine.dialect.name,
                    "include_db": include_db,
                    "include_storage": include_storage,
                    "app_env": settings.app_env,
                }
                zf.writestr("sync_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

            logger.info("Sync package created in %.2fs at %s (size: %.2f MB)",
                        time.time() - t0, zip_path, os.path.getsize(zip_path) / (1024 * 1024))
            return zip_path

        except Exception:
            if os.path.exists(zip_path):
                try:
                    os.unlink(zip_path)
                except Exception:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @classmethod
    def _prepare_database_for_export(
        cls,
        staging_dir: str,
        zf: zipfile.ZipFile,
        project_root: Optional[Path] = None,
    ) -> None:
        """
        Prepares an up-to-date SQLite database:
        If active backend is PostgreSQL, pulls all current table rows from PostgreSQL into SQLite.
        If active backend is SQLite, copies local_db.sqlite3 after checkpointing.
        """
        root = project_root or PROJECT_ROOT
        data_dir = root / "data"
        existing_sqlite = data_dir / "local_db.sqlite3"
        target_sqlite = os.path.join(staging_dir, "local_db.sqlite3")

        is_postgres = engine.dialect.name == "postgresql"

        if is_postgres:
            logger.info("Exporting live PostgreSQL tables into SQLite database...")
            initialized = cls._init_sqlite_schema(target_sqlite)
            if not initialized and existing_sqlite.exists():
                shutil.copy2(str(existing_sqlite), target_sqlite)

            cls._dump_postgres_to_sqlite(target_sqlite)
        else:
            # Active database is already SQLite
            logger.info("Checkpointing active SQLite database...")
            if existing_sqlite.exists():
                try:
                    with sqlite3.connect(str(existing_sqlite)) as con:
                        con.execute("PRAGMA wal_checkpoint(FULL);")
                except Exception as ex:
                    logger.debug("SQLite wal_checkpoint skipped: %s", ex)
                shutil.copy2(str(existing_sqlite), target_sqlite)
            else:
                cls._init_sqlite_schema(target_sqlite)

        # Add target_sqlite to zip
        if os.path.exists(target_sqlite):
            zf.write(target_sqlite, arcname="data/local_db.sqlite3")

        # Copy any other metadata in data/ (e.g. .google_drive_sync_status.json)
        if data_dir.exists():
            for item in os.listdir(data_dir):
                if item == "local_db.sqlite3" or item.endswith("-wal") or item.endswith("-shm"):
                    continue
                fp = data_dir / item
                if fp.is_file() and not fp.name.endswith(".tmp"):
                    zf.write(str(fp), arcname=f"data/{item}")

    @classmethod
    def _init_sqlite_schema(cls, sqlite_path: str, project_root: Optional[Path] = None) -> bool:
        """Initializes schema on a fresh SQLite database using Alembic."""
        from alembic.config import Config
        from alembic import command

        root = project_root or PROJECT_ROOT
        alembic_cfg_path = os.path.join(root, "alembic.ini")
        if os.path.exists(alembic_cfg_path):
            cfg = Config(alembic_cfg_path)
            posix_path = Path(sqlite_path).resolve().as_posix()
            cfg.set_main_option("sqlalchemy.url", f"sqlite:///{posix_path}")
            command.upgrade(cfg, "head")
            return True
        return False

    @classmethod
    def _dump_postgres_to_sqlite(cls, sqlite_path: str) -> None:
        """Transfers all rows from PostgreSQL into the target SQLite database."""
        inspector = inspect(engine)
        all_tables = [t for t in inspector.get_table_names() if not t.startswith("sqlite_")]

        ordered_tables = [t for t in SYNC_TABLE_PRIORITY if t in all_tables]
        for t in all_tables:
            if t not in ordered_tables and t not in SYNC_METADATA_TABLES:
                ordered_tables.append(t)

        with sqlite3.connect(sqlite_path) as con:
            con.execute("PRAGMA foreign_keys = OFF;")
            sqlite_tables = {
                row[0]
                for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                if not row[0].startswith("sqlite_")
            }
            missing_tables = sorted(set(ordered_tables) - sqlite_tables)
            if missing_tables:
                raise ValueError(f"SQLite schema thiếu các bảng: {', '.join(missing_tables)}")

            for tbl in ordered_tables:
                con.execute(f'DELETE FROM "{tbl}";')

            with db_session() as session:
                for tbl in ordered_tables:
                    rows = session.execute(text(f'SELECT * FROM "{tbl}"')).fetchall()
                    if not rows:
                        continue

                    cols_info = inspector.get_columns(tbl)
                    cols = [c["name"] for c in cols_info]
                    placeholders = ",".join(["?"] * len(cols))
                    col_str = ",".join([f'"{c}"' for c in cols])
                    cleaned_rows = []
                    for row in rows:
                        r_dict = dict(row._mapping)
                        c_row = []
                        for col_name in cols:
                            val = r_dict.get(col_name)
                            if isinstance(val, (dict, list)):
                                val = json.dumps(val, ensure_ascii=False)
                            elif isinstance(val, (datetime, date)):
                                val = val.isoformat()
                            c_row.append(val)
                        cleaned_rows.append(c_row)

                    con.executemany(
                        f'INSERT OR REPLACE INTO "{tbl}" ({col_str}) VALUES ({placeholders})',
                        cleaned_rows,
                    )
                    logger.debug("Synced table %s: %d rows", tbl, len(cleaned_rows))

            con.execute("PRAGMA foreign_keys = ON;")

    @classmethod
    def _package_storage_files(
        cls,
        zf: zipfile.ZipFile,
        project_root: Optional[Path] = None,
        use_cloud_storage: bool = False,
    ) -> None:
        """Packages non-cache files from local or configured blob storage."""
        root = project_root or PROJECT_ROOT
        if use_cloud_storage:
            from app.infrastructure.storage.facade import storage_repo

            provider = storage_repo.active_provider
            if storage_repo.active_provider_name != "local":
                with tempfile.TemporaryDirectory(prefix="epub_sync_storage_") as staging:
                    staging_root = Path(staging)
                    for bucket in STORAGE_EXPORT_SUBDIRS:
                        for object_name in provider.list_files(f"{bucket}/", raise_on_error=True):
                            normalized = cls._normalize_storage_key(object_name)
                            staged_path = staging_root / Path(*PurePosixPath(normalized).parts)
                            staged_path.parent.mkdir(parents=True, exist_ok=True)
                            if not provider.download_file_stream(object_name, str(staged_path)):
                                raise RuntimeError(f"Không tải được object storage: {object_name}")
                            zf.write(staged_path, arcname=f"storage/{normalized}")
                return

        storage_root = root / "storage"
        if not storage_root.exists():
            return

        for sub in STORAGE_EXPORT_SUBDIRS:
            target_sub = storage_root / sub
            if not target_sub.exists():
                continue

            for walk_root, dirs, files in os.walk(target_sub):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIR_NAMES]
                for f in files:
                    if any(f.endswith(ext) for ext in IGNORED_FILE_EXTS):
                        continue
                    abs_path = os.path.join(walk_root, f)
                    rel_path = os.path.relpath(abs_path, str(root))
                    zf.write(abs_path, arcname=rel_path.replace("\\", "/"))

    @staticmethod
    def _normalize_storage_key(object_name: str) -> str:
        raw = str(object_name or "").replace("\\", "/")
        path = PurePosixPath(raw)
        if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"Storage object path không an toàn: {object_name}")
        if path.parts[0] not in STORAGE_EXPORT_SUBDIRS:
            raise ValueError(f"Storage object ngoài phạm vi sync: {object_name}")
        return "/".join(path.parts)

    @classmethod
    def import_sync_package(
        cls,
        zip_file_path: str,
        restore_to_postgres_if_active: bool = True,
        project_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Unpacks and applies a sync package to the local project.
        Guards against directory traversal (Zip Slip).
        """
        if not os.path.exists(zip_file_path):
            raise FileNotFoundError(f"Không tìm thấy file: {zip_file_path}")

        root = project_root or PROJECT_ROOT
        extracted_files = 0
        extracted_db = False
        restored_tables: Dict[str, int] = {}
        staged_files: List[Tuple[str, Path]] = []
        applied_files: List[Tuple[Path, Optional[Path]]] = []
        uploaded_storage_keys: List[str] = []
        temp_extract_dir = Path(tempfile.mkdtemp(prefix="epub_sync_import_"))
        try:
            with zipfile.ZipFile(zip_file_path, "r") as zf:
                members = cls._validate_zip(zip_file_path, zf)
                staged_files = cls._stage_zip_members(zf, members, temp_extract_dir)

            extracted_files = len(staged_files)
            extracted_db = any(name == "data/local_db.sqlite3" for name, _ in staged_files)
            storage_files = [(name, path) for name, path in staged_files if name.startswith("storage/")]
            local_files = [(name, path) for name, path in staged_files if not name.startswith("storage/")]

            if project_root is None:
                from app.infrastructure.storage.facade import storage_repo

                use_cloud_storage = storage_repo.active_provider_name != "local" and bool(storage_files)
            else:
                use_cloud_storage = False

            applied_files = cls._apply_staged_files(root, local_files, temp_extract_dir)
            if use_cloud_storage:
                uploaded_storage_keys = cls._upload_cloud_storage(storage_files)
            else:
                applied_files.extend(cls._apply_staged_files(root, storage_files, temp_extract_dir))

            if extracted_db and restore_to_postgres_if_active and engine.dialect.name == "postgresql":
                sqlite_path = str(root / "data" / "local_db.sqlite3")
                restored_tables = cls._restore_sqlite_to_postgres(sqlite_path)

            return {
                "status": "success",
                "extracted_files_count": extracted_files,
                "storage_files_restored": len(storage_files),
                "database_restored": extracted_db,
                "sqlite_db_restored": extracted_db,
                "postgres_restored_tables": restored_tables,
                "postgres_upsert_stats": restored_tables,
            }
        except zipfile.BadZipFile as exc:
            raise ValueError("Sync package không phải ZIP hợp lệ hoặc bị hỏng.") from exc
        except Exception:
            cls._rollback_staged_files(applied_files)
            if uploaded_storage_keys:
                cls._rollback_cloud_storage(uploaded_storage_keys)
            raise
        finally:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)

    @staticmethod
    def _target_path(root: Path, relative_path: str) -> Path:
        target = (root / Path(*PurePosixPath(relative_path).parts)).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"Sync target ngoài phạm vi cho phép: {relative_path}") from exc
        return target

    @classmethod
    def _apply_staged_files(
        cls,
        root: Path,
        staged_files: List[Tuple[str, Path]],
        staging_root: Path,
    ) -> List[Tuple[Path, Optional[Path]]]:
        applied: List[Tuple[Path, Optional[Path]]] = []
        try:
            for relative_path, source in staged_files:
                target = cls._target_path(root, relative_path)
                if target.exists() and target.is_dir():
                    raise ValueError(f"Sync target đã là thư mục: {relative_path}")
                backup = None
                if target.exists():
                    backup = staging_root / "rollback" / Path(*PurePosixPath(relative_path).parts)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_target = target.with_name(f".{target.name}.syncing")
                shutil.copy2(source, temporary_target)
                os.replace(temporary_target, target)
                applied.append((target, backup))
            return applied
        except Exception:
            cls._rollback_staged_files(applied)
            raise

    @staticmethod
    def _rollback_staged_files(applied: List[Tuple[Path, Optional[Path]]]) -> None:
        for target, backup in reversed(applied):
            try:
                if backup is not None and backup.exists():
                    temporary_target = target.with_name(f".{target.name}.rollback")
                    shutil.copy2(backup, temporary_target)
                    os.replace(temporary_target, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                logger.exception("Failed rolling back imported file %s", target)

    @staticmethod
    def _upload_cloud_storage(storage_files: List[Tuple[str, Path]]) -> List[str]:
        from app.infrastructure.storage.facade import storage_repo

        uploaded: List[str] = []
        try:
            for relative_path, source in storage_files:
                object_name = SyncPackageService._normalize_storage_key(
                    relative_path.removeprefix("storage/")
                )
                result = storage_repo.upload_file_stream(
                    str(source),
                    object_name,
                    content_type="application/octet-stream",
                )
                if not result and not storage_repo.file_exists(object_name):
                    raise RuntimeError(f"Không thể upload object storage: {object_name}")
                uploaded.append(object_name)
            return uploaded
        except Exception:
            SyncPackageService._rollback_cloud_storage(uploaded)
            raise

    @staticmethod
    def _rollback_cloud_storage(object_names: List[str]) -> None:
        try:
            from app.infrastructure.storage.facade import storage_repo

            storage_repo.delete_files(object_names)
        except Exception:
            logger.exception("Failed rolling back imported cloud storage objects")

    @classmethod
    def _restore_sqlite_to_postgres(cls, sqlite_path: str) -> Dict[str, int]:
        """Optionally restores data from an extracted SQLite database into PostgreSQL."""
        if not os.path.exists(sqlite_path):
            return {}

        results: Dict[str, int] = {}
        inspector = inspect(engine)
        pg_tables = set(inspector.get_table_names())

        with sqlite3.connect(sqlite_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            sqlite_tables = {
                row[0]
                for row in cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                if not row[0].startswith("sqlite_")
            }
            app_sqlite_tables = sqlite_tables - SYNC_METADATA_TABLES
            missing_tables = sorted(app_sqlite_tables - pg_tables)
            if missing_tables:
                raise ValueError(
                    f"SQLite package chứa các bảng không có trên PostgreSQL: {', '.join(missing_tables)}"
                )
            missing_package_tables = sorted((pg_tables - SYNC_METADATA_TABLES) - app_sqlite_tables)
            if missing_package_tables:
                raise ValueError(
                    f"SQLite package thiếu các bảng PostgreSQL: {', '.join(missing_package_tables)}"
                )
            ordered_tables = [t for t in SYNC_TABLE_PRIORITY if t in app_sqlite_tables]
            ordered_tables.extend(sorted(app_sqlite_tables - set(ordered_tables)))

            with db_session() as session:
                for tbl in ordered_tables:
                    cur.execute(f'SELECT * FROM "{tbl}"')
                    rows = cur.fetchall()
                    results[tbl] = len(rows)
                    if not rows:
                        continue

                    cols = [d[0] for d in cur.description]
                    cols_escaped = ",".join([f'"{c}"' for c in cols])
                    param_names = ",".join([f":{c}" for c in cols])

                    pk_cols = inspector.get_pk_constraint(tbl).get("constrained_columns", [])
                    if pk_cols:
                        conflict_target = ",".join([f'"{c}"' for c in pk_cols])
                        update_cols = [c for c in cols if c not in pk_cols]
                        if update_cols:
                            update_clause = "UPDATE SET " + ", ".join(
                                [f'"{c}" = EXCLUDED."{c}"' for c in update_cols]
                            )
                        else:
                            update_clause = "DO NOTHING"
                        sql = (
                            f'INSERT INTO "{tbl}" ({cols_escaped}) VALUES ({param_names}) '
                            f'ON CONFLICT ({conflict_target}) {update_clause}'
                        )
                    else:
                        sql = f'INSERT INTO "{tbl}" ({cols_escaped}) VALUES ({param_names})'

                    for row in rows:
                        session.execute(text(sql), dict(row))

                session.commit()

        return results
