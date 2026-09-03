import json
import logging
import math
import mimetypes
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text

from app.config import settings
import app.db.session as db_session_module
from app.db.session import PROJECT_ROOT

logger = logging.getLogger("EpubBackend.Studio")

LOCAL_APP_ENVS = {"development", "dev", "local", "test"}


def require_local_studio_env():
    """Chặn truy cập Studio nếu không phải môi trường local với storage local."""
    if (
        settings.storage_provider.lower() != "local"
        or settings.app_env.lower() not in LOCAL_APP_ENVS
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Studio is only available in local environment with local storage.",
        )


router = APIRouter(
    prefix="/studio",
    tags=["Studio"],
    dependencies=[Depends(require_local_studio_env)],
)


@router.get("/health")
def studio_health() -> Dict[str, str]:
    """Lightweight capability check used by the main UI to hide unavailable Studio links."""
    return {"status": "ok"}


def _get_engine():
    """Lấy engine động từ db_session_module để luôn cập nhật khi reset_db_engine()."""
    return db_session_module.engine


def _get_storage_root() -> Path:
    storage_dir = (PROJECT_ROOT / "storage").resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


def _get_db_file_path() -> Optional[Path]:
    db_url = settings.database_url
    if db_url.startswith("sqlite:///"):
        clean_path = db_url[len("sqlite:///"):]
        if clean_path.startswith("./") or clean_path.startswith(".\\"):
            return (PROJECT_ROOT / clean_path[2:]).resolve()
        if clean_path not in (":memory:", "") and not clean_path.startswith("file:"):
            return Path(clean_path).resolve()
    return None


def _get_valid_tables() -> Dict[str, List[Dict[str, Any]]]:
    """Lấy danh sách các bảng hợp lệ và schema cột tương ứng (hỗ trợ cả SQLite và PostgreSQL)."""
    inspector = inspect(_get_engine())
    table_names = sorted([t for t in inspector.get_table_names() if not t.startswith("sqlite_")])

    schema_map = {}
    for name in table_names:
        pk_constraint = inspector.get_pk_constraint(name)
        pk_cols = set(pk_constraint.get("constrained_columns", []) if pk_constraint else [])
        cols_info = inspector.get_columns(name)
        schema_map[name] = [
            {
                "cid": i,
                "name": col["name"],
                "type": str(col["type"]),
                "notnull": not bool(col.get("nullable", True)),
                "dflt_value": str(col.get("default", "")) if col.get("default") is not None else None,
                "pk": col["name"] in pk_cols,
            }
            for i, col in enumerate(cols_info)
        ]
    return schema_map


# ==============================================================================
# 1. OVERVIEW & METRICS
# ==============================================================================
@router.get("/overview")
def get_studio_overview() -> Dict[str, Any]:
    """Trả về thông tin tổng quan hệ thống: database, storage, tables."""
    db_file = _get_db_file_path()
    db_size_bytes = db_file.stat().st_size if db_file and db_file.exists() else 0

    storage_root = _get_storage_root()
    storage_size_bytes = sum(
        f.stat().st_size for f in storage_root.rglob("*") if f.is_file()
    )
    storage_file_count = sum(1 for f in storage_root.rglob("*") if f.is_file())

    table_schema = _get_valid_tables()
    total_records = 0
    table_stats = []

    with _get_engine().connect() as conn:
        for t_name, cols in table_schema.items():
            count = conn.execute(text(f'SELECT count(*) FROM "{t_name}";')).scalar() or 0
            total_records += count
            pk_col = next((c["name"] for c in cols if c["pk"]), None)
            table_stats.append({
                "name": t_name,
                "row_count": count,
                "column_count": len(cols),
                "primary_key": pk_col,
            })

    active_jobs = []
    try:
        from app.db.session import db_session
        from app.modules.library.persistence.legacy_repository import LibraryRepository
        with db_session() as session:
            jobs = LibraryRepository.get_active_build_jobs(session)
            active_jobs = [
                {
                    "job_id": j.job_id,
                    "novel_id": j.novel_id,
                    "status": j.status,
                    "strategy": j.strategy,
                    "progress_percentage": j.progress_percentage or 0,
                    "current_step": j.current_step or "",
                    "current_chapter": j.current_chapter,
                    "total_chapters": j.total_chapters or 0,
                    "processed_chapters": j.processed_chapters or 0,
                }
                for j in jobs
            ]
    except Exception as exc:
        logger.debug("Failed to fetch active jobs in overview: %s", exc)

    return {
        "app_env": settings.app_env,
        "database_backend": "sqlite",
        "database_file": str(db_file) if db_file else None,
        "database_size_bytes": db_size_bytes,
        "database_size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "storage_provider": settings.storage_provider,
        "storage_directory": str(storage_root),
        "storage_size_bytes": storage_size_bytes,
        "storage_size_mb": round(storage_size_bytes / (1024 * 1024), 2),
        "storage_file_count": storage_file_count,
        "total_tables": len(table_schema),
        "total_records": total_records,
        "tables": table_stats,
        "active_jobs": active_jobs,
    }


