# Offline Sync Package

> Tổng hợp kiến thức về đóng gói dữ liệu ngoại tuyến (Database & Storage ZIP package) để đồng bộ giữa môi trường công ty và máy ở nhà.
> Cập nhật lần cuối: 2026-09-04

---

## Architecture

### Kiến trúc Đóng gói Đồng bộ Ngoại Tuyến (Offline Sync Package)
- **Ngày**: 2026-09-04
- **Chi tiết**: Cho phép đóng gói toàn bộ dữ liệu ứng dụng gồm cơ sở dữ liệu và thư mục lưu trữ (`storage/novels/`, `storage/uploads/`) thành 1 file ZIP duy nhất (~138MB). Giải quyết triệt để nhu cầu làm việc offline tại nhà với SQLite khi môi trường công ty chạy trên PostgreSQL Supabase và Cloud Storage.
- **Files liên quan**: `app/modules/sync/sync_package_service.py`, `app/modules/sync/api.py`, `app/static/index.html`

### Chuyển đổi Trực Tiếp PostgreSQL sang SQLite (On-The-Fly DB Dump)
- **Ngày**: 2026-09-04
- **Chi tiết**: Khi xuất gói từ hệ thống đang chạy PostgreSQL, `SyncPackageService` tự động khởi tạo hoặc sao chép schema SQLite vào staging `data/local_db.sqlite3`, sau đó trích xuất toàn bộ dữ liệu các bảng theo thứ tự ưu tiên (`novels`, `chapters`, `book_bibles`, `jobs`, `profile_*`, `reader_*`) và nạp vào SQLite trong ~10s. Đảm bảo máy ở nhà chạy `start_local.ps1` nhận đầy đủ 585 chương đã dịch mà không cần kết nối mạng.
- **Files liên quan**: `app/modules/sync/sync_package_service.py`

### Stream File Nén và Dọn Dẹp File Tạm Background
- **Ngày**: 2026-09-04
- **Chi tiết**: Endpoint `/api/v1/sync/export-package` dùng FastAPI `FileResponse` để stream file `.zip` lớn (~138MB) về client kèm header `Content-Disposition`. Sử dụng `BackgroundTasks` để tự động xóa file `.zip` và thư mục tạm `epub_sync_export_*` ngay sau khi response kết thúc, ngăn ngừa nguy cơ phình đĩa ổ cứng.
- **Files liên quan**: `app/modules/sync/api.py`

---

## Bugs & Solutions

### Ghi đè Hàng Loạt Timestamp "1 Phút Trước" trên ChapterModel
- **Ngày**: 2026-09-04
- **Vấn đề**: Sau khi dịch hoặc lưu truyện, toàn bộ 3.727 chương đồng loạt hiển thị "1 phút trước" (hoặc "31 phút trước"), làm mất mốc thời gian dịch thực tế của từng chương.
- **Root cause**:
  1. `ChapterModel.updated_at` có cấu hình `onupdate=lambda: datetime.now(timezone.utc)`, tự động nhảy giờ khi cập nhật bản ghi.
  2. Vòng lặp `save_novel` trong `legacy_repository.py` gán cứng `ch_model.updated_at = now` cho toàn bộ danh sách chương thay vì giữ nguyên timestamp của từng chương.
- **Fix**:
  1. Bỏ `onupdate` trên `ChapterModel.updated_at`.
  2. Trong `save_novel`, chỉ gán `updated_at` từ metadata chương nếu có hoặc giữ nguyên giá trị cũ trong DB.
  3. Khôi phục timestamp chuẩn xác cho các chương đã dịch từ ổ đĩa và bản lưu trữ EPUB r27.
- **Files liên quan**: `app/modules/library/persistence/legacy_models.py`, `app/modules/library/persistence/legacy_repository.py`

