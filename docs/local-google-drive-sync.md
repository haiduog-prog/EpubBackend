# Hướng dẫn đồng bộ local qua Google Drive Desktop

Tính năng này dành cho trường hợp một người dùng đổi qua lại giữa hai máy Windows. Google Drive Desktop chỉ đồng bộ dữ liệu; backend vẫn chạy trên dữ liệu local của từng máy.

## Chuẩn bị một lần

1. Cài Google Drive Desktop trên cả hai máy.
2. Tạo một thư mục được đồng bộ, ví dụ `E:\Google Drive\EpubBackendSync`.
3. Đảm bảo project có `data/`, `storage/novels/` và `storage/uploads/`.
4. Không chạy server trên thư mục Google Drive.

Có thể dùng PowerShell hoặc Python trực tiếp:

```powershell
.\scripts\local_sync.ps1 backup -SyncRoot "E:\Google Drive\EpubBackendSync"
```

```powershell
python scripts/local_sync.py check --sync-root "E:\Google Drive\EpubBackendSync"
```

Đường dẫn phải là thư mục Google Drive thực tế trên máy đó. Kiểm tra ổ đĩa trước khi chạy:

```powershell
Test-Path "G:\"
Get-PSDrive
```

Nếu `Test-Path` trả về `False` thì Google Drive Desktop chưa mount ổ `G:` hoặc đang dùng drive letter khác. Trong PowerShell dùng `G:\` (không cần viết `G:\\`); hãy thay bằng đường dẫn đúng, ví dụ `"G:\My Drive\EpubBackendSync"`. Script sẽ báo `DRIVE_PENDING` rõ ràng nếu ổ đã cấu hình không tồn tại.

Nếu không muốn truyền đường dẫn mỗi lần, đặt biến môi trường `EPUB_SYNC_ROOT` trong phiên PowerShell hiện tại:

```powershell
$env:EPUB_SYNC_ROOT = "E:\Google Drive\EpubBackendSync"
.\scripts\local_sync.ps1 check
```

## Khi rời máy đang dùng

1. Dừng Uvicorn/server hoàn toàn.
2. Chạy `backup`.
3. Chờ Google Drive báo đã đồng bộ xong.
4. Không sửa dữ liệu trong lúc Drive còn trạng thái pending.

`backup` chỉ cập nhật file mới hoặc đã thay đổi trong `storage/`. Database được backup riêng bằng SQLite online backup để không phụ thuộc trực tiếp vào file WAL đang ghi.
Sau backup thành công, máy local lưu baseline tại `data/.local_sync_state.json` để lần sau phát hiện đúng conflict/xóa file.

## Khi chuyển sang máy khác

1. Chờ Google Drive tải xong thư mục `EpubBackendSync`.
2. Chạy `check`.
3. Nếu trạng thái hợp lệ, chạy `restore`.
4. Khởi động server local.

Máy mới chưa có `data/local_db.sqlite3` và chưa có file trong `storage/` sẽ được bootstrap toàn bộ bằng `restore`.

```powershell
.\scripts\local_sync.ps1 check -SyncRoot "E:\Google Drive\EpubBackendSync"
.\scripts\local_sync.ps1 restore -SyncRoot "E:\Google Drive\EpubBackendSync"
```

## Trạng thái

- `READY`: không có thay đổi.
- `CHANGES_PENDING`: local có thay đổi chưa backup.
- `SYNCING_UP`: đang đẩy dữ liệu lên thư mục đồng bộ.
- `SYNCING_DOWN`: đang khôi phục dữ liệu về máy local.
- `SYNCED`: thao tác thành công.
- `DRIVE_PENDING`: Drive có thay đổi mới hoặc file chưa ổn định; cần kiểm tra/restore.
- `CONFLICT`: cùng file đã thay đổi ở cả hai máy; script không tự ghi đè.
- `ERROR`: lỗi database, checksum, quyền hoặc đường dẫn.

Kết quả chi tiết nằm trong `sync-status.json`; manifest baseline nằm trong `manifest.json`.

## Xóa dữ liệu

Mặc định script không tự xóa file. Nếu thực sự muốn truyền thao tác xóa sang hướng còn lại, dùng thêm `--allow-deletions` sau khi đã kiểm tra danh sách file:

```powershell
.\scripts\local_sync.ps1 backup -SyncRoot "E:\Google Drive\EpubBackendSync" -AllowDeletions
```

## Rollback và xử lý lỗi

Mỗi lần restore tạo rollback trong `data/sync-rollbacks/<timestamp>/`. Nếu restore lỗi, không xóa rollback. Với `CONFLICT`, giữ nguyên dữ liệu cả hai phía, sao lưu thủ công bản cần giữ rồi mới dùng `-Force` nếu muốn chọn dữ liệu từ Drive.

`storage/cache/` không được đồng bộ. Không chạy hai máy cùng lúc và không commit database hoặc dữ liệu truyện vào Git.