@router.get("/jobs/active")
def get_studio_active_jobs() -> List[Dict[str, Any]]:
    """Trả về danh sách tác vụ đang chạy ngầm hoặc xếp hàng."""
    try:
        from app.db.session import db_session
        from app.modules.library.persistence.legacy_repository import LibraryRepository
        with db_session() as session:
            jobs = LibraryRepository.get_active_build_jobs(session)
            return [
                {
                    "job_id": j.job_id,
                    "novel_id": j.novel_id,
                    "status": j.status,
                    "strategy": j.strategy,
                    "progress_percentage": j.progress_percentage or 0,
                    "current_step": j.current_step or "",
                    "current_chapter": j.current_chapter,
                    "total_chapters": j.total_chapters or 0,
                    "processed_chapters": j.processed_chapters or 0,
                }
                for j in jobs
            ]
    except Exception as exc:
        logger.debug("Failed to fetch active jobs: %s", exc)
        return []


# ==============================================================================
# 2. TABLES SCHEMA & DATA
# ==============================================================================
@router.get("/tables")
def list_tables() -> List[Dict[str, Any]]:
    """Trả về danh sách tất cả các bảng kèm cấu trúc cột và số lượng bản ghi."""
    tables_map = _get_valid_tables()
    results = []
    with _get_engine().connect() as conn:
        for t_name, columns in tables_map.items():
            count = conn.execute(text(f'SELECT count(*) FROM "{t_name}";')).scalar() or 0
            results.append({
                "name": t_name,
                "row_count": count,
                "columns": columns,
            })
    return results


@router.get("/tables/{table_name}/data")
def get_table_data(
    table_name: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None),
    sort_by: Optional[str] = Query(default=None),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    filter_col: Optional[str] = Query(default=None),
    filter_op: Optional[str] = Query(default="eq", pattern="^(eq|neq|contains|gt|lt|gte|lte|is_null|not_null)$"),
    filter_val: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """Truy vấn dữ liệu bảng có phân trang, tìm kiếm, lọc và sắp xếp an toàn."""
    tables_map = _get_valid_tables()
    if table_name not in tables_map:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bảng '{table_name}' không tồn tại trong cơ sở dữ liệu."
        )

    columns = tables_map[table_name]
    valid_col_names = {c["name"] for c in columns}

    where_clauses = []
    params: Dict[str, Any] = {"limit": limit, "offset": offset}

    # 1. Toàn văn Search
    if search and search.strip():
        search_terms = []
        text_cols = [c["name"] for c in columns if any(t in c["type"].upper() for t in ("CHAR", "TEXT", "CLOB", "JSON", "VARCHAR"))]
        if not text_cols:
            text_cols = [c["name"] for c in columns]
        for i, col in enumerate(text_cols):
            param_name = f"search_{i}"
            search_terms.append(f'CAST("{col}" AS TEXT) LIKE :{param_name}')
            params[param_name] = f"%{search.strip()}%"
        if search_terms:
            where_clauses.append(f"({' OR '.join(search_terms)})")

    # 2. Lọc theo cột (Filter)
    if filter_col and filter_col in valid_col_names:
        if filter_op == "is_null":
            where_clauses.append(f'"{filter_col}" IS NULL')
        elif filter_op == "not_null":
            where_clauses.append(f'"{filter_col}" IS NOT NULL')
        elif filter_val is not None:
            if filter_op == "eq":
                where_clauses.append(f'"{filter_col}" = :fval')
                params["fval"] = filter_val
            elif filter_op == "neq":
                where_clauses.append(f'"{filter_col}" != :fval')
                params["fval"] = filter_val
            elif filter_op == "contains":
                where_clauses.append(f'CAST("{filter_col}" AS TEXT) LIKE :fval')
                params["fval"] = f"%{filter_val}%"
            elif filter_op == "gt":
                where_clauses.append(f'"{filter_col}" > :fval')
                params["fval"] = filter_val
            elif filter_op == "lt":
                where_clauses.append(f'"{filter_col}" < :fval')
                params["fval"] = filter_val
            elif filter_op == "gte":
                where_clauses.append(f'"{filter_col}" >= :fval')
                params["fval"] = filter_val
            elif filter_op == "lte":
                where_clauses.append(f'"{filter_col}" <= :fval')
                params["fval"] = filter_val

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # 3. Sắp xếp (Sort)
    sort_sql = ""
    if sort_by and sort_by in valid_col_names:
        sort_dir = "ASC" if order.lower() == "asc" else "DESC"
        sort_sql = f'ORDER BY "{sort_by}" {sort_dir}'
    else:
        # Mặc định sort theo primary key nếu có
        pk_cols = [c["name"] for c in columns if c["pk"]]
        if pk_cols:
            sort_sql = f'ORDER BY {", ".join([f"\"{pk}\" DESC" for pk in pk_cols])}'

    start_time = time.perf_counter()
    with _get_engine().connect() as conn:
        # Đếm tổng số bản ghi thỏa điều kiện
        count_query = f'SELECT count(*) FROM "{table_name}" {where_sql};'
        total = conn.execute(text(count_query), params).scalar() or 0

        # Lấy dữ liệu trang
        data_query = f'SELECT * FROM "{table_name}" {where_sql} {sort_sql} LIMIT :limit OFFSET :offset;'
        cursor = conn.execute(text(data_query), params)
        keys = list(cursor.keys())
        raw_rows = cursor.fetchall()

    execution_ms = round((time.perf_counter() - start_time) * 1000, 2)

    rows = []
    for r in raw_rows:
        row_dict = {}
        for k, v in zip(keys, r):
            if isinstance(v, (dict, list)):
                row_dict[k] = v
            elif isinstance(v, str) and (v.startswith("{") or v.startswith("[")):
                try:
                    row_dict[k] = json.loads(v)
                except Exception:
                    row_dict[k] = v
            else:
                row_dict[k] = v
        rows.append(row_dict)

    return {
        "table": table_name,
        "columns": columns,
        "total": total,
        "limit": limit,
        "offset": offset,
        "page": (offset // limit) + 1,
        "total_pages": math.ceil(total / limit) if limit > 0 else 1,
        "execution_ms": execution_ms,
        "rows": rows,
    }


# ==============================================================================
# 3. SQL RUNNER
# ==============================================================================
class SqlQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=5000)
    limit: int = Field(default=100, ge=1, le=500)


