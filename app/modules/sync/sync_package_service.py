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
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import inspect, text

from app.config import settings
from app.db.session import PROJECT_ROOT, db_session, engine

logger = logging.getLogger("EpubBackend.SyncPackageService")

IGNORED_FILE_EXTS = {".tmp", ".syncing", ".lock"}
IGNORED_DIR_NAMES = {"__pycache__", ".git", "cache", "outputs"}
STORAGE_EXPORT_SUBDIRS = ("novels", "uploads")


class SyncPackageService:
    """Handles creating and restoring sync zip packages for offline machine migration."""

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
                    cls._package_storage_files(zf, project_root=root)

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
            # If local_db.sqlite3 exists, use its schema as template; otherwise generate schema
            if existing_sqlite.exists():
                shutil.copy2(str(existing_sqlite), target_sqlite)
            else:
                cls._init_sqlite_schema(target_sqlite)

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
    def _init_sqlite_schema(cls, sqlite_path: str) -> None:
        """Initializes schema on a fresh SQLite database using Alembic."""
        from alembic.config import Config
        from alembic import command

        alembic_cfg_path = os.path.join(PROJECT_ROOT, "alembic.ini")
        if os.path.exists(alembic_cfg_path):
            cfg = Config(alembic_cfg_path)
            posix_path = Path(sqlite_path).resolve().as_posix()
            cfg.set_main_option("sqlalchemy.url", f"sqlite:///{posix_path}")
            command.upgrade(cfg, "head")

    @classmethod
    def _dump_postgres_to_sqlite(cls, sqlite_path: str) -> None:
        """Transfers all rows from PostgreSQL into the target SQLite database."""
        inspector = inspect(engine)
        all_tables = [t for t in inspector.get_table_names() if not t.startswith("sqlite_")]

        # Prioritize core tables
        priority = ["alembic_version", "novels", "chapters", "book_bibles", "import_jobs",
                    "translation_jobs", "epub_build_jobs", "profile_books", "profile_editions",
                    "profile_chapter_mappings", "profile_events", "profile_evidence",
                    "profile_submissions", "reader_user_settings", "reader_progress"]
        ordered_tables = [t for t in priority if t in all_tables]
        for t in all_tables:
            if t not in ordered_tables:
                ordered_tables.append(t)

        with sqlite3.connect(sqlite_path) as con:
            con.execute("PRAGMA foreign_keys = OFF;")

            # Clean existing records in SQLite tables
            for tbl in ordered_tables:
                try:
                    con.execute(f'DELETE FROM "{tbl}";')
                except Exception:
                    pass
            con.commit()

            with db_session() as session:
                for tbl in ordered_tables:
                    try:
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

                        con.executemany(f'INSERT OR REPLACE INTO "{tbl}" ({col_str}) VALUES ({placeholders})', cleaned_rows)
                        con.commit()
                        logger.debug("Synced table %s: %d rows", tbl, len(cleaned_rows))
                    except Exception as err:
                        logger.warning("Error syncing table %s to SQLite: %s", tbl, err)

            con.execute("PRAGMA foreign_keys = ON;")

    @classmethod
    def _package_storage_files(
        cls,
        zf: zipfile.ZipFile,
        project_root: Optional[Path] = None,
    ) -> None:
        """Packages non-cache files from storage/ into the zip file."""
        root = project_root or PROJECT_ROOT
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

        temp_extract_dir = tempfile.mkdtemp(prefix="epub_sync_import_")
        try:
            with zipfile.ZipFile(zip_file_path, "r") as zf:
                # Security: Validate all paths
                for member in zf.namelist():
                    norm = os.path.normpath(member)
                    if (
                        norm.startswith("..")
                        or os.path.isabs(norm)
                        or "/../" in member
                        or "\\..\\" in member
                    ):
                        raise ValueError(f"Tệp nén chứa đường dẫn không an toàn: {member}")

                # Extract verified members
                for member in zf.namelist():
                    norm = os.path.normpath(member)
                    if not (norm.startswith("data") or norm.startswith("storage") or norm == "sync_manifest.json"):
                        continue

                    target_dest = root / norm
                    if member.endswith("/"):
                        target_dest.mkdir(parents=True, exist_ok=True)
                        continue

                    target_dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source_stream, open(target_dest, "wb") as dest_stream:
                        shutil.copyfileobj(source_stream, dest_stream)

                    extracted_files += 1
                    if norm == os.path.normpath("data/local_db.sqlite3"):
                        extracted_db = True

            # If active engine is PostgreSQL and user wants to sync into PostgreSQL:
            if extracted_db and restore_to_postgres_if_active and engine.dialect.name == "postgresql":
                sqlite_path = str(root / "data" / "local_db.sqlite3")
                restored_tables = cls._restore_sqlite_to_postgres(sqlite_path)

            return {
                "status": "success",
                "extracted_files_count": extracted_files,
                "storage_files_restored": extracted_files,
                "database_restored": extracted_db,
                "sqlite_db_restored": extracted_db,
                "postgres_restored_tables": restored_tables,
                "postgres_upsert_stats": restored_tables,
            }
        finally:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)

    @classmethod
    def _restore_sqlite_to_postgres(cls, sqlite_path: str) -> Dict[str, int]:
        """Optionally restores data from an extracted SQLite database into PostgreSQL."""
        if not os.path.exists(sqlite_path):
            return {}

        results: Dict[str, int] = {}
        inspector = inspect(engine)
        pg_tables = set(inspector.get_table_names())

        priority = ["alembic_version", "novels", "chapters", "book_bibles", "import_jobs",
                    "translation_jobs", "epub_build_jobs", "profile_books", "profile_editions",
                    "profile_chapter_mappings", "profile_events", "profile_evidence",
                    "profile_submissions", "reader_user_settings", "reader_progress"]

        with sqlite3.connect(sqlite_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            with db_session() as session:
                for tbl in priority:
                    if tbl not in pg_tables:
                        continue
                    try:
                        cur.execute(f'SELECT * FROM "{tbl}"')
                        rows = cur.fetchall()
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
                                update_clause = "UPDATE SET " + ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in update_cols])
                            else:
                                update_clause = "DO NOTHING"
                            sql = f'INSERT INTO "{tbl}" ({cols_escaped}) VALUES ({param_names}) ON CONFLICT ({conflict_target}) {update_clause}'
                        else:
                            sql = f'INSERT INTO "{tbl}" ({cols_escaped}) VALUES ({param_names})'

                        for r in rows:
                            row_dict = dict(r)
                            session.execute(text(sql), row_dict)

                        session.commit()
                        results[tbl] = len(rows)
                    except Exception as err:
                        session.rollback()
                        logger.warning("Failed restoring table %s into PostgreSQL: %s", tbl, err)

        return results
