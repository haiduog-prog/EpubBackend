# Local Environment

> Tổng hợp kiến thức về môi trường chạy local độc lập với SQLite và local storage trong dự án.
> Cập nhật lần cuối: 2026-09-03

---

## Architecture

### Tách database khỏi thư mục public storage
- **Ngày**: 2026-09-03
- **Chi tiết**: SQLite local được lưu trong `data/`, còn `storage/` chỉ chứa các blob cần phục vụ. `/storage` chỉ được mount khi `STORAGE_PROVIDER=local` và `APP_ENV` thuộc profile local/dev/test; production hoặc cloud không được expose thư mục này.
- **Files liên quan**: `app/config.py`, `app/main.py`, `.gitignore`

### Một code path hỗ trợ PostgreSQL và SQLite
- **Ngày**: 2026-09-03
- **Chi tiết**: Application giữ chung repository/service layer. Khác biệt database được cô lập tại engine setup, Alembic batch operations và các đoạn PostgreSQL-specific như advisory lock. SQLite dùng foreign keys, WAL và busy timeout; PostgreSQL vẫn giữ lock toàn cục cho EPUB worker.
- **Files liên quan**: `app/db/session.py`, `app/modules/library/application/epub_build_worker.py`, `alembic/versions/`

### EpubBackend Studio Isolation & Conditional Mount
- **Ngày**: 2026-09-03
- **Chi tiết**: Mount giao diện UI `/studio` và router API `/api/v1/studio` có điều kiện khởi động: chỉ kích hoạt khi `STORAGE_PROVIDER=local` và `APP_ENV` thuộc profile local/dev/test. Thêm router guard `require_local_studio_env()` trả về HTTP 403 Forbidden nếu phát hiện truy cập trái phép. Ngăn chặn tuyệt đối việc phơi bày endpoint DDL/DML, SQL runner và quản trị filesystem trên production.
- **Files liên quan**: `app/main.py`, `app/api/v1/studio.py`

### Dynamic Database Engine Lookup
- **Ngày**: 2026-09-03
- **Chi tiết**: Các công cụ quản trị nội bộ không giữ tham chiếu tĩnh tới `engine`. Mọi thao tác truy vấn sử dụng helper `_get_engine()` trỏ động tới `db_session_module.engine` tại runtime, đảm bảo tự động phản chiếu database mới sau `reset_db_engine()` hoặc chuyển đổi cấu hình mà không cần restart server.
- **Files liên quan**: `app/api/v1/studio.py`, `app/db/session.py`

---

## Bugs & Solutions

### HTML Entity Decoding Trap trong Inline Event Handlers
- **Ngày**: 2026-09-03
- **Vấn đề**: Sử dụng `escapeHtml()` trong inline attribute như `<button onclick="action('${val}')">` vẫn bị XSS.
- **Root cause**: Trình duyệt tự động giải mã HTML entity (`&quot;`, `&#39;`) thành dấu nháy thật trước khi JavaScript parser chạy.
- **Fix**: Đưa toàn bộ biến động vào thuộc tính `data-*` (dataset) và dùng Event Delegation (`addEventListener`) trên container cha.
- **Files liên quan**: `app/static/studio.html`

### Studio UI mất quyền truy cập khi bật Auth
- **Ngày**: 2026-09-03
- **Vấn đề**: Khi cấu hình `AUTH_REQUIRED=true`, tất cả request từ Studio UI bị 401 Unauthorized do router được bảo vệ bởi `get_current_user`.
- **Root cause**: Các lời gọi `fetch` trong `studio.html` không gửi header `Authorization`.
- **Fix**: Tạo wrapper `studioFetch()` tự động đọc token từ `window.EpubAuth` hoặc `localStorage` và đính kèm `Authorization: Bearer <token>`.
- **Files liên quan**: `app/static/studio.html`

### Test Studio có thể xóa dữ liệu thật
- **Ngày**: 2026-09-03
- **Vấn đề**: Test Studio dùng chung storage root và database local thật; lệnh test xóa file đệ quy có thể xóa mất dữ liệu dev.
- **Root cause**: Test fixture không cô lập filesystem và database engine.
- **Fix**: Viết lại test dùng fixture `tmp_path`, engine SQLite tạm thời và dispose engine trong khối `finally`.
- **Files liên quan**: `tests/test_studio_api.py`

### SQLite path phụ thuộc working directory
- **Ngày**: 2026-09-03
- **Vấn đề**: URL SQLite tương đối có thể trỏ tới database khác khi lệnh được gọi từ thư mục con.
- **Root cause**: SQLAlchemy resolve đường dẫn tương đối theo current working directory.
- **Fix**: Resolve database path từ project root trước khi tạo engine; giữ nguyên memory database và SQLite file URI.
- **Files liên quan**: `app/db/session.py`

