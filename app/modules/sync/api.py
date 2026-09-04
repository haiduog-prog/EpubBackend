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
