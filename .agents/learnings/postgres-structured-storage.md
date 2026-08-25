# PostgreSQL Structured Storage & Migration

> Tổng hợp kiến thức về hạ tầng lưu trữ cơ sở dữ liệu có cấu trúc Render PostgreSQL / Supabase, mô hình Repository, quy trình Backfill từ Cloudflare R2, cơ chế Audit an toàn và Quản lý/Xóa dữ liệu đa tầng.
> Cập nhật lần cuối: 2026-08-25

---

## Architecture

### Render PostgreSQL & R2 Separation of Concerns
- **Ngày**: 2026-08-19
- **Chi tiết**: Tách biệt hoàn toàn tầng dữ liệu có cấu trúc và tầng file payload lớn. Toàn bộ structured entities (Novels, Chapters metadata, Book Bibles, Translation Jobs, Import Jobs, Character Profiles, Timeline Events & Evidence) được lưu trữ trên PostgreSQL. Cloudflare R2 chỉ đóng vai trò Blob Storage cho file lớn (.epub, .txt nội dung chương, cover.jpg). Loại bỏ hoàn toàn Firestore.
- **Files liên quan**: `app/db/models/`, `app/repositories/`, `app/core/storage.py`, `render.yaml`

### Render Web Service + Supabase PostgreSQL Hybrid Architecture
- **Ngày**: 2026-08-20
- **Chi tiết**: Kết hợp Web Service FastAPI trên Render (miễn phí) và PostgreSQL trên Supabase (miễn phí vĩnh viễn, không hết hạn 30 ngày như Render DB Free). Backend kết nối tới Supabase qua Session Connection Pooler URI (port 6543 hoặc 5432) qua biến môi trường `DATABASE_URL`. Cloudflare R2 vẫn làm Blob storage cho EPUB/media để tránh phí egress.
- **Files liên quan**: `render.yaml`, `app/db/session.py`, `app/config.py`

### Repository Pattern & Context-Managed Session
- **Ngày**: 2026-08-19
- **Chi tiết**: Mọi thao tác đọc/ghi Database đều đi qua các ClassMethod của tầng Repository (`LibraryRepository`, `BookBibleRepository`, `CharacterProfileRepository`) nhận `session: Session`. Tầng Service điều phối qua context manager `with db_session() as session:`, đảm bảo transaction atomicity, tự động rollback khi exception và giải phóng connection về pool.
- **Files liên quan**: `app/db/session.py`, `app/repositories/`

### Cascading Foreign Key Integrity & Multi-Layer Bulk Deletion
- **Ngày**: 2026-08-24
- **Chi tiết**: Các bảng liên kết (Chapters -> Novels, Events/Submissions/Editions -> ProfileBooks, Evidence -> ProfileEvents) cấu hình `ondelete='CASCADE'` trong SQLAlchemy DDL. Khi xóa đơn hoặc xóa hàng loạt (`bulk_delete_novels`), database tự động dọn sạch bản ghi con trong 1 transaction, đồng thời service xóa toàn bộ file trong bucket prefix `novels/{novel_id}/` trên Supabase/R2 Storage và xóa Book Bible, Character Profile.
- **Files liên quan**: `app/db/models/library.py`, `app/modules/library/legacy_service.py`, `app/modules/library/api.py`

### One-Time Backfill vs Safe Build Commands
- **Ngày**: 2026-08-19
- **Chi tiết**: Lệnh `buildCommand` trong `render.yaml` chỉ chạy `alembic upgrade head`. Script migration dữ liệu cũ từ R2 sang DB (`migrate_structured_r2_to_postgres.py`) là thao tác một lần, được bảo vệ bằng biến môi trường `RUN_BACKFILL=1` để không bao giờ chạy lại trong các lần deploy định kỳ, tránh ghi đè dữ liệu mới trong DB bằng JSON cũ.
- **Files liên quan**: `render.yaml`, `scripts/migrate_structured_r2_to_postgres.py`

---

## Bugs & Solutions