### Tạo thư mục làm sai thời điểm fail-fast
- **Ngày**: 2026-09-03
- **Vấn đề**: URL database không hợp lệ có thể ném `PermissionError` ngay khi reset engine, trước thao tác database mà test mong đợi.
- **Root cause**: Tự động tạo parent directory không xử lý lỗi hệ điều hành.
- **Fix**: Bọc `mkdir(parents=True, exist_ok=True)` bằng `try/except OSError`; để lỗi kết nối phát sinh tại thao tác database thực tế.
- **Files liên quan**: `app/db/session.py`, `tests/test_postgres_storage_integration.py`

### WAL làm hỏng SQLite read-only URI
- **Ngày**: 2026-09-03
- **Vấn đề**: Kết nối `mode=ro` thất bại với lỗi không thể ghi database.
- **Root cause**: Connection listener luôn chạy `PRAGMA journal_mode=WAL`, kể cả trên read-only connection.
- **Fix**: Vẫn bật busy timeout và foreign keys, nhưng coi WAL là tối ưu tùy chọn và bỏ qua `sqlite3.OperationalError` tại riêng pragma này.
- **Files liên quan**: `app/db/session.py`, `tests/test_local_environment.py`

### Worker giữ engine cũ sau reset
- **Ngày**: 2026-09-03
- **Vấn đề**: EPUB worker có thể tiếp tục dùng dialect/connection pool cũ sau `reset_db_engine()`.
- **Root cause**: Import biến `engine` trực tiếp sao chép reference tại thời điểm import module.
- **Fix**: Import module session và lấy `db_session_module.engine` tại thời điểm consumer chạy; chỉ dùng advisory lock khi engine hiện tại là PostgreSQL.
- **Files liên quan**: `app/modules/library/application/epub_build_worker.py`, `tests/test_local_environment.py`

### Local path resolver vượt ranh giới storage
- **Ngày**: 2026-09-03
- **Vấn đề**: Cơ chế fallback legacy có thể resolve file từ `data/`, bao gồm SQLite database.
- **Root cause**: Public resolver dùng danh sách candidate path rộng hơn storage root.
- **Fix**: `LocalStorageProvider.resolve_local_path()` chỉ dùng `_safe_path()` và chỉ trả về file tồn tại bên trong storage root.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `tests/test_local_environment.py`

---

## How-To

### Chạy backend local độc lập
- **Ngày**: 2026-09-03
- **Bước thực hiện**:
  1. Chạy `start_local.ps1` trên PowerShell hoặc `start_local.bat` trên Command Prompt.
  2. Script cấu hình SQLite trong `data/`, chọn local storage và bind server vào `127.0.0.1`.
  3. Dùng Alembic upgrade để bảo đảm schema đạt revision head trước khi thao tác dữ liệu.
- **Files liên quan**: `start_local.ps1`, `start_local.bat`, `alembic.ini`

### Xác minh thay đổi database local
- **Ngày**: 2026-09-03
- **Bước thực hiện**:
  1. Chạy test local và storage integration.
  2. Kiểm tra migration theo chu kỳ `base -> head -> base -> head` trên SQLite tạm.
  3. Chạy toàn bộ pytest suite và `git diff --check` trước khi commit.
- **Files liên quan**: `tests/test_local_environment.py`, `tests/test_postgres_storage_integration.py`

### Quản lý Database và Storage qua EpubBackend Studio
- **Ngày**: 2026-09-03
- **Bước thực hiện**:
  1. Khởi chạy app với profile local (`start_local.ps1` hoặc `start_local.bat`).
  2. Mở trình duyệt tại `http://localhost:8000/studio`.
  3. Quản lý schema bảng, lọc/sắp xếp dữ liệu, thực thi SQL trong console và duyệt/dọn dẹp các thư mục tệp trong `storage/`.
- **Files liên quan**: `app/api/v1/studio.py`, `app/static/studio.html`, `app/main.py`

---

## Patterns

### Giới hạn filesystem path tại provider boundary
- **Ngày**: 2026-09-03
- **Chi tiết**: Mọi object key từ request phải đi qua `_safe_key()`/`_safe_path()` của provider. API phía trên chỉ nhận path đã được provider xác nhận tồn tại; không tự nối đường dẫn hay tái sử dụng fallback legacy cho public file serving.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `app/modules/library/api.py`

### Test filesystem phải dùng thư mục tạm
- **Ngày**: 2026-09-03
- **Chi tiết**: Test static storage và SQLite tạo dữ liệu bằng `tmp_path`, mount một FastAPI app riêng và dùng `monkeypatch` cho settings/environment. Không ghi file cố định vào `storage/` vì có thể ghi đè hoặc xóa dữ liệu thật của developer.
- **Files liên quan**: `tests/test_local_environment.py`

### Path Traversal Guard và Safe Deletion trong Storage Explorer
- **Ngày**: 2026-09-03
- **Chi tiết**: Mọi thao tác explorer/xóa tệp phải chuẩn hóa đường dẫn và kiểm tra `target.relative_to(storage_root)`. Chặn tuyệt đối việc xóa thư mục gốc (`target == storage_root`). Lệnh gọi hệ điều hành (`explorer.exe`) chỉ truyền argument dạng list, `shell=False` để ngăn command injection.
- **Files liên quan**: `app/api/v1/studio.py`

