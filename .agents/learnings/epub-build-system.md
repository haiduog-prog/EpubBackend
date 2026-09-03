# EPUB Build System

> Tổng hợp kiến thức về hệ thống biên dịch EPUB: Fast Patch, Background Build Worker, Advisory Lock, Revision Tracking, Retention Management, Realtime Progress Reporting, Graceful Cancellation, Cross-Cloud Storage Fallback, Scoped Range Isolation, và ThreadPool Cancellation Propagation.
> Cập nhật lần cuối: 2026-09-03

---

## Architecture

### FAST_PATCH vs FULL_REBUILD Two-Strategy Pattern
- **Ngày**: 2026-09-02
- **Chi tiết**: Hệ thống dùng hai chiến lược build EPUB. FAST_PATCH chỉ thay thế XHTML chapters đã thay đổi trong archive ZIP có sẵn bằng Info-ZIP `zip -u` (hoặc fallback Python zipfile streaming). FULL_REBUILD dùng `ebooklib.write_epub()` tạo lại toàn bộ. FAST_PATCH chỉ áp dụng khi: (a) `epub_fast_patch_enabled=True`, (b) không phải structural change, (c) layout standardized (`ch_NNNN.xhtml`). Mọi trường hợp khác fallback FULL_REBUILD.
- **Files liên quan**: `app/modules/library/application/epub_export_service.py`, `app/modules/library/application/epub_zip_patcher.py`

### Background Build Consumer (Single Worker)
- **Ngày**: 2026-09-02
- **Chi tiết**: Worker chạy dưới dạng `asyncio.Task` trong cùng process FastAPI. Concurrency=1 (phù hợp Render Free 512MB). Worker loop: acquire advisory lock → claim job → heartbeat lease → build in executor → complete job → release lock. Build thực thi trong `run_in_executor` để không block event loop. Heartbeat chạy song song bằng `asyncio.create_task` mỗi 25s.
- **Files liên quan**: `app/modules/library/application/epub_build_worker.py`, `app/main.py`

### Per-Chapter Revision Tracking
- **Ngày**: 2026-09-02
- **Chi tiết**: `dirty_chapters` lưu dưới dạng dict `{"chapter_index": revision_number}` thay vì list. Khi worker claim job, nó snapshot `claimed_dirty_chapters`. Khi complete, so sánh claimed vs current: nếu chapter có revision mới hơn claimed → giữ lại trong dirty set và tự enqueue job tiếp. Pattern này giải quyết race condition khi user sửa chapter cùng lúc worker đang build.
- **Files liên quan**: `app/modules/library/persistence/legacy_repository.py` (mark_dirty_and_enqueue_job, complete_job)

### Immutable Versioned EPUB Artifacts
- **Ngày**: 2026-09-02
- **Chi tiết**: Mỗi build tạo file `novels/{id}/exports/r{rev}.epub` trên Object Storage (Supabase/R2). File là immutable — không bao giờ ghi đè. Retention policy xóa revision cũ: `oldest_to_keep = current_rev - retention_copies + 1`. Novel model lưu `current_epub_key` trỏ vào revision mới nhất.
- **Files liên quan**: `app/modules/library/application/epub_export_service.py`

### Realtime Progress & Graceful Cancellation Architecture
- **Ngày**: 2026-09-03
- **Chi tiết**: Client poll trạng thái job qua API và nhận thông tin `current_step`, `current_chapter`, `processed_chapters`, `total_chapters`, `progress_percentage`. Khi người dùng bấm "Hủy", client gọi `POST /epub-builds/{job_id}/cancel`. Worker/Exporter kiểm tra qua `is_cancelled_callback()` trước mỗi lượt tải chương. Nếu bị hủy, hệ thống raise `EpubBuildCancelledException`, dọn dẹp file tạm, không cập nhật storage và chuyển job sang trạng thái `cancelled`.
- **Files liên quan**: `app/modules/library/application/epub_export_service.py`, `app/modules/library/application/epub_build_worker.py`, `app/modules/library/api.py`

