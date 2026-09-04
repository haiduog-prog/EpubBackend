# Tech spec: Android đồng bộ dữ liệu local qua Google Drive API

## 1. Tóm tắt

Ứng dụng Android đồng bộ dữ liệu truyện giữa hai thiết bị Android thông qua một thư mục dùng chung trên Google Drive. Ứng dụng không dùng ổ đĩa `G:\` và không phụ thuộc Google Drive Desktop. Cơ chế đồng bộ sử dụng manifest, SHA-256, baseline cục bộ và snapshot SQLite để chỉ truyền dữ liệu thay đổi.

Mục tiêu của MVP là hỗ trợ một người dùng chuyển qua lại giữa hai máy, mỗi lần chỉ có một thiết bị thực hiện thay đổi. Không hỗ trợ chỉnh sửa đồng thời hoặc đồng bộ realtime.

## 2. Phạm vi

### Trong phạm vi

- Đăng nhập Google và cấp quyền tối thiểu cho Drive.
- Chọn hoặc tạo thư mục `EpubBackendSync` trên Drive.
- Đồng bộ `storage/novels`, `storage/uploads` và `database/local_db.sqlite3`.
- So sánh manifest và SHA-256 để upload/download incremental.
- Hiển thị tiến trình, trạng thái, lỗi và danh sách conflict.
- Restore máy mới và tạo rollback trước khi ghi đè local.
- Chạy thủ công và chạy nền qua WorkManager khi có mạng.

### Ngoài phạm vi

- Hai thiết bị cùng sửa dữ liệu realtime.
- Đồng bộ `storage/cache`.
- Đồng bộ nguyên database khi database đang được ghi.
- Chia sẻ thư mục cho nhiều tài khoản.
- Google Drive Desktop hoặc đường dẫn filesystem trên Android.

## 3. Kiến trúc đề xuất

```text
UI Compose
  -> SyncViewModel
      -> SyncCoordinator
          -> LocalSnapshotDataSource (Room/SQLite + files)
          -> ManifestComparator
          -> DriveSyncDataSource (Drive API v3)
          -> SyncStateStore (DataStore)
          -> RollbackStore
```

Các lớp không được gọi Drive API trực tiếp từ UI. `SyncCoordinator` là owner của một phiên sync; mỗi thời điểm chỉ cho phép một phiên chạy. Mọi thao tác file/database phải đi qua `LocalSnapshotDataSource` để bảo đảm snapshot nhất quán.

## 4. Google authentication và Drive API

### Quyền truy cập

Ưu tiên scope `https://www.googleapis.com/auth/drive.file`. App chỉ nhìn thấy các file/thư mục do app tạo hoặc người dùng chọn qua Google Picker/SAF tùy trải nghiệm sản phẩm. Nếu cần mở một thư mục sync đã tồn tại do backend tạo, phải dùng flow chọn thư mục và lưu `driveFolderId`; không lưu đường dẫn `G:\`.

Token chỉ lưu trong cơ chế credential an toàn của Google/Android. Không ghi access token vào manifest, log hoặc DataStore dạng plaintext. Khi token hết hạn, refresh hoặc yêu cầu đăng nhập lại; lỗi quyền phải chuyển thành `AUTH_REQUIRED`.

### Metadata trên Drive

Lưu `driveFolderId` gốc trong DataStore. Tạo hoặc tìm các thư mục con:

```text
EpubBackendSync/
├── database/
├── storage/novels/
├── storage/uploads/
├── manifest.json
└── sync-status.json
```

File được định danh bằng relative key POSIX, ví dụ `novels/book-1/chapter-01.html`. Android có thể cache Drive file ID theo key, nhưng mọi lần sync phải kiểm tra file còn thuộc đúng parent và không tin tuyệt đối vào cache.

## 5. Contract manifest

Android phải đọc/ghi `schema_version = 1` tương thích backend hiện tại:

```json
{
  "schema_version": 1,
  "created_at": "2026-09-04T10:00:00Z",
  "machine": "android-device-id",
  "storage": {
    "novels/book-1/chapter-01.html": {
      "size": 12345,
      "mtime_ns": 0,
      "sha256": "..."
    }
  },
  "database": {
    "size": 45678,
    "mtime_ns": 0,
    "sha256": "...",
    "content_sha256": "..."
  }
}
```

