# EPUB Build System

> Tổng hợp kiến thức về hệ thống biên dịch EPUB: Fast Patch, Background Build Worker, Advisory Lock, Revision Tracking, và Retention Management.
> Cập nhật lần cuối: 2026-09-02

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
