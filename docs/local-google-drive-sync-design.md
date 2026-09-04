# Kế hoạch đồng bộ dữ liệu local qua Google Drive Desktop

> Thiết kế cho trường hợp một người dùng luân phiên giữa hai máy Windows.
> Cập nhật lần cuối: 2026-09-03

## Mục tiêu

Cho phép chuyển dữ liệu truyện giữa hai máy bằng Google Drive Desktop mà không dùng Google Drive API, Supabase hoặc database cloud. Google Drive chỉ là nơi đồng bộ dữ liệu; backend vẫn chạy bằng SQLite và filesystem local.

## Tóm tắt hiểu biết

- Dữ liệu cần đồng bộ gồm `data/local_db.sqlite3`, `storage/novels/` và `storage/uploads/`.
- Người dùng chỉ chạy một máy tại một thời điểm.
- Server phải được tắt hoàn toàn trước khi backup hoặc restore.
- File truyện cần đồng bộ theo từng file để chỉ cập nhật phần thay đổi.
- SQLite cần được backup bằng cơ chế nhất quán, không copy trực tiếp khi đang ghi WAL.
- Mỗi máy có local baseline riêng để phân biệt lần restore đầu tiên với thao tác xóa dữ liệu.
- Script phải hiển thị trạng thái và phát hiện conflict trước khi ghi đè.
- `storage/cache/` và mã nguồn Git không thuộc dữ liệu đồng bộ.

## Giả định và non-goals

- Hai máy đều chạy Windows và đã cài Google Drive Desktop.
- Người dùng chờ Drive hoàn tất đồng bộ trước khi chuyển máy.
- Không hỗ trợ chạy đồng thời hai server hoặc merge dữ liệu hai chiều tự động.
- Không tích hợp OAuth/Google Drive API.
- Không đưa database hoặc dữ liệu truyện vào Git.

## Thiết kế cuối cùng

### Thư mục đồng bộ

```text
EpubBackendSync/
├── database/
│   └── local_db.sqlite3
├── storage/
│   ├── novels/
│   └── uploads/
├── manifest.json
└── sync-status.json
```

Ứng dụng tiếp tục dùng dữ liệu local trong project. Script chỉ sao chép dữ liệu giữa project và thư mục `EpubBackendSync` trên Google Drive.
Sau lần backup/restore thành công, mỗi máy lưu baseline cục bộ tại `data/.local_sync_state.json`; file này không đồng bộ qua Drive.

### Công cụ

- `scripts/local_sync.py`: core xử lý `backup`, `check`, `restore`, SQLite backup, SHA-256, manifest và conflict.
- `scripts/local_sync.ps1`: wrapper PowerShell để chạy thuận tiện trên Windows.
- `docs/local-google-drive-sync.md`: hướng dẫn cài đặt, đổi máy và xử lý lỗi.

Quy ước:

- `backup`: local → thư mục đồng bộ.
- `restore`: thư mục đồng bộ → local.
- `check`: chỉ đọc và báo chênh lệch, không sửa dữ liệu.
- Đường dẫn Google Drive nhận qua tham số `-SyncRoot` hoặc biến môi trường cấu hình; không hard-code ổ đĩa.

### Manifest và trạng thái

`manifest.json` lưu đường dẫn tương đối, kích thước, thời gian sửa đổi và SHA-256 của từng file; riêng database lưu checksum và metadata backup. `sync-status.json` lưu trạng thái cuối, máy thực hiện, thời gian, số file upload/download/unchanged, file lỗi và database checksum.

Các trạng thái công khai:

`READY`, `CHANGES_PENDING`, `CHECKING`, `SYNCING_UP`, `SYNCING_DOWN`, `SYNCED`, `DRIVE_PENDING`, `CONFLICT`, `ERROR`.

Script trả exit code `0` khi thành công hoặc không có thay đổi; trả exit code khác `0` khi có lỗi, conflict hoặc snapshot chưa ổn định.

### Luồng backup

1. Xác nhận server không còn chạy.
2. So sánh dữ liệu local với manifest gần nhất.
3. Chỉ copy file mới hoặc đã thay đổi trong `storage/`.
4. Tạo bản SQLite backup nhất quán vào `database/local_db.sqlite3`.
5. Ghi manifest tạm, kiểm tra checksum rồi đổi tên thành manifest hoàn chỉnh.
6. Ghi `SYNCED` hoặc trạng thái lỗi.

### Luồng restore

1. Kiểm tra Google Drive đã đồng bộ và file không còn thay đổi.
2. Chạy `check` để xác định file lệch và conflict.
3. Nếu máy chưa có local baseline và còn trống, bootstrap toàn bộ dữ liệu từ Drive.
4. Tạo rollback local trước khi thay đổi.
5. Chỉ copy file khác biệt từ thư mục đồng bộ về `storage/`.
6. Khôi phục database sau khi xác minh fingerprint.
7. Ghi baseline cục bộ và trạng thái `SYNCED`; nếu có vấn đề thì giữ rollback và báo `ERROR`.

### Conflict và an toàn dữ liệu

Nếu cùng một file khác baseline đã bị sửa ở cả hai máy, script dừng ở trạng thái `CONFLICT`, không tự ghi đè. Mọi restore phải tạo rollback có timestamp. Script không tự xóa file local hoặc file trên Drive nếu không có cờ xác nhận rõ ràng.

## Lộ trình triển khai

1. Viết module hash/manifest và mô hình trạng thái.
2. Viết SQLite backup/restore an toàn bằng Python.
3. Viết backup incremental cho `storage/` và phát hiện conflict.
4. Thêm PowerShell wrapper, exit code và thông báo terminal.
5. Viết test với `tmp_path`, không chạm dữ liệu thật.
6. Viết hướng dẫn Google Drive và smoke test trên hai máy.

## Kiểm thử và tiêu chí hoàn thành

- Backup database đang dùng WAL không mất dữ liệu.
- Một file thay đổi chỉ cập nhật đúng file đó.
- Checksum sai, Drive chưa ổn định và conflict đều bị chặn.
- Restore tạo rollback và khôi phục được database/storage.
- `storage/cache/` không bị đồng bộ.
- Test không ghi vào `data/local_db.sqlite3` hoặc `storage/` thật.
- Có thể đổi máy theo quy trình tắt server → backup/check → chờ Drive → restore → chạy server.

## Rủi ro còn lại

Script không đọc được trạng thái nội bộ chính xác của Google Drive Desktop; chỉ có thể kiểm tra kích thước file ổn định, manifest và checksum. Người dùng vẫn phải chờ Drive báo hoàn tất. SQLite không phù hợp cho hai máy ghi cùng lúc.

## Nhật ký quyết định

| Quyết định | Phương án thay thế | Lý do |
|---|---|---|
| Đồng bộ storage theo từng file | Tạo ZIP toàn bộ mỗi lần | Tiết kiệm thời gian và dung lượng khi chỉ vài file thay đổi |
| Backup SQLite thành một file riêng | Copy database đang chạy | Tránh database không nhất quán do WAL |
| App chạy trên dữ liệu local | Chạy trực tiếp trên thư mục Drive | Tránh đọc file khi Drive đang tải dở |
| Manifest có checksum từng file | Chỉ dựa vào modified time | Phát hiện được thay đổi dù timestamp không đáng tin |
| Dừng khi conflict | Tự động chọn bản mới hơn | Không làm mất dữ liệu người dùng |
| Trạng thái qua terminal và JSON | Thêm màn hình UI | Phù hợp phạm vi script, không mở rộng app không cần thiết |