### Cloudflare R2 404 on EPUB Download via Stale Env Var & Alias Conflict
- **Ngày**: 2026-08-25
- **Vấn đề**: Tải file EPUB qua `/api/v1/library/novels/{novel_id}/export/epub` bị 404 Not Found từ URL Cloudflare R2 (`pub-*.r2.dev/novels/.../full.epub`) dù server đang chạy backend `Supabase`.
- **Root cause**: `file_exists_in_r2` là alias trỏ vào active provider (`self.file_exists`, tức Supabase). Nhánh kiểm tra `CLOUDFLARE_R2_PUBLIC_URL` đặt trước Supabase, nên khi biến này còn lưu trong môi trường Render, backend ngộ nhận file có trên R2 và redirect 307 nhầm sang R2 CDN thay vì Supabase CDN.
- **Fix**: Đảo thứ tự ưu tiên trong `export_novel_epub_endpoint`, kiểm tra `active_provider_name == 'supabase'` trước để redirect sang Supabase Public CDN. Chỉ redirect sang R2 khi `active_provider_name == 'r2'` hoặc khi `file_exists_on_r2` xác nhận file thực sự có trên R2.
- **Files liên quan**: `app/modules/library/api.py`, `tests/test_library_service.py`

### 502 Bad Gateway / Timeout on Large EPUB Export with Supabase Storage
- **Ngày**: 2026-08-24
- **Vấn đề**: Gọi `/api/v1/library/novels/{novel_id}/export/epub` cho truyện lớn (500+ chương) bị HTTP 502 Bad Gateway sau ~69s trên Render.
- **Root cause**: Quá trình biên dịch EPUB tải tuần tự 500+ file text qua HTTP API của Supabase Storage tạo latency lớn, trong khi endpoint chưa tự động chuyển hướng CDN khi file `full.epub` đã tồn tại trên Supabase Storage mà thiếu biến `SUPABASE_STORAGE_PUBLIC_URL`.
- **Fix**: Sử dụng `storage_repo.get_public_url(storage_key)` tự động tính toán Supabase Public CDN URL (`/storage/v1/object/public/...`) và trả về `307 Temporary Redirect` ngay lập tức nếu file đã có trên Storage.
- **Files liên quan**: `app/modules/library/api.py`, `app/infrastructure/storage/legacy_storage.py`

### Security Boundary Violation with Inline Template Literals
- **Ngày**: 2026-08-24
- **Vấn đề**: `test_security_boundaries.py` thất bại vì phát hiện cú pháp `onclick="openNovelDetail('${...}')"` trong chuỗi HTML động.
- **Root cause**: Chèn biến JavaScript trực tiếp vào inline event handler có nguy cơ XSS và vi phạm quy chuẩn bảo mật frontend.
- **Fix**: Đổi sang dùng HTML5 Data Attributes (`data-novel-id`, `data-novel-title`) và gắn event listener bằng `querySelectorAll().forEach(btn => btn.addEventListener('click', ...))` sau khi render.
- **Files liên quan**: `app/static/index.html`, `tests/test_security_boundaries.py`

### Alembic CLI Not Recognized & Python PATH on Windows
- **Ngày**: 2026-08-20
- **Vấn đề**: Chạy lệnh `alembic upgrade head` trên PowerShell báo lỗi `The term 'alembic' is not recognized as the name of a cmdlet...`.
- **Root cause**: Thư mục Scripts của Python (`AppData\Local\Python\...\Scripts`) chưa được nạp vào biến môi trường `PATH` của Windows.
- **Fix**: Sử dụng cú pháp gọi qua module Python: `python -m alembic upgrade head` để thực thi trực tiếp từ môi trường Python hiện hành.
- **Files liên quan**: `alembic.ini`, `alembic/env.py`

### Trailing Dot in Alembic Revision Target
- **Ngày**: 2026-08-20
- **Vấn đề**: Chạy `python -m alembic upgrade head.` báo lỗi `FAILED: Can't locate revision identified by 'head.'`.
- **Root cause**: Dính dấu chấm `.` ở cuối tham số khiến Alembic tìm revision mang tên `head.` thay vì target revision `head`.
- **Fix**: Bỏ dấu chấm ở đuôi, chạy đúng lệnh: `python -m alembic upgrade head`.
- **Files liên quan**: `alembic/versions/2865c48fe099_initial_schema.py`