`mtime_ns` chỉ là metadata tham khảo trên Android. So sánh nội dung phải dựa trên SHA-256. Với database, `content_sha256` là fingerprint logic của snapshot SQLite; Android cần tạo snapshot ổn định rồi tính fingerprint theo cùng contract. Nếu chưa triển khai được fingerprint tương thích, MVP phải đánh dấu database là không tương thích thay vì tự ghi đè.

State cục bộ lưu trong DataStore, gồm manifest baseline cuối cùng đã xác nhận, `last_sync_at`, `last_sync_id`, `drive_folder_id` và phiên bản schema. Không đưa state này lên Drive.

## 6. Luồng backup/upload

1. Kiểm tra đăng nhập, quyền Drive, mạng và đảm bảo không có sync khác đang chạy.
2. Đóng transaction ghi dữ liệu hoặc tạo local snapshot tạm.
3. Tạo manifest local và hash từng file thuộc `novels`/`uploads`.
4. Tải manifest Drive mới nhất.
5. So sánh local với Drive và baseline local.
6. Nếu Drive có thay đổi từ sau baseline, dừng với `DRIVE_PENDING` hoặc `CONFLICT`.
7. Upload song song có giới hạn các file mới/thay đổi.
8. Upload database snapshot sau khi hoàn tất backup SQLite.
9. Ghi `manifest.json` bằng cơ chế versioned/atomic best-effort.
10. Cập nhật baseline local và hiển thị `SYNCED`.

Không upload file đang thay đổi. File lớn dùng resumable upload; retry theo exponential backoff và tôn trọng `Retry-After`.

## 7. Luồng restore/download

1. Kiểm tra quyền, mạng và khóa sync.
2. Tải manifest Drive và tạo snapshot/rollback local.
3. Nếu máy mới không có database và storage, bootstrap toàn bộ.
4. So sánh Drive, local và baseline để phát hiện thay đổi hai chiều.
5. Nếu có local change hoặc conflict, dừng và không ghi đè.
6. Download file thay đổi vào file tạm, kiểm tra SHA-256, sau đó rename atomic.
7. Restore database snapshot khi đã đóng database local.
8. Chỉ áp dụng xóa khi người dùng bật xác nhận `allowDeletions`.
9. Ghi baseline local và trạng thái `SYNCED`.

Rollback tối thiểu gồm các file bị ghi đè/xóa và database trước restore. Dọn rollback chỉ sau khi người dùng xác nhận sync thành công.

## 8. Conflict và trạng thái

Mỗi key được phân loại theo ba phiên bản: local hiện tại, Drive hiện tại và baseline cuối cùng. Nếu local và Drive cùng khác baseline thì conflict. Không tự chọn phiên bản.

Các trạng thái public:

| Trạng thái | Ý nghĩa | Hành động UI |
|---|---|---|
| `READY` | Chưa phát hiện thay đổi | Cho phép kiểm tra/sync |
| `CHANGES_PENDING` | Local có thay đổi chưa đẩy | Hiển thị số file upload |
| `SYNCING_UP` | Đang upload | Hiển thị progress |
| `SYNCING_DOWN` | Đang restore | Khóa thao tác chỉnh sửa |
| `SYNCED` | Hoàn tất | Hiển thị thời gian và summary |
| `DRIVE_PENDING` | Drive mới thay đổi/chưa tải xong | Chờ mạng rồi check/restore |
| `CONFLICT` | Hai phía cùng thay đổi | Hiển thị key, không tự ghi đè |
| `AUTH_REQUIRED` | Thiếu hoặc hết quyền | Yêu cầu đăng nhập/cấp quyền |
| `ERROR` | Lỗi không phân loại | Hiển thị retry và log |

`sync-status.json` trên Drive là thông tin tham khảo. Trạng thái hiển thị trong app phải lấy từ phiên sync local, không phụ thuộc việc ghi status lên Drive thành công.

## 9. API nội bộ Kotlin

```kotlin
interface SyncCoordinator {
    suspend fun check(): SyncCheckResult
    suspend fun backup(options: SyncOptions = SyncOptions()): SyncResult
    suspend fun restore(options: SyncOptions = SyncOptions()): SyncResult
    fun observeState(): Flow<SyncUiState>
}

data class SyncOptions(
    val allowDeletions: Boolean = false,
    val force: Boolean = false
)
```

