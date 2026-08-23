# Web Reader

> Tổng hợp kiến thức về thư viện đọc truyện web cho các chương đã dịch.
> Cập nhật lần cuối: 2026-08-24

---

## Architecture

### Read-only Reader Module
- **Ngày**: 2026-08-24
- **Chi tiết**: Tách Reader thành module read-only gồm API, schema và service. Reader chỉ xuất các truyện có chương đã dịch hoàn tất, không fallback sang nội dung gốc. Tầng Reader phụ thuộc `LibraryService` application facade thay vì truy cập persistence trực tiếp.
- **Files liên quan**: `app/modules/reader/`, `app/api/v1/reader.py`, `app/modules/library/application/facade.py`

### Render Catalog With Direct Blob Reads
- **Ngày**: 2026-08-24
- **Chi tiết**: Render phục vụ catalog và metadata chương; trình duyệt tải nội dung chương trực tiếp từ public storage để tránh cold start và truyền lại payload lớn qua web service. Reader không nhúng service-role key.
- **Files liên quan**: `app/static/reader.html`, `app/modules/reader/schemas.py`, `app/modules/library/legacy_service.py`

---

## Bugs & Solutions

### Metadata Chapter Zero Caused Reader 500
- **Ngày**: 2026-08-24
- **Vấn đề**: Một truyện có chương metadata `chapter_index = 0`, trong khi schema Reader yêu cầu chỉ số từ 1. Response validation lỗi thành HTTP 500; trình duyệt biểu hiện thành `Failed to fetch`.
- **Root cause**: Bộ lọc readable chapters không loại các mục metadata.
- **Fix**: Chỉ xuất chương có `chapter_index >= 1`, đồng thời thêm test hồi quy.
- **Files liên quan**: `app/modules/reader/service.py`, `tests/test_reader.py`

### Supabase Metadata And R2 Content Drift
- **Ngày**: 2026-08-24
- **Vấn đề**: Metadata chương nằm ở Supabase nhưng file TXT cũ nằm trên R2; URL Supabase trả lỗi còn URL R2 vẫn đọc được.
- **Fix**: Response có nhiều `content_urls`; frontend thử tuần tự từng URL rồi mới fallback về Render API.
- **Files liên quan**: `app/modules/library/legacy_service.py`, `app/modules/reader/schemas.py`, `app/static/reader.html`

---

## How-To

### Verify Reader Production
- **Ngày**: 2026-08-24
- **Bước thực hiện**:
  1. Gọi `GET /api/v1/reader/books` và kiểm tra số lượng truyện.
  2. Gọi `/api/v1/reader/books/{novel_id}` để kiểm tra metadata và mục lục.
  3. Kiểm tra từng URL trong `content_urls`; URL storage phải trả 200.
  4. Chạy `.venv/Scripts/python.exe -m pytest -q` trước khi push.
- **Files liên quan**: `tests/test_reader.py`, `app/modules/reader/api.py`

### Configure Backend URL
- **Ngày**: 2026-08-24
- **Bước thực hiện**:
  1. Reader ưu tiên query `?backend=...`.
  2. Nếu không có, đọc `localStorage.epub_backend_url`.
  3. Nếu vẫn thiếu, dùng backend production mặc định.
  4. Giữ query override khi quay lại Reader hoặc mở trang quản trị.
- **Files liên quan**: `app/static/reader.html`, `app/static/index.html`

---

## Patterns

### Application-Facade Boundary
- **Ngày**: 2026-08-24
- **Chi tiết**: Module Reader không import repository, database session hoặc storage singleton. Các use-case như `get_chapter_content_url(s)` được expose qua `LibraryService` và `ChapterService`, giúp giữ boundary và tránh N+1 query bằng cách truyền `ChapterItem` đã hydrate.
- **Files liên quan**: `app/modules/reader/service.py`, `app/modules/library/application/chapter_service.py`

### Progressive Storage Fallback
- **Ngày**: 2026-08-24
- **Chi tiết**: Xây dựng danh sách URL đã validate chỉ nhận `http`/`https`, loại trùng, thử storage trực tiếp theo thứ tự rồi fallback về API. Cách này tương thích dữ liệu chuyển đổi giữa Supabase và R2 mà không làm hỏng luồng đọc.
- **Files liên quan**: `app/modules/library/legacy_service.py`, `app/static/reader.html`