### Supabase Direct IPv6 vs Render Network Unreachable
- **Ngày**: 2026-08-20
- **Vấn đề**: Deploy lên Render bị crash khi khởi động với lỗi `psycopg.OperationalError: connection to server at "2406:da14:...", port 5432 failed: Network is unreachable`.
- **Root cause**: Supabase Direct hostname (`db.<ref>.supabase.co`) chỉ hỗ trợ IPv6 theo mặc định, trong khi Render Web Service chỉ hỗ trợ outbound IPv4.
- **Fix**: Sử dụng **Connection Pooler (Supavisor)** URI của Supabase (`aws-0-<region>.pooler.supabase.com` port 5432 - Session mode hoặc 6543 - Transaction mode) vì host pooler hỗ trợ đầy đủ IPv4. User dạng `postgres.<ref>`.
- **Files liên quan**: `render.yaml`, `app/config.py`, `app/db/session.py`

### Supabase Policy Exists RLS Disabled & Public Warnings
- **Ngày**: 2026-08-20
- **Vấn đề**: Supabase Advisor cảnh báo Critical: `Policy Exists RLS Disabled` và `RLS Disabled in Public`.
- **Root cause**: Các bảng trong schema `public` có policy nhưng chưa bật cờ `ENABLE ROW LEVEL SECURITY`.
- **Fix**: Chạy `ALTER TABLE public.<table_name> ENABLE ROW LEVEL SECURITY;` hoặc bật trên Table Editor. Lưu ý: Backend FastAPI dùng connection string trực tiếp (`postgres` role) nên không bị ảnh hưởng bởi RLS.
- **Files liên quan**: Supabase SQL Editor, `app/db/base.py`

### Parameter Mismatch & Empty Book ID in list_events
- **Ngày**: 2026-08-19
- **Vấn đề**: Gọi endpoint `/events` bị lỗi 500 `TypeError: unexpected keyword argument 'canonical_chapter'`. Khi không truyền `book_id`, service truyền chuỗi rỗng `''` khiến repository luôn trả về rỗng.
- **Root cause**: Service truyền nhầm tên param `canonical_chapter` thay vì `max_canonical_chapter`. Repository chỉ có method lọc theo `book_id`.
- **Fix**: Sửa tên param thành `max_canonical_chapter=canonical_chapter`. Bổ sung `CharacterProfileRepository.list_all_events(session, status, max_canonical_chapter)` để xử lý truy vấn toàn bộ sự kiện khi `book_id is None`.
- **Files liên quan**: `app/services/character_profile_service.py`, `app/repositories/character_profile_repository.py`

### Legacy Evidence Dropped During Migration
- **Ngày**: 2026-08-19
- **Vấn đề**: `CharacterEvent` cũ không lưu `event_key`, importer sinh key giả `ev-{event_id}` nhưng `EventEvidence` lại lưu logical hash key, dẫn đến lookup lệch và toàn bộ evidence cũ bị bỏ qua.
- **Root cause**: Format lưu trữ giữa event và evidence trong legacy JSON không đồng bộ về khóa liên kết.
- **Fix**: Xây dựng hàm `compute_logical_event_key()` theo đúng công thức SHA256 canonical trong service để tái tạo logical key chuẩn cho event, duy trì bảng mapping hai chiều `logical_key <-> event_id` khi import evidence.
- **Files liên quan**: `scripts/migrate_structured_r2_to_postgres.py`

### Silent Failure in R2 Migration & Audit
- **Ngày**: 2026-08-19
- **Vấn đề**: Khi thiếu R2 credentials hoặc mất kết nối, `list_files` và `download_json` âm thầm trả về rỗng hoặc None, khiến script migration và audit báo PASS giả dù chưa import được dữ liệu.
- **Root cause**: Hàm download và listing mặc định nuốt exception và trả về fallback.
- **Fix**: Thêm tham số `raise_on_error=True` vào `list_files()`, `_r2_get_json()`, và `download_json()`. Thêm hàm tiền kiểm `_validate_r2_connectivity()` trước khi backfill.
- **Files liên quan**: `app/core/storage.py`, `scripts/migrate_structured_r2_to_postgres.py`, `scripts/audit_structured_storage.py`