### Lỗ hổng Zip Slip khi Giải nén Gói Dữ Liệu
- **Ngày**: 2026-09-04
- **Vấn đề**: File ZIP tải lên khi Import có thể chứa các đường dẫn tương đối độc hại (vd `../../etc/passwd` hoặc vượt ngoài project root).
- **Root cause**: Trình giải nén mặc định nếu không kiểm tra `member.filename` sẽ giải nén file theo đường dẫn tương đối ghi trong zip header.
- **Fix**: Duyệt trước toàn bộ `zf.namelist()`, chuẩn hóa qua `os.path.normpath()`, kiểm tra `norm.startswith("..")`, `os.path.isabs()` hoặc chứa `..`, ném ngay `ValueError` nếu phát hiện đường dẫn không an toàn.
- **Files liên quan**: `app/modules/sync/sync_package_service.py`, `tests/test_sync_package.py`

---

## How-To

### Xuất Gói Dữ Liệu Đồng Bộ Về Máy Ở Nhà
- **Ngày**: 2026-09-04
- **Bước thực hiện**:
  1. Tại máy công ty, truy cập giao diện web `http://localhost:8000`.
  2. Bấm nút **Đồng Bộ Máy Ở Nhà** trên Header (hoặc nút **Xuất Gói ZIP** tại tab Dữ Liệu Đã Lưu).
  3. Kiểm tra thông số thống kê ước tính (số truyện, chương đã dịch, dung lượng tệp storage ~203MB, kích thước file ZIP ~138MB).
  4. Bấm **Tải Về Gói Đồng Bộ Ngay (.ZIP)**. Chờ ~15-20s để hệ thống kết xuất và lưu file zip về máy.
  5. Copy file zip vào USB hoặc lưu vào Cloud Drive để mang về nhà.
- **Files liên quan**: `app/static/index.html`, `app/modules/sync/api.py`

### Nhập Gói Dữ Liệu Đồng Bộ Tại Máy Ở Nhà
- **Ngày**: 2026-09-04
- **Bước thực hiện**:
  1. Tại máy ở nhà, khởi động backend bằng lệnh `.\start_local.ps1` (hoặc `start_local.bat`).
  2. Mở trình duyệt tại `http://localhost:8000`.
  3. Bấm **Đồng Bộ Máy Ở Nhà** ➔ chọn tab **Nhập Gói ZIP (Import)**.
  4. Kéo thả file `.zip` vào ô tải lên và bấm **Bắt Đầu Nạp Dữ Liệu Gói ZIP**.
  5. Chờ hệ thống giải nén vào `data/` và `storage/`. Sau khi hoàn tất, F5 trang để tiếp tục đọc và dịch ngoại tuyến.
- **Files liên quan**: `app/static/index.html`, `app/modules/sync/sync_package_service.py`

---

## Patterns

### Isolated Project Root Pattern trong Test Storage Isolation
- **Ngày**: 2026-09-04
- **Chi tiết**: Các service thao tác với filesystem và database file cần hỗ trợ tham số `project_root: Optional[Path] = None` (mặc định trỏ `PROJECT_ROOT`). Trong unit tests, truyền `tmp_path` làm `project_root` để test thoải mái tạo, xuất và giải nén file mà tuyệt đối không vi phạm luật Test Storage Isolation, bảo toàn 100% dữ liệu thật của người dùng.
- **Ví dụ code**:
  ```python
  @classmethod
  def export_sync_package(cls, include_db=True, include_storage=True, project_root: Optional[Path] = None) -> str:
      root = project_root or PROJECT_ROOT
      # thao tác an toàn bên trong root...
  ```
- **Files liên quan**: `app/modules/sync/sync_package_service.py`, `tests/test_sync_package.py`

### Dynamic Download Trigger & Vanilla Dropzone Pattern
- **Ngày**: 2026-09-04
- **Chi tiết**: Kỹ thuật tải file nhị phân qua REST API bằng cách chuyển `response.blob()` thành object URL tạm thời `window.URL.createObjectURL(blob)`, tạo thẻ `<a>` ảo với thuộc tính `download`, kích hoạt `.click()` và dọn dẹp bằng `URL.revokeObjectURL(url)`. Kết hợp vùng drag & drop thuần JavaScript (`dragenter`, `dragover`, `drop`) gắn trực tiếp vào `<input type="file">`.
- **Files liên quan**: `app/static/index.html`
