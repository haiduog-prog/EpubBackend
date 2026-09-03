# Kế hoạch triển khai môi trường local độc lập

## 1. Mục tiêu

Thiết lập môi trường phát triển và vận hành cục bộ trên Windows mà không cần Docker, PostgreSQL Server, Render, Supabase, Cloudflare R2 hoặc Firebase.

Ứng dụng tiếp tục dùng FastAPI, SQLAlchemy, Alembic và các service hiện có. Hạ tầng local gồm SQLite cho dữ liệu quan hệ và filesystem cho blob. Chức năng dịch bằng Gemini vẫn cần kết nối Internet; vì vậy, “độc lập” trong tài liệu này có nghĩa là độc lập về database, storage và hosting, không phải offline hoàn toàn.

## 2. Phạm vi và giả định

- Dành cho một developer trên một máy Windows.
- Chỉ chạy một Uvicorn application process.
- Server luôn bind `127.0.0.1`; không phục vụ LAN hoặc Internet.
- Không hỗ trợ nhiều process cùng ghi vào SQLite.
- Không đổi API contract hiện tại.
- Không đổi tên các giá trị `STRUCTURED_STORAGE_BACKEND=postgres` và `STRUCTURED_STORAGE_READ_SOURCE=postgres` trong đợt này. Trong code hiện tại, `postgres` biểu thị SQLAlchemy/database repository và vẫn có thể dùng SQLite.
- Không tự động cài dependency, reset database hoặc gọi dịch vụ cloud trong script khởi động.

## 3. Bố cục dữ liệu local

```text
EpubBackend/
├── data/
│   └── local_db.sqlite3
├── storage/
│   ├── novels/
│   └── outputs/
├── start_local.ps1
└── start_local.bat
```

Database phải nằm ngoài thư mục được mount static. `data/local_db.sqlite3` không được truy cập qua `/storage/...`.

## 4. Cấu hình local

Các script khởi động gán cấu hình sau cho riêng process hiện tại:

```env
APP_ENV=local
AUTH_REQUIRED=false
DATABASE_URL=sqlite:///./data/local_db.sqlite3
STORAGE_PROVIDER=local
STRUCTURED_STORAGE_BACKEND=postgres
STRUCTURED_STORAGE_READ_SOURCE=postgres
```

Không ghi đè `.env`. Các giá trị có sẵn như `GEMINI_API_KEY` tiếp tục được nạp từ `.env`; biến được script đặt trước phải có độ ưu tiên cao hơn `load_dotenv()`.

Đồng thời đổi fallback `DATABASE_URL` trong `app/config.py` từ `sqlite:///./storage/local_db.sqlite3` thành `sqlite:///./data/local_db.sqlite3`, để lệnh và test chạy không qua script vẫn không đặt database trong static storage.

## 5. Thay đổi theo thành phần

### 5.1 Database session

Sửa `app/db/session.py`:

- Tạo thư mục cha của SQLite database trước khi tạo engine.
- Dùng SQLAlchemy URL parser để xử lý đúng đường dẫn tương đối, đường dẫn tuyệt đối, Windows path, URI và memory database.
- Với SQLite file URL tương đối, resolve đường dẫn từ project root xác định bởi vị trí `app/db/session.py`, không dựa vào current working directory.
- Không biến đổi `:memory:` hoặc SQLite URI dạng `file:...`; shared in-memory database dùng trong test phải tiếp tục hoạt động.
- Việc tạo thư mục cha là best-effort để giữ đặc tính lazy của `create_engine()` và `reset_db_engine()`:

```python
try:
    parent_dir.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
```

  Chỉ bỏ qua `OSError` ở bước chuẩn bị thư mục. Không được nuốt lỗi khi SQLAlchemy thực sự connect, đọc hoặc ghi database; các lỗi đó phải tiếp tục truyền ra caller.
- Giữ `check_same_thread=False`.
- Đăng ký connection event cho SQLite:

```sql
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

- Không áp dụng SQLite PRAGMA cho PostgreSQL.

### 5.2 Alembic

Rà và hoàn thiện các migration:

- `ab12cd34ef56_add_chapter_semantic_review.py`
- `c1d2e3f4a5b6_epub_fast_patch_and_build_jobs.py`
- `d2e3f4a5b6c7_add_layout_and_claimed_dirty.py`
- `f4a5b6c7d8e9_add_epub_build_job_progress_columns.py`

Các thao tác SQLite không hỗ trợ trực tiếp, đặc biệt `alter_column`, phải chạy trong `op.batch_alter_table(...)`. Giữ tương thích PostgreSQL và không làm thay đổi revision ID hoặc chuỗi `down_revision`.

Các file trên đang có thay đổi chưa commit trong worktree và đã dùng `op.batch_alter_table(...)`. Theo kết quả xác minh được cung cấp, trạng thái hiện tại đã chạy thành công chu kỳ `base -> head -> base -> head`. Khi triển khai phải giữ nguyên nền tảng này, không khôi phục hoặc ghi đè; chu kỳ migration vẫn được chạy lại ở regression gate cuối.

### 5.3 Git ignore cho dữ liệu local

Giữ quy tắc `data/` hiện có và bổ sung tường minh:

```gitignore
data/*.sqlite3
data/*.sqlite3-wal
data/*.sqlite3-shm
```

Ba pattern này mang tính tài liệu hóa khi `data/` đang được ignore toàn bộ, đồng thời bảo vệ ý định nếu quy tắc tổng quát được nới lỏng sau này.

### 5.4 Chọn storage provider

Sửa `app/infrastructure/storage/legacy_storage.py`:

- Nếu `STORAGE_PROVIDER=local`, `active_provider` phải trả `local_provider` ngay cả khi máy vẫn còn credential Supabase/R2.
- Không tự động đổi nghĩa `is_blob_active` từ “cloud blob hoạt động” thành “bất kỳ provider nào hoạt động”.
- Nếu cần kiểm tra provider tổng quát, thêm thuộc tính có tên rõ nghĩa như `is_storage_active` và cập nhật từng call site có chủ đích.
- Giữ kiểm tra path traversal của `LocalStorageProvider`.
- Bổ sung API public an toàn, ví dụ `resolve_local_path(object_name)`, để endpoint lấy đường dẫn file local mà không gọi trực tiếp `_safe_path()`.

### 5.5 Static storage

Sửa `app/main.py` để mount:

```text
/storage -> <project>/storage
```

Đường dẫn filesystem phải được tính ổn định từ project root hoặc cấu hình, không phụ thuộc tùy ý vào current working directory. Database nằm trong `data/`, nên không thuộc static mount.

### 5.6 Export EPUB

Sửa `app/modules/library/api.py`:

- Với request không `force_rebuild`, không chọn chapter range và local EPUB đã tồn tại, trả file cache bằng `FileResponse`.
- Resolve file thông qua API an toàn của local provider.
- Giữ `media_type=application/epub+zip` và `Content-Disposition` hỗ trợ UTF-8.
- Không đọc toàn bộ EPUB vào RAM.
- Không redirect qua CDN trong nhánh local.
- Nếu cache không tồn tại, giữ nguyên luồng `build_and_publish_epub`.

### 5.7 EPUB build worker

Sửa `app/modules/library/application/epub_build_worker.py` theo dialect:

```text
PostgreSQL
  acquire advisory lock
  claim/process job
  release advisory lock

SQLite
  claim/process job trực tiếp
```

SQLite không tạo connection `AUTOCOMMIT` chỉ để thử câu SQL PostgreSQL. Giữ nguyên lease, heartbeat, retry, cancellation và cập nhật progress. Thiết kế dựa trên ràng buộc một application process.

### 5.8 Script khởi động

Tạo `start_local.ps1` và `start_local.bat` với hành vi tương đương:

1. Chuyển working directory về thư mục chứa script.
2. Tạo `data/` và `storage/` nếu thiếu.
3. Gán biến môi trường local cho process.
4. Ưu tiên `.venv/Scripts/python.exe` nếu tồn tại; nếu không, dùng `python` từ `PATH`.
5. Chạy `python -m alembic upgrade head`.
6. Dừng ngay và giữ exit code khác `0` nếu migration thất bại.
7. Chạy `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`.

Script không được tự động cài package, xóa database hoặc fallback sang cloud.

## 6. Xử lý lỗi và trường hợp biên

- Thư mục database không tạo được: không làm `reset_db_engine()` mất đặc tính lazy; thao tác database đầu tiên phải phát sinh lỗi và lỗi không được bị repository/service nuốt.
- Migration thất bại: không khởi động server.
- Port 8000 đang được dùng: để Uvicorn trả lỗi và exit; không tự chọn port khác vì sẽ làm URL local thiếu ổn định.
- File cache EPUB không còn tồn tại: build lại theo luồng hiện tại.
- Object key tuyệt đối hoặc chứa `..`: từ chối trước khi truy cập filesystem.
- SQLite tạm thời bị khóa: `busy_timeout` chờ hữu hạn; lỗi còn lại phải được log, không retry vô hạn.
- Credential cloud vẫn có trong `.env`: `STORAGE_PROVIDER=local` vẫn luôn chọn local provider.

## 7. Kiểm thử tự động

### 7.1 Migration SQLite

Dùng database tạm, không dùng `data/local_db.sqlite3` thật:

```text
base -> head -> base -> head
```

Xác minh revision cuối, bảng/cột/index chính và foreign-key integrity.

### 7.2 Database runtime

- Mỗi SQLite connection bật foreign key.
- Cascade delete hoạt động thật.
- WAL và busy timeout được cấu hình.
- Một kịch bản đọc/ghi đồng thời cơ bản không lập tức lỗi `database is locked`.
- SQLite relative path luôn resolve từ project root; memory database và URI `file:...` không bị chuyển thành file path thông thường.
- `reset_db_engine("sqlite:////nonexistent/invalid_path/test.db")` không lỗi ngay lúc tạo engine, nhưng thao tác ghi đầu tiên phải phát sinh exception như hợp đồng trong `test_fail_fast_on_database_error`.

### 7.3 Storage

- `STORAGE_PROVIDER=local` thắng khi Supabase/R2 cũng được cấu hình.
- Put/get/list/delete local hoạt động.
- Path traversal bị từ chối.
- Blob hợp lệ truy cập được qua `/storage/...`.
- `/storage/local_db.sqlite3` và `/storage/../data/local_db.sqlite3` không trả database.

### 7.4 EPUB export và worker

- Local cache hit trả file và không gọi build.
- Cache miss gọi build như hiện tại.
- `force_rebuild` bỏ qua cache.
- SQLite worker không thực thi `pg_try_advisory_lock` hoặc `pg_advisory_unlock`.
- PostgreSQL worker vẫn acquire/release advisory lock.

### 7.5 Regression

Chạy toàn bộ test suite bằng `python -m pytest -q`. Không hard-code số lượng test vì suite sẽ thay đổi theo thời gian.

## 8. Smoke test thủ công

1. Chạy một trong hai script local trên máy không có PostgreSQL/Docker.
2. Kiểm tra `GET /`.
3. Kiểm tra `GET /api/v1/library/novels`.
4. Kiểm tra `GET /api/auth/config` trả local mode và `auth_required=false`.
5. Import một EPUB và xác minh metadata/chapter được lưu trong SQLite.
6. Restart server và xác minh dữ liệu vẫn tồn tại.
7. Mở cover/chapter hợp lệ từ `/storage/...`.
8. Build EPUB lần đầu; lần tải tiếp theo phải dùng cache.
9. Xác minh database không tải được qua HTTP.
10. Xác minh server chỉ listen trên `127.0.0.1`.
11. Tùy chọn: dịch thử bằng Gemini khi có Internet và API key.

## 9. Thứ tự triển khai

1. Đổi fallback `DATABASE_URL`, bổ sung `.gitignore`, resolve SQLite path từ project root và hoàn thiện engine configuration.
2. Giữ nguyên bốn migration đã sửa; chạy lại migration cycle sau khi hoàn tất các thay đổi khác.
3. Sửa lựa chọn local provider mà không thay đổi mơ hồ semantics của `is_blob_active`.
4. Mount static storage và thêm security test cho database path.
5. Thêm local cached EPUB response.
6. Phân nhánh advisory lock theo dialect.
7. Tạo hai script khởi động.
8. Chạy targeted tests, migration cycle, toàn bộ test suite và smoke test.

## 10. Tiêu chí hoàn thành

- Một lệnh/script khởi động được backend local từ database rỗng.
- Không cần Render, Supabase, R2, Firebase, Docker hoặc PostgreSQL Server.
- SQLite và blob tồn tại qua restart.
- Database không được phục vụ qua HTTP.
- Local EPUB cache hoạt động mà không rebuild thừa.
- Worker không phát cảnh báo PostgreSQL advisory lock trên SQLite.
- Migration cycle và toàn bộ test suite pass.
- Không làm hồi quy đường chạy PostgreSQL/cloud hiện có.

## 11. Nhật ký quyết định

| Quyết định | Phương án khác | Lý do |
|---|---|---|
| SQLite nằm trong `data/`, blob nằm trong `storage/` | Đặt cả hai trong `storage/` | Tránh public database khi mount static |
| Resolve SQLite URL tương đối từ project root | Dùng current working directory | Lệnh chạy từ thư mục con vẫn dùng cùng database |
| Script override environment theo process | Sửa `.env` khi chuyển profile | Giữ được cấu hình cloud/Gemini và tránh thao tác thủ công |
| Giữ giá trị flag `postgres` | Đổi thành `sql` hoặc `database` | Tránh mở rộng phạm vi và regression không cần thiết |
| Phân nhánh worker theo dialect | Bắt exception từ PostgreSQL SQL trên SQLite | Không tạo warning và không dùng connection/transaction thừa |
| Giữ `is_blob_active` theo nghĩa hiện tại | Cho local vào biểu thức hiện tại | Tránh thay đổi hành vi ngầm tại nhiều call site |
| Dùng `FileResponse` cho EPUB local | Đọc bytes vào RAM hoặc redirect CDN | Streaming hiệu quả và giữ download headers |
| Một process SQLite | Hỗ trợ multi-process SQLite | Phù hợp phạm vi local single-user và tránh locking phức tạp |
| Giữ bốn migration batch hiện tại | Viết lại hoặc khôi phục migration | Trạng thái hiện tại đã vượt qua chu kỳ migration hai chiều |

## 12. Non-goals

- Chạy offline chức năng Gemini.
- Migrate dữ liệu cloud hiện có về local tự động.
- Cho phép truy cập từ LAN/Internet.
- Thay PostgreSQL production bằng SQLite.
- Hỗ trợ nhiều Uvicorn worker trên cùng SQLite database.
- Refactor toàn bộ storage abstraction hoặc đổi tên toàn bộ cấu hình legacy.