@router.post("/sql")
def execute_sql_query(req: SqlQueryRequest) -> Dict[str, Any]:
    """Thực thi câu truy vấn SQL tùy biến và trả về kết quả."""
    raw_query = req.query.strip()
    # Loại bỏ dấu chấm phẩy cuối cùng nếu có
    if raw_query.endswith(";"):
        raw_query = raw_query[:-1].strip()

    first_word = raw_query.split()[0].upper() if raw_query else ""
    is_select = first_word in ("SELECT", "WITH", "PRAGMA", "EXPLAIN")

    start_time = time.perf_counter()
    try:
        with _get_engine().connect() as conn:
            if is_select:
                # Bọc limit nếu chưa có
                cursor = conn.execute(text(raw_query))
                keys = list(cursor.keys())
                raw_rows = cursor.fetchmany(req.limit)
                rows = [dict(zip(keys, r)) for r in raw_rows]
                execution_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return {
                    "success": True,
                    "is_query": True,
                    "columns": keys,
                    "rows": rows,
                    "row_count": len(rows),
                    "execution_ms": execution_ms,
                }
            else:
                # DML / DDL statement
                result = conn.execute(text(raw_query))
                conn.commit()
                execution_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return {
                    "success": True,
                    "is_query": False,
                    "rows_affected": result.rowcount,
                    "execution_ms": execution_ms,
                    "message": f"Thực thi thành công! Số dòng bị ảnh hưởng: {result.rowcount}",
                }
    except Exception as exc:
        execution_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.warning("SQL Runner error: %s", exc)
        return {
            "success": False,
            "error": str(exc),
            "execution_ms": execution_ms,
        }