`force` chỉ được gọi sau dialog xác nhận rõ ràng và luôn tạo rollback trước. Repository trả về domain errors thay vì ném raw HTTP/IO exception lên UI.

## 10. WorkManager và lifecycle

- Dùng `CoroutineWorker` cho sync nền.
- Constraint: `NetworkType.CONNECTED`; tùy chọn chỉ chạy Wi-Fi khi file lớn.
- Unique work `epub-sync`, policy `KEEP` để tránh chạy chồng.
- Foreground notification khi upload/download dài.
- Hủy worker phải giữ dữ liệu nguyên vẹn; file tạm có hậu tố `.syncing` và được dọn ở lần chạy sau.
- Không tự động restore/ghi đè dữ liệu khi app đang mở màn hình biên tập; chỉ check hoặc yêu cầu người dùng xác nhận.

## 11. Bảo mật và riêng tư

- Chỉ cấp quyền Drive tối thiểu và chỉ một thư mục sync.
- Không log nội dung chương, token, URL tải có chữ ký hoặc thông tin nhạy cảm.
- SHA-256 dùng để kiểm tra toàn vẹn, không phải mã hóa.
- Nếu truyện có nội dung riêng tư, cân nhắc mã hóa snapshot/file trước khi upload; đây là hạng mục sau MVP.
- Không tải file từ Drive nếu key chứa `..`, path tuyệt đối hoặc bucket ngoài `novels`/`uploads`.

## 12. Kiểm thử nghiệm thu

### Unit

- Hash file và so sánh manifest.
- Phân loại unchanged/upload/download/delete/conflict.
- Parse manifest sai schema, path traversal và checksum mismatch.
- Retry/backoff và mapping lỗi Drive.

### Integration

- Drive fake/test account: upload một file, chỉ file thay đổi được tải lại.
- Bootstrap thiết bị mới.
- Restore tạo rollback.
- Conflict không ghi đè local hoặc Drive.
- Mất mạng giữa upload/download có thể chạy lại an toàn.
- Token hết hạn yêu cầu xác thực lại.

### UI

- Hiển thị tiến trình và số lượng file.
- Nút sync bị khóa khi đang chạy.
- Dialog xác nhận xóa/force.
- Trạng thái lỗi có retry và hướng dẫn.

### Tiêu chí nghiệm thu MVP

- Hai Android device tuần tự chuyển dữ liệu qua cùng một Drive folder.
- File không thay đổi không bị truyền lại.
- Không mất dữ liệu khi phát hiện conflict, checksum lỗi hoặc mất mạng.
- Thiết bị mới restore được snapshot.
- Backend Windows hiện tại đọc được cấu trúc file do Android tạo.

## 13. Lộ trình triển khai

1. Tách `sync-domain` thuần Kotlin: model, manifest, comparator, status.
2. Tạo `DriveDataSource` với OAuth, folder ID, list/upload/download resumable.
3. Tạo `LocalSnapshotDataSource` và adapter cho database hiện tại.
4. Implement `check` trước, sau đó upload, restore và rollback.
5. Thêm ViewModel/Compose screen hiển thị trạng thái.
6. Thêm WorkManager và notification.
7. Chạy test integration với dữ liệu test riêng, không dùng `storage/` hoặc database thật.
8. Pilot trên hai thiết bị trước khi bật auto-sync.

## 14. Nhật ký quyết định

| Quyết định | Phương án thay thế | Lý do |
|---|---|---|
| Android gọi trực tiếp Drive API | Gọi backend Windows | Android hoạt động độc lập, không cần PC bật |
| Sync theo file incremental | ZIP snapshot | Tiết kiệm băng thông và thời gian |
| Giữ manifest/schema hiện tại | Thiết kế protocol mới | Tương thích backend đang có |
| Conflict thì dừng | Tự ưu tiên local/Drive | Tránh mất dữ liệu |
| SQLite là snapshot an toàn | Copy file DB đang mở | Tránh lỗi WAL và database hỏng |
| WorkManager cho nền | Service chạy liên tục | Phù hợp lifecycle và tiết kiệm pin |
