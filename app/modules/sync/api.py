from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.modules.sync.google_drive_sync import GoogleDriveSyncError, google_drive_sync_service


router = APIRouter(
    prefix="/sync",
    tags=["Google Drive Sync"],
    dependencies=[Depends(get_current_user)],
)


class SyncOptions(BaseModel):
    allow_deletions: bool = False
    force: bool = False


def _run(operation):
    try:
        return operation()
    except GoogleDriveSyncError as exc:
        code = {
            "AUTH_REQUIRED": 401,
            "QUOTA_EXCEEDED": 403,
            "NOT_FOUND": 404,
            "CONFLICT": 409,
            "CHANGES_PENDING": 409,
            "DRIVE_PENDING": 409,
        }.get(exc.status, 503)
        detail = {"status": exc.status, "message": str(exc), **exc.details}
        raise HTTPException(status_code=code, detail=detail) from exc
    except Exception as exc:
        err_msg = str(exc)
        if "storageQuotaExceeded" in err_msg or "Service Accounts do not have storage quota" in err_msg:
            raise HTTPException(
                status_code=403,
                detail={
                    "status": "QUOTA_EXCEEDED",
                    "message": "Tài khoản Service Account không có dung lượng lưu trữ trên Google Drive cá nhân. Vui lòng dùng OAuth 2.0 (tài khoản cá nhân) hoặc Shared Drive.",
                    "reason": "storageQuotaExceeded",
                },
            ) from exc
        raise HTTPException(
            status_code=500,
            detail={"status": "ERROR", "message": f"Lỗi hệ thống đồng bộ: {exc}"},
        ) from exc


@router.get("/status")
def get_sync_status():
    return _run(google_drive_sync_service.status)


@router.post("/check")
def check_sync():
    return _run(google_drive_sync_service.check)


@router.post("/backup")
def backup_sync(options: SyncOptions = SyncOptions()):
    return _run(lambda: google_drive_sync_service.backup(options.allow_deletions))


@router.post("/restore")
def restore_sync(options: SyncOptions = SyncOptions()):
    return _run(lambda: google_drive_sync_service.restore(options.allow_deletions, options.force))


# ==============================================================================
# OFFLINE SYNC PACKAGE (ZIP EXPORT & IMPORT FOR HOME MACHINE)
# ==============================================================================
import os
import shutil
import tempfile
from fastapi import BackgroundTasks, File, UploadFile
from fastapi.responses import FileResponse
from app.modules.sync.sync_package_service import SyncPackageService


def _cleanup_temp_file(filepath: str) -> None:
    try:
        if os.path.exists(filepath):
            os.unlink(filepath)
        parent = os.path.dirname(filepath)
        if os.path.exists(parent) and "epub_sync_export_" in os.path.basename(parent):
            shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        pass


@router.get("/estimate")
def get_sync_estimate_endpoint():
    """Returns estimated counts and byte sizes of database and storage for export."""
    try:
        return SyncPackageService.get_sync_estimate()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi tính toán dung lượng: {exc}") from exc


@router.get("/export-package")
def export_sync_package_endpoint(
    background_tasks: BackgroundTasks,
    include_db: bool = True,
    include_storage: bool = True,
):
    """Generates and downloads a complete offline sync package (.zip) for home machine sync."""
    try:
        zip_path = SyncPackageService.export_sync_package(
            include_db=include_db,
            include_storage=include_storage,
        )
        filename = os.path.basename(zip_path)
        background_tasks.add_task(_cleanup_temp_file, zip_path)
        return FileResponse(
            path=zip_path,
            filename=filename,
            media_type="application/zip",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi đóng gói dữ liệu: {exc}") from exc


@router.post("/import-package")
async def import_sync_package_endpoint(
    file: UploadFile = File(...),
    restore_to_postgres: bool = True,
):
    """Unpacks an uploaded sync package (.zip) into the project's data/ and storage/."""
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Vui lòng tải lên tệp nén định dạng .zip")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        temp_zip_path = tmp.name

    try:
        with open(temp_zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        result = SyncPackageService.import_sync_package(
            temp_zip_path,
            restore_to_postgres_if_active=restore_to_postgres,
        )
        return result
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err)) from val_err
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi giải nén và nạp dữ liệu: {exc}") from exc
    finally:
        if os.path.exists(temp_zip_path):
            try:
                os.unlink(temp_zip_path)
            except Exception:
                pass