### Cross-Cloud Storage Fallback Architecture
- **Ngày**: 2026-09-03
- **Chi tiết**: Hệ thống hỗ trợ song song hai nhà cung cấp lưu trữ đám mây (Cloudflare R2 làm primary cho băng thông cao, Supabase làm secondary cho legacy storage). Repository phải thực hiện fallback 2 chiều cho `file_exists()`, `download_file_stream()`, và `get_bytes()`: nếu primary provider (R2) không tìm thấy đối tượng, bắt buộc phải kiểm tra tiếp secondary provider (Supabase) trước khi kết luận đối tượng không tồn tại.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`

### ThreadPool Cancellation Propagation Architecture
- **Ngày**: 2026-09-03
- **Chi tiết**: Các tác vụ tải chương đồng thời qua `concurrent.futures.ThreadPoolExecutor` không tự động dừng khi job bị hủy ở tầng API/DB. Cần truyền trực tiếp `is_cancelled_callback()` vào worker function bên trong thread pool, kiểm tra trước mỗi request I/O và raise `EpubBuildCancelledException` ngay lập tức để ngắt thread, tránh việc các thread tiếp tục gửi request mạng quán tính.
- **Files liên quan**: `app/modules/library/legacy_service.py`

---

## Bugs & Solutions

### Advisory Lock Bị Mất Sau Session Commit
- **Ngày**: 2026-09-02
- **Vấn đề**: `pg_try_advisory_lock` gắn với PostgreSQL backend PID. Khi dùng ORM session commit, SQLAlchemy có thể trả connection về pool → PID thay đổi → lock mất.
- **Root cause**: Advisory lock thuộc về physical connection, không phải transaction. Session commit có thể trả connection về pool.
- **Fix**: Checkout raw connection riêng `engine.connect().execution_options(isolation_level="AUTOCOMMIT")` và giữ suốt chu trình build. AUTOCOMMIT ngăn pool rollback giải phóng lock khi trả connection.
- **Files liên quan**: `app/modules/library/application/epub_build_worker.py`

### Cloud Upload False Positive Do Local Fallback
- **Ngày**: 2026-09-02
- **Vấn đề**: Sau upload cloud thất bại, `StorageRepository.file_exists()` fallback kiểm tra local disk → báo thành công giả.
- **Root cause**: `file_exists()` có logic `if active_provider miss → check local_provider`. Khi `upload_file_stream` fail trên cloud nhưng mirror local thành công, `file_exists` trả True.
- **Fix**: Dùng `file_exists_on_supabase()` hoặc `file_exists_on_r2()` — gọi trực tiếp provider, không fallback.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `app/modules/library/application/epub_export_service.py`

### Double Rebuild Khi Download Từ UI
- **Ngày**: 2026-09-02
- **Vấn đề**: UI enqueue background build → poll → build xong → download endpoint vẫn mang `?force_rebuild=true` → chạy `build_and_publish_epub` đồng bộ lần 2 → timeout 502.
- **Root cause**: JavaScript giữ `force_rebuild=true` trong URL download sau khi background build hoàn tất.
- **Fix**: Khi download sau background build, gọi `GET /export/epub` không kèm `force_rebuild` → nhận 307 CDN redirect tức thì.
- **Files liên quan**: `app/static/index.html`

### Info-ZIP `zip -u` Không Patch Khi Timestamp Bằng Nhau
- **Ngày**: 2026-09-02
- **Vấn đề**: `zip -u` chỉ thay file nếu staging file mtime > archive entry mtime. Nếu bằng hoặc entry có timestamp tương lai → exit 0 nhưng không patch.
- **Root cause**: Info-ZIP dùng timestamp comparison, không phải content comparison.
- **Fix**: Set `os.utime(staging_file, (now+10, now+10))` trước khi chạy `zip -u` để đảm bảo staging luôn mới hơn.
- **Files liên quan**: `app/modules/library/application/epub_zip_patcher.py`

### Dirty Chapters Type Mismatch (list vs dict)
- **Ngày**: 2026-09-02
- **Vấn đề**: `NovelModel.dirty_chapters` khai báo `Mapped[list]` với `default=list` nhưng business logic lưu dict `{"10": 43}`.
- **Root cause**: Schema DDL không được cập nhật khi chuyển từ list sang dict-based revision tracking.
- **Fix**: Đổi type annotation thành `Mapped[dict]` với `default=dict` cho cả `NovelModel.dirty_chapters`, `EpubBuildJobModel.dirty_chapters`, và `claimed_dirty_chapters`.
- **Files liên quan**: `app/modules/library/persistence/legacy_models.py`

### Ghost Novel Gây "Không Tìm Thấy Truyện" Khi Build
- **Ngày**: 2026-09-02
- **Vấn đề**: Novel tồn tại trong legacy JSON storage nhưng không có trong Postgres DB. Build endpoint dùng DB repository → báo lỗi "không tìm thấy".
- **Root cause**: Dữ liệu chưa được migrate từ JSON sang Postgres.
- **Fix**: `get_novel()` tự động upsert novel vào DB khi đọc được từ JSON fallback (khi backend là `postgres`/`dual`).
- **Files liên quan**: `app/modules/library/legacy_service.py`

### Ép FULL_REBUILD Do Thiếu current_epub_key Dù full.epub Có Sẵn
- **Ngày**: 2026-09-02
- **Vấn đề**: Dịch 1 chương nhưng worker chạy `FULL_REBUILD` duyệt toàn bộ chương từ 0 đến hết.
- **Root cause**: Truyện cũ chưa có `current_epub_key` trong DB (`NULL`). Logic `needs_full` thấy `not current_epub_key` liền ép `strategy=full_rebuild`, bỏ qua file `novels/{id}/full.epub` đã tồn tại trên storage.
- **Fix**: Trong `mark_dirty_and_enqueue_job`, kiểm tra `has_existing_base = novel.current_epub_key or storage_repo.file_exists(...)`. Nếu có `full.epub`, khởi tạo `current_epub_key` và chọn `fast_patch`.
- **Files liên quan**: `app/modules/library/persistence/legacy_repository.py`

### UI Luôn Gửi force_rebuild: true Khi Xuất Theo Dải Chương
- **Ngày**: 2026-09-02
- **Vấn đề**: Người dùng nhập range `151-151` trên modal Xuất EPUB nhưng server vẫn rebuild toàn bộ.
- **Root cause**: `index.html` hardcode `force_rebuild: true` trong body POST `/epub-builds`.
- **Fix**: `force_rebuild: !isSpecificRange` — chỉ force rebuild khi người dùng không chọn range cụ thể.
- **Files liên quan**: `app/static/index.html`

### Lặp Tiền Tố novels/novels Khi Bóc Tách Cover Key
- **Ngày**: 2026-09-02
- **Vấn đề**: Supabase trả về 400 Bad Request cho request tải ảnh cover: `.../novels/novels/novels/cover.jpeg`.
- **Root cause**: `cover_url.split('/novels/', 1)[1]` rồi lại cộng thêm `"novels/" + ...` làm nhân đôi tiền tố.
- **Fix**: Dùng `raw_cover.split('/novels/')[-1].lstrip('/')` để lấy đúng phần đuôi và chuẩn hóa thành `novels/{tail}`.
- **Files liên quan**: `app/modules/library/legacy_service.py`

### Alembic Multiple Head Revisions Khi Có Nhiều Migration Nhánh Song Song
- **Ngày**: 2026-09-03
- **Vấn đề**: Lệnh `python -m alembic upgrade head` báo lỗi `Multiple head revisions are present`.
- **Root cause**: Hai file migration khác nhau cùng khai báo chung một `down_revision`, tạo ra phân nhánh.
- **Fix**: Sửa `down_revision` của file migration mới nhất trỏ vào ID revision của file head còn lại để tạo chuỗi tuyến tính.
- **Files liên quan**: `alembic/versions/f4a5b6c7d8e9_add_epub_build_job_progress_columns.py`

### psycopg InFailedSqlTransaction Do Thiếu Cột Trong Database Chưa Migrate
- **Ngày**: 2026-09-03
- **Vấn đề**: Bất kỳ query nào tới `epub_build_jobs` đều crash với lỗi `InFailedSqlTransaction: current transaction is aborted`.
- **Root cause**: Model SQLAlchemy đã khai báo các cột mới (`current_step`, `total_chapters`...), nhưng database thực tế chưa được chạy DDL thêm cột. Câu lệnh SELECT đầu tiên fail làm hỏng toàn bộ transaction Postgres.
- **Fix**: Tạo migration Alembic thêm các cột mới và chạy `python -m alembic upgrade head`.
- **Files liên quan**: `alembic/versions/f4a5b6c7d8e9_add_epub_build_job_progress_columns.py`, `app/modules/library/persistence/legacy_models.py`

### Base EPUB False-Miss Do Thiếu Cross-Cloud Fallback Giữa R2 và Supabase
- **Ngày**: 2026-09-03
- **Vấn đề**: Người dùng xuất chương 151 nhưng hệ thống luôn nhận định `has_existing_base = False` và ép sang `FULL_REBUILD`.
- **Root cause**: Trên Render, R2 là active provider nhưng file `full.epub` nằm trên Supabase. Hàm `storage_repo.file_exists()` chỉ kiểm tra R2 và local disk, không hỏi Supabase nên trả về False.
- **Fix**: Bổ sung fallback kiểm tra chéo sang Supabase trong `file_exists()`, `download_file_stream()`, và `get_bytes()`. Đồng thời kiểm tra cả hai tiền tố `novels/{id}/full.epub` và `{id}/full.epub`.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `app/modules/library/persistence/legacy_repository.py`

### Cờ is_structural_dirty Toàn Cục Ép Scoped Range Sang FULL_REBUILD
- **Ngày**: 2026-09-03
- **Vấn đề**: Khi một bộ truyện chưa từng build thành công, cờ `is_structural_dirty` vẫn là `True` trong database, khiến mọi request vá theo dải chương cụ thể (151-151) bị ép sang `FULL_REBUILD`.
- **Root cause**: Điều kiện `needs_full = bool(... or novel.is_structural_dirty ...)` không phân biệt giữa rebuild toàn bộ và vá dải chương cụ thể.
- **Fix**: Định nghĩa `is_scoped_range = bool(dirty_indexes and not force_rebuild)`. Chỉ kích hoạt `full_rebuild` từ cờ này khi `novel.is_structural_dirty and not is_scoped_range`.
- **Files liên quan**: `app/modules/library/persistence/legacy_repository.py`

### export_full_epub Bỏ Qua target_indexes Khi Rebuild Dải Chương
- **Ngày**: 2026-09-03
- **Vấn đề**: Khi rebuild dải chương 151-151, worker vẫn duyệt và tải toàn bộ các chương từ chương 0 của cả bộ truyện.
- **Root cause**: Code cũ trong `export_full_epub` cố tình log "bỏ qua range" và gán `chapters_to_export = sorted(meta.chapters)`.
- **Fix**: Lọc `chapters_to_export = [ch for ch in sorted(meta.chapters) if ch.chapter_index in target_indexes]`, chỉ tải và đóng gói đúng các chương trong dải yêu cầu.
- **Files liên quan**: `app/modules/library/legacy_service.py`

### ThreadPool Chạy Quán Tính Khi Đã Hủy Job Do Thiếu Callback Trong Worker Loop
- **Ngày**: 2026-09-03
- **Vấn đề**: Người dùng bấm Hủy Biên Dịch trên UI, API trả về status cancelled nhưng log server vẫn tiếp tục gửi HTTP request tải các chương tiếp theo.
- **Root cause**: `ThreadPoolExecutor` trong `export_full_epub` không nhận `is_cancelled_callback`. Khi job bị hủy ở DB, các luồng đang chạy không biết và vẫn tải nốt danh sách chương.
- **Fix**: Truyền `is_cancelled_callback` vào `_fetch_chapter_text`. Kiểm tra trước mỗi lần gửi request; nếu True thì raise `EpubBuildCancelledException` ngay lập tức để ngắt thread pool.
- **Files liên quan**: `app/modules/library/legacy_service.py`, `app/modules/library/application/epub_export_service.py`

### Fallback Rebuild Bị Mất Scoped Range Do Worker Thiếu target_chapters
- **Ngày**: 2026-09-03
- **Vấn đề**: Dù job là fast_patch cho chương 151, khi bị fallback sang rebuild, server lại tải toàn bộ 4000 chương từ chương 0.
- **Root cause**: Worker khi gọi `build_and_publish_epub` chỉ truyền `dirty_chapters=[151]`, để `target_chapters=None`. Khi rơi vào fallback `export_full_epub`, nó thấy `target_chapters=None` nên coi như xuất toàn bộ truyện.
- **Fix**: Worker tự động chuyển đổi `dirty_chapters` thành chuỗi `target_chapters="151"` trước khi gọi build. Đồng thời trong `epub_export_service.py` tự động suy diễn `effective_target_chapters` từ `target_indexes` trước khi fallback.
- **Files liên quan**: `app/modules/library/application/epub_build_worker.py`, `app/modules/library/application/epub_export_service.py`

### EPUB Layout Rejection Do Regex Tên File Chương Quá Khắt Khe
- **Ngày**: 2026-09-03
- **Vấn đề**: File base EPUB trên storage bị `is_layout_standardized` đánh giá là không chuẩn và ép fallback sang rebuild toàn bộ.
- **Root cause**: Regex cũ `^ch_\d{4}\.xhtml$` chỉ chấp nhận định dạng đúng 4 chữ số (`ch_0151.xhtml`). Nếu file base có tên `chapter_151.xhtml` hoặc `ch_151.xhtml` thì bị từ chối.
- **Fix**: Mở rộng regex thành `^(?:ch|chapter)_?0*(\d+)\.(?:xhtml|html)$` trong cả `is_layout_standardized` và `patch_epub_streaming`.
- **Files liên quan**: `app/modules/library/application/epub_zip_patcher.py`

---

## How-To

### Quy Trình Thêm Mới Một EPUB Build Strategy
- **Ngày**: 2026-09-02
- **Bước thực hiện**:
  1. Thêm logic strategy mới trong `EpubExportService.build_and_publish_epub()`.
  2. Update `mark_dirty_and_enqueue_job()` để set `strategy` field phù hợp.
  3. Đảm bảo output file qua `EpubZipPatcher.verify_epub_archive()` trước upload.
  4. Thêm test trong `tests/test_epub_fast_patch.py`.
  5. Chạy benchmark trong `tests/test_epub_benchmark.py`.
- **Files liên quan**: `app/modules/library/application/epub_export_service.py`, `app/modules/library/persistence/legacy_repository.py`

### Quy Trình Triển Khai Graceful Cancellation Cho Background Task
- **Ngày**: 2026-09-03
- **Bước thực hiện**:
  1. Thêm trạng thái `'cancelled'` vào model và response schema.
  2. Tạo endpoint `POST /.../{job_id}/cancel` chuyển trạng thái job trong DB sang `'cancelled'`.
  3. Trong hàm xử lý tác vụ nặng (vòng lặp I/O), tiêm callback `is_cancelled_callback()` kiểm tra DB trước mỗi bước.
  4. Khi cờ `cancelled` bật, raise một Exception riêng biệt (`EpubBuildCancelledException`), dọn dẹp file tạm trên đĩa và dừng worker mà không ghi đè storage.
- **Files liên quan**: `app/modules/library/api.py`, `app/modules/library/application/epub_export_service.py`, `app/modules/library/application/epub_build_worker.py`

---

## Patterns

### EPUB OCF Compliance Enforcement
- **Ngày**: 2026-09-02
- **Chi tiết**: EPUB spec yêu cầu entry đầu tiên là `mimetype` (uncompressed, offset 0). Sau khi Info-ZIP patch, OCF có thể bị lệch. Pattern: kiểm tra entry[0], nếu sai thì rebuild container: ghi mimetype trước (STORED), copy tất cả entry khác **giữ nguyên compress_type gốc** bằng `dst_zf.writestr(info, data)` (truyền ZipInfo object thay vì filename string).
- **Files liên quan**: `app/modules/library/application/epub_zip_patcher.py`

### Lease Heartbeat + State Flag Guard
- **Ngày**: 2026-09-02
- **Chi tiết**: Worker chạy heartbeat song song bằng `asyncio.create_task`. Heartbeat cập nhật `lease_expires_at` mỗi 25s. Nếu heartbeat fail → set `lease_state["lost"] = True`. Trước khi complete job, worker kiểm tra flag này. `complete_job()` cũng xác thực `job.lease_token == worker_id`. Pattern "double-check" này ngăn hoàn toàn stale worker ghi đè artifact mới.
- **Files liên quan**: `app/modules/library/application/epub_build_worker.py`, `app/modules/library/persistence/legacy_repository.py`

### Dirty State Write-Once-True Until Build
- **Ngày**: 2026-09-02
- **Chi tiết**: `is_structural_dirty` trong `save_novel()` dùng logic `model.is_structural_dirty = model.is_structural_dirty or bool(meta.is_structural_dirty)`. Một khi True, chỉ có thể reset về False trong `complete_job()` sau build thành công. Pattern tương tự áp dụng cho `desired_revision` (chỉ tăng, không giảm: `max(model, meta)`).
- **Files liên quan**: `app/modules/library/persistence/legacy_repository.py`

### mark_dirty Phải Log Ở Warning Level
- **Ngày**: 2026-09-02
- **Chi tiết**: `mark_dirty()` là trigger duy nhất để enqueue rebuild job. Nếu nó fail mà log ở `debug` (mặc định không hiển thị), EPUB sẽ không bao giờ cập nhật mà không có dấu hiệu. Luôn dùng `logger.warning` cho các hàm "fire-and-forget" quan trọng.
- **Files liên quan**: `app/modules/library/legacy_service.py`

### Realtime Progress Callback Injection Pattern
- **Ngày**: 2026-09-03
- **Chi tiết**: Hàm xử lý I/O nặng độc lập (`build_and_publish_epub`) không phụ thuộc trực tiếp vào DB session dài hạn. Thay vào đó, worker tiêm một callback nhẹ: mỗi khi tải xong một phần tử, callback mở một transaction con ngắn hạn ghi nhận `current_step` và commit ngay lập tức. Client poll status sẽ nhận được dòng trạng thái live mượt mà mà không gây database lock contention.
- **Files liên quan**: `app/modules/library/application/epub_export_service.py`, `app/modules/library/application/epub_build_worker.py`

### Scoped Range Isolation Pattern
- **Ngày**: 2026-09-03
- **Chi tiết**: Phân biệt rạch ròi giữa "thay đổi cấu trúc toàn cục của tiểu thuyết" (`is_structural_dirty`) và "thao tác biên dịch theo dải chương cụ thể" (`is_scoped_range = bool(dirty_indexes and not force_rebuild)`). Khi người dùng yêu cầu thao tác trên một phạm vi xác định, hệ thống cô lập phạm vi đó: không cho phép cờ bẩn toàn cục ép sang `full_rebuild`, và không bao giờ tải duyệt các chương nằm ngoài phạm vi được chỉ định.
- **Files liên quan**: `app/modules/library/persistence/legacy_repository.py`, `app/modules/library/legacy_service.py`

### Worker Param Forwarding Invariance Pattern
- **Ngày**: 2026-09-03
- **Chi tiết**: Mọi tham số phạm vi dải chương (`target_chapters`, `dirty_chapters`) phải được bảo toàn xuyên suốt chuỗi gọi hàm: `API -> DB Job -> Worker Claim -> Export Service -> Fallback Rebuild`. Nếu một hàm trung gian chuyển giao thiếu đối số, hàm con sẽ hiểu nhầm là yêu cầu xuất toàn bộ cuốn sách. Phải luôn có lớp suy diễn tự động (`effective_target_chapters = target_chapters or ",".join(dirty_chapters)`) để bảo vệ tính bất biến.
- **Files liên quan**: `app/modules/library/application/epub_build_worker.py`, `app/modules/library/application/epub_export_service.py`
