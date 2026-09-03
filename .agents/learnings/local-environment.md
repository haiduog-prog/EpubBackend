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

---

## Bugs & Solutions

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