### Non-Deterministic Mirror Entity Overwrite
- **Ngày**: 2026-08-19
- **Vấn đề**: Cùng một entity ID xuất hiện ở nhiều đường dẫn file R2 khác nhau (`novels/{id}/profile/...` và `data/profile_...`). Do `set()` duyệt ngẫu nhiên, importer có thể chọn bản ghi cũ đè lên bản ghi mới.
- **Root cause**: Duyệt danh sách key không có gom nhóm theo ID và không so sánh phiên bản.
- **Fix**: Nhóm (group) toàn bộ file theo primary ID, chỉ giữ bản ghi có revision cao nhất hoặc `created_at` mới nhất. Dùng logic `session.add` và skip nếu entity đã tồn tại trong DB.
- **Files liên quan**: `scripts/migrate_structured_r2_to_postgres.py`

### OOM Restart & SSL Handshake Bottleneck on Large EPUB Import with Supabase Storage
- **Ngày**: 2026-08-24
- **Vấn đề**: Tải file EPUB lớn (>500 chương) lên Render Free (512MB RAM) bị sập tiến trình tại chương ~495 với lỗi *"Import bị gián đoạn do server restart"*.
- **Root cause**: `SupabaseStorageProvider` khởi tạo `httpx.Client()` mới cho từng chương gây rò rỉ SSL context; `BeautifulSoup` và `EpubBook` không được giải phóng chủ động (`decompose()`, `gc.collect()`), làm RAM vượt 512MB khiến Linux OOM Killer gửi `SIGKILL`.
- **Fix**: 
  1. Duy trì `httpx.Client` tái sử dụng (connection pooling `max_keepalive_connections=20, max_connections=50`) trong `SupabaseStorageProvider`.
  2. Bổ sung `soup.decompose()` trong `_extract_raw_chapters_from_epub`.
  3. Giải phóng `book`, `epub_bytes`, `raw_sections` và gọi `gc.collect()` định kỳ mỗi 20 chương trong `_process_epub_chapters_sync`.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `app/modules/library/legacy_service.py`, `tests/test_supabase_storage.py`

---

## How-To

### Quy trình Xóa Nhanh & Xóa Hàng Loạt Tiểu Thuyết
- **Ngày**: 2026-08-24
- **Bước thực hiện**:
  1. **API**: Gọi `POST /api/v1/library/novels/bulk-delete` với body `{"novel_ids": ["slug-1", "slug-2"]}`.
  2. **Web UI**: Vào tab **Database Inspector** ➔ Bảng **Danh Sách Tiểu Thuyết Trong Database** ➔ Tick chọn các truyện ➔ Bấm **Xóa (N) Truyện Đã Chọn** ➔ Xác nhận trên Confirm Modal.
  3. **Chi tiết từng truyện**: Mở modal Novel Detail ➔ Bấm nút **Xóa Truyện** trên thanh tiêu đề.
- **Files liên quan**: `app/modules/library/api.py`, `app/static/index.html`, `app/modules/library/legacy_service.py`

### Quy trình Cấu hình Render Web Service + Supabase Database
- **Ngày**: 2026-08-20
- **Bước thực hiện**:
  1. Lấy connection string từ Supabase: Bấm nút `Connect` ➔ `Direct / ORM` ➔ tab `URI` (chọn Session Pooler port 6543 hoặc Direct 5432).
  2. Điền mật khẩu database vào placeholder `[YOUR-PASSWORD]`.
  3. Cấu hình biến môi trường trên Render Web Service:
     - `DATABASE_URL`: chuỗi kết nối Supabase vừa lấy.
     - `STRUCTURED_STORAGE_BACKEND`: `postgres`
     - `STRUCTURED_STORAGE_READ_SOURCE`: `postgres`
  4. Chạy migration tạo bảng từ máy tính hoặc tự động qua Build Command: `$env:DATABASE_URL="<Supabase_URI>"; python -m alembic upgrade head`.
  5. Kiểm tra toàn vẹn bảng trên Supabase Table Editor hoặc chạy script audit: `python scripts/audit_structured_storage.py`.
- **Files liên quan**: `render.yaml`, `scripts/audit_structured_storage.py`, `app/config.py`