# ==============================================================================
# 4. STORAGE EXPLORER
# ==============================================================================
@router.get("/storage/files")
def list_storage_files(sub_path: str = Query(default="")) -> Dict[str, Any]:
    """Duyệt danh sách các thư mục và file trong storage/ một cách an toàn."""
    storage_root = _get_storage_root()
    clean_sub = sub_path.strip().replace("\\", "/").strip("/")

    # Ngăn chặn path traversal
    target_dir = (storage_root / clean_sub).resolve()
    try:
        target_dir.relative_to(storage_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Đường dẫn không an toàn hoặc nằm ngoài thư mục storage.")

    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(status_code=404, detail="Thư mục không tồn tại.")

    def _calculate_dir_stats(dir_path: Path) -> tuple[int, int]:
        total_bytes = 0
        total_files = 0
        try:
            for p in dir_path.rglob("*"):
                if p.is_file():
                    total_bytes += p.stat().st_size
                    total_files += 1
        except (PermissionError, OSError):
            pass
        return total_bytes, total_files

    items = []
    total_folder_bytes = 0
    total_folder_files = 0

    for entry in sorted(target_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel_to_storage = entry.relative_to(storage_root).as_posix()
        stat = entry.stat()
        mime, _ = mimetypes.guess_type(entry.name)

        if entry.is_dir():
            size_bytes, file_count = _calculate_dir_stats(entry)
            size_formatted = _format_bytes(size_bytes)
            total_folder_bytes += size_bytes
            total_folder_files += file_count
        else:
            size_bytes = stat.st_size
            file_count = None
            size_formatted = _format_bytes(size_bytes)
            total_folder_bytes += size_bytes
            total_folder_files += 1

        item = {
            "name": entry.name,
            "path": rel_to_storage,
            "absolute_path": str(entry.resolve()),
            "is_dir": entry.is_dir(),
            "size_bytes": size_bytes,
            "size_formatted": size_formatted,
            "file_count": file_count,
            "modified_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "mime_type": mime or ("directory" if entry.is_dir() else "application/octet-stream"),
            "url": f"/storage/{rel_to_storage}" if not entry.is_dir() else None,
        }
        items.append(item)

    # Xây dựng breadcrumbs
    breadcrumbs = [{"name": "storage", "path": ""}]
    if clean_sub:
        parts = clean_sub.split("/")
        accum = []
        for p in parts:
            accum.append(p)
            breadcrumbs.append({"name": p, "path": "/".join(accum)})

    return {
        "current_path": clean_sub,
        "absolute_path": str(target_dir),
        "breadcrumbs": breadcrumbs,
        "items": items,
        "total_items": len(items),
        "total_size_bytes": total_folder_bytes,
        "total_size_formatted": _format_bytes(total_folder_bytes),
        "total_files_count": total_folder_files,
    }


class DeleteFileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    recursive: bool = Field(default=False, description="Xóa đệ quy toàn bộ thư mục và nội dung bên trong")


class OpenExplorerRequest(BaseModel):
    sub_path: Optional[str] = ""


@router.post("/storage/open-explorer")
def open_storage_in_explorer(req: Optional[OpenExplorerRequest] = None) -> Dict[str, Any]:
    """Mở thư mục hoặc tệp tin trong Windows File Explorer (chỉ khả dụng trong môi trường local)."""
    storage_root = _get_storage_root()
    sub = req.sub_path if (req and req.sub_path) else ""
    clean_sub = sub.strip().replace("\\", "/").strip("/")

    target = (storage_root / clean_sub).resolve() if clean_sub else storage_root.resolve()
    try:
        target.relative_to(storage_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ hoặc nằm ngoài thư mục storage.")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Đường dẫn không tồn tại trên ổ đĩa.")

    abs_str = str(target)

    import platform
    import subprocess

    try:
        system_name = platform.system()
        if system_name == "Windows":
            if target.is_file():
                subprocess.Popen(["explorer.exe", f"/select,{abs_str}"])
            else:
                subprocess.Popen(["explorer.exe", abs_str])
        elif system_name == "Darwin":
            subprocess.Popen(["open", abs_str])
        else:
            subprocess.Popen(["xdg-open", abs_str])

        return {
            "success": True,
            "path": abs_str,
            "message": f"Đã mở đường dẫn trong File Explorer: {abs_str}",
        }
    except Exception as exc:
        logger.error(f"Lỗi khi mở File Explorer: {exc}")
        raise HTTPException(status_code=500, detail=f"Không thể mở File Explorer: {exc}")


@router.delete("/storage/files")
def delete_storage_file(req: DeleteFileRequest) -> Dict[str, Any]:
    """Xóa an toàn một tệp hoặc thư mục (hỗ trợ xóa đệ quy) trong storage/."""
    storage_root = _get_storage_root()
    clean_sub = req.path.strip().replace("\\", "/").strip("/")

    if not clean_sub:
        raise HTTPException(status_code=400, detail="Không thể xóa thư mục gốc storage.")

    target = (storage_root / clean_sub).resolve()
    try:
        target.relative_to(storage_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ.")

    if target == storage_root:
        raise HTTPException(status_code=400, detail="Không thể xóa thư mục gốc storage.")

    if not target.exists():
        raise HTTPException(status_code=404, detail="Tệp tin hoặc thư mục không tồn tại.")

    try:
        if target.is_file():
            target.unlink()
            return {"success": True, "message": f"Đã xóa file '{clean_sub}' thành công."}
        elif target.is_dir():
            if req.recursive:
                shutil.rmtree(target)
                return {"success": True, "message": f"Đã xóa toàn bộ thư mục '{clean_sub}' và nội dung bên trong."}
            else:
                if any(target.iterdir()):
                    raise HTTPException(
                        status_code=400,
                        detail="Thư mục chưa rỗng. Hãy bật tùy chọn xóa đệ quy hoặc xóa các tệp bên trong trước."
                    )
                target.rmdir()
                return {"success": True, "message": f"Đã xóa thư mục '{clean_sub}' thành công."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Không thể xóa: {exc}")


def _format_bytes(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.2f} MB"
    return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"
