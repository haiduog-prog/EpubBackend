# Google Drive API sync backend

Backend expose các endpoint sync cho Android:

```text
GET  /api/v1/sync/status
POST /api/v1/sync/check
POST /api/v1/sync/backup
POST /api/v1/sync/restore
```

Các endpoint nằm trong `/api/v1` và bắt buộc Bearer authentication. `backup` và
`restore` chạy tuần tự trong một process lock, tạo SQLite snapshot an toàn,
đồng bộ incremental theo SHA-256 và dừng khi phát hiện conflict.

## Cấu hình backend

```env
GOOGLE_DRIVE_SYNC_ENABLED=true
GOOGLE_DRIVE_SYNC_FOLDER_ID=<id-thu-muc-EpubBackendSync>
GOOGLE_DRIVE_CREDENTIALS_FILE=/run/secrets/google-drive-credentials.json
```

Có thể dùng `GOOGLE_DRIVE_CREDENTIALS_JSON` thay cho file. Credential hỗ trợ
service account hoặc authorized-user JSON. Với service account, chia sẻ thư
mục Google Drive cho email của service account. Với OAuth user, cấp scope
`drive.file`; không đưa client secret hay refresh token vào APK.

`GOOGLE_DRIVE_PROJECT_ROOT` chỉ cần đặt khi backend không chạy ở thư mục gốc
của repository. Mặc định backend dùng thư mục gốc project hiện tại:

```text
storage/novels/  -> Drive/storage/novels/
storage/uploads/ -> Drive/storage/uploads/
data/local_db.sqlite3 -> Drive/database/local_db.sqlite3
```

Trạng thái local được lưu trong `data/.google_drive_sync_status.json` và
baseline trong `data/.google_drive_sync_state.json`; hai file này không được
upload lên Drive. `storage/cache` không nằm trong phạm vi sync.

## Android gọi API

```http
POST /api/v1/sync/check
Authorization: Bearer <supabase-access-token>
```

Body của `backup`/`restore`:

```json
{
  "allow_deletions": false,
  "force": false
}
```

HTTP `409` biểu thị `CONFLICT`, `CHANGES_PENDING` hoặc `DRIVE_PENDING`; Android
phải hiển thị cảnh báo và không tự retry `force`. HTTP `401` yêu cầu đăng nhập
lại. `force` chỉ được gửi sau khi người dùng xác nhận và backend luôn tạo
rollback trước khi ghi đè.