### Quy trình Chạy One-Time Backfill & Audit Verification
- **Ngày**: 2026-08-19
- **Bước thực hiện**:
  1. Cấu hình biến môi trường kết nối R2 và PostgreSQL (`DATABASE_URL`, `CLOUDFLARE_R2_*`).
  2. Chạy schema migration: `python -m alembic upgrade head`.
  3. Kích hoạt backfill: `RUN_BACKFILL=1 python scripts/migrate_structured_r2_to_postgres.py`.
  4. Chạy kiểm đếm đối chiếu toàn diện: `python scripts/audit_structured_storage.py`.
  5. (Tùy chọn sau khi audit PASS 100%): Chạy dry-run dọn dẹp R2 cũ: `python scripts/cleanup_structured_r2.py --dry-run`.
- **Files liên quan**: `scripts/migrate_structured_r2_to_postgres.py`, `scripts/audit_structured_storage.py`, `scripts/cleanup_structured_r2.py`

---

## Patterns

### Pure PostgreSQL Isolation Pattern
- **Ngày**: 2026-08-19
- **Chi tiết**: Trong chế độ `postgres`, các hàm đọc (`get_novel`, `get_bible`, `get_job`, `_hydrate_all_from_storage`) chỉ truy vấn Database và trả về kết quả ngay hoặc None. Tuyệt đối không fallback sang đọc file R2 hay ổ đĩa để tránh hồi sinh dữ liệu đã xóa hoặc đọc dữ liệu rác.
- **Ví dụ code**:
  ```python
  if settings.structured_storage_read_source == 'postgres' or settings.structured_storage_backend == 'postgres':
      try:
          with db_session() as session:
              db_obj = Repository.get(session, entity_id)
              return db_obj
      except Exception as exc:
          if settings.structured_storage_backend == 'postgres':
              raise exc
          logger.warning('DB read failed: %s', exc)
  ```
- **Files liên quan**: `app/services/library_service.py`, `app/core/storage.py`, `app/services/character_profile_service.py`

### Pluggable Supabase & Cloudflare R2 Blob Storage Architecture
- **Ngày**: 2026-08-21
- **Chi tiết**: Tầng Blob Storage (ảnh bìa, nội dung TXT chương, file EPUB xuất bản) được thiết kế theo mô hình Strategy/Provider với interface `BaseStorageProvider`. Hỗ trợ `SupabaseStorageProvider` (thông qua REST API/CDN) và `R2StorageProvider` (boto3 S3 API). Cho phép chuyển đổi lưu trữ hoàn toàn sang Supabase hoặc quay lại Cloudflare R2 bằng cách đổi biến môi trường `STORAGE_PROVIDER=supabase` hoặc `STORAGE_PROVIDER=r2`. Các phương thức cũ như `upload_file_to_r2`, `file_exists_in_r2`, `is_r2_active` được giữ nguyên tương thích ngược để không làm gãy mã nguồn hiện hữu.
- **Files liên quan**: `app/core/storage.py`, `app/config.py`, `render.yaml`, `scripts/migrate_r2_to_supabase_storage.py`

### Dataset Event Listener Binding Pattern (XSS & Security Boundary Compliance)
- **Ngày**: 2026-08-24
- **Chi tiết**: Khi render động các bảng danh sách hoặc thẻ có action nút bấm (Mở, Xóa, Sửa), không sử dụng inline `onclick="fn('${var}')"`. Gán các thông tin vào `data-*` attribute và thực hiện query selector để bind event listeners, giúp frontend hoàn toàn tuân thủ CSP và các kiểm thử ranh giới bảo mật.
- **Ví dụ code**:
  ```javascript
  tbody.innerHTML = items.map(item => `
      <button class="btn btn-danger item-delete-btn" data-id="${escapeHtml(item.id)}" data-title="${escapeHtml(item.title)}">
          Xóa
      </button>
  `).join('');

  tbody.querySelectorAll('.item-delete-btn').forEach(btn => {
      btn.addEventListener('click', () => confirmDelete(btn.dataset.id, btn.dataset.title));
  });
  ```
- **Files liên quan**: `app/static/index.html`, `tests/test_security_boundaries.py`
