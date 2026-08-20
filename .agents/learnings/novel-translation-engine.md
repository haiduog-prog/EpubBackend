# Novel Translation Engine

> Tổng hợp kiến thức về hệ thống dịch truyện thuần Việt (v2) hỗ trợ EPUB/HTML/TXT với Structured Outputs, Prompt Caching và Decoupled LLM Providers.
> Cập nhật lần cuối: 2026-08-17

---

## Architecture

### Clean Architecture & Provider Decoupling
- **Ngày**: 2026-08-10
- **Chi tiết**: Tách biệt LLM Client qua Abstract Class `BaseLLMClient` (`app/llm/base.py`). Các provider (`AnthropicProvider`, `GeminiProvider`) triển khai độc lập và được tạo qua `LLMFactory`. Giúp mở rộng LLM provider mới mà không ảnh hưởng tầng API hay Business logic.
- **Files liên quan**: `app/llm/base.py`, `app/llm/anthropic_provider.py`, `app/llm/gemini_provider.py`, `app/llm/factory.py`

### Deterministic Book Bible Delta Merging (Code-level Upsert)
- **Ngày**: 2026-08-10
- **Chi tiết**: Không bắt LLM tái sinh toàn bộ Book Bible JSON để tránh trôi key và tốn token. LLM trích xuất delta (`BookBibleDelta`), code Python tự upsert `new_characters`, append `address_terms` cho nhân vật cũ, upsert địa danh và thuật ngữ.
- **Files liên quan**: `app/services/book_bible_service.py`, `app/schemas/book_bible.py`

### HTML Text Node Merging
- **Ngày**: 2026-08-10
- **Chi tiết**: Gộp các text nodes liền kề trong cùng 1 block ngữ nghĩa (`<p>`, `<li>`, `<blockquote>`) bị phân tách bởi thẻ inline (`<em>`, `<b>`, `<a>`) thành 1 ID ngữ nghĩa duy nhất trước khi dịch JSON array, đảm bảo không bị xé nhỏ câu gây đứt mạch văn.
- **Files liên quan**: `app/parsers/html_merger.py`

### Prompt Caching Breakpoint Placement
- **Ngày**: 2026-08-10
- **Chi tiết**: Đưa Quy tắc dịch + Book Bible JSON lên ĐẦU trong System Message và đánh dấu `cache_control: {"type": "ephemeral"}`. Đưa `previous_context` và `text_to_translate` xuống CUỐI User Message để giữ prefix hash ổn định cho Prompt Caching hit.
- **Files liên quan**: `app/prompts/templates.py`, `app/llm/anthropic_provider.py`

---

### Shared Chapter-aware Character Book Bible
- **Ngày**: 2026-08-17
- **Chi tiết**: Tách `book_id` chuẩn khỏi `edition_id` của từng EPUB; lưu thay đổi nhân vật bằng canonical append-only events và dựng snapshot theo canonical chapter. Android chỉ gửi edition/chapter đang mở; backend lọc mọi event tương lai. Projection/cache phục vụ đọc nhanh, còn event log giữ khả năng audit và rebuild.
- **Files liên quan**: `app/schemas/character_profile.py`, `app/services/character_profile_service.py`, `docs/character-profile-book-bible-design.md`

### Dual-Storage R2 Architecture & CDN Direct Download
- **Ngày**: 2026-08-17
- **Chi tiết**: Lưu trữ song song 2 tầng trên Cloudflare R2: `full.epub` nguyên cuốn để tải trọn bộ tốc độ cao qua R2 Public CDN Subdomain (`pub-*.r2.dev`), và các file Text từng chương (`original/ch_*.txt`, `translated/ch_*.txt`) để phục vụ đọc streaming, dịch từng chương và trích xuất Book Bible.
- **Files liên quan**: `app/services/library_service.py`, `app/core/storage.py`, `app/api/v1/library.py`

### Temporal Identity and Shared Canonical Data
- **Ngày**: 2026-08-17
- **Chi tiết**: Alias/identity link cũng là event có mốc chương. Trước chương tiết lộ, hai nhân vật vẫn độc lập; sau link đã duyệt, snapshot mới merge state. Book Bible dùng chung nhưng không có personal overlay trong MVP.
- **Files liên quan**: `app/services/character_profile_service.py`, `tests/test_character_identity_timeline.py`

## Bugs & Solutions

### Gemini TTS Model 400 Invalid Argument
- **Ngày**: 2026-08-10
- **Vấn đề**: Gọi `generate_content` bị lỗi 400 INVALID_ARGUMENT do model `gemini-2.5-flash-preview-tts` chỉ nhận response modality `AUDIO`.
- **Root cause**: Quét danh sách model động chứa cả các model Audio/TTS không hỗ trợ tạo Text.
- **Fix**: Viết hàm lọc bỏ tất cả model chứa chuỗi `-tts`, `tts`, `embedding`, `imagen`, `veo`, `audio` trước khi gọi text generation.
- **Files liên quan**: `app/llm/gemini_provider.py`

### Gemini 429 Quota Exhausted Fallback
- **Ngày**: 2026-08-10
- **Vấn đề**: Gọi Gemini API trả về 429 RESOURCE_EXHAUSTED do một model bị hết hạn ngạch ngày.
- **Root cause**: Key free tier bị giới hạn daily quota trên từng model đơn lẻ.
- **Fix**: Triển khai chuỗi fallback tự động (`gemini-1.5-flash-latest`, `gemini-1.5-flash-002`, `gemini-1.5-flash`, `gemini-2.0-flash-exp`, `gemini-2.0-flash-lite`, `gemini-1.5-pro`). Nếu tất cả đều hết quota, báo lỗi hướng dẫn người dùng tạo Key mới hoặc dùng Claude.
- **Files liên quan**: `app/llm/gemini_provider.py`

---

### Retry tạo event trùng
- **Ngày**: 2026-08-17
- **Vấn đề**: Android có thể gửi lại submission sau timeout hoặc offline replay.
- **Root cause**: Không có khóa ổn định theo lần gửi và fingerprint nội dung.
- **Fix**: Bắt buộc `Idempotency-Key`; service lưu `submissions_by_key`, còn evidence group dựa trên `edition_id + content_fingerprint`, nên retry không tạo event/evidence trùng.
- **Files liên quan**: `app/schemas/character_profile.py`, `app/services/character_profile_service.py`, `app/api/v1/character_profiles.py`

### Future identity gây spoiler
- **Ngày**: 2026-08-17
- **Vấn đề**: Gộp alias toàn cục khiến chương cũ biết danh tính được tiết lộ ở chương sau.
- **Root cause**: Identity mapping không có hiệu lực theo thời gian.
- **Fix**: Lưu identity link như event; resolver chỉ áp dụng link có `canonical_chapter <= requested_chapter`, có regression test cho reveal ở chương 300.
- **Files liên quan**: `app/services/character_profile_service.py`, `tests/test_character_identity_timeline.py`

### EPUB Export Timeout & 404 Missing R2 CDN Cache
- **Ngày**: 2026-08-20
- **Vấn đề**: Tải file EPUB từ client Android/Web bị lỗi 404 Not Found từ Cloudflare R2 (`pub-*.r2.dev/novels/{novel_id}/full.epub`).
- **Root cause**: Backend tự động 307 Redirect đến URL CDN R2 `novels/{novel_id}/full.epub` khi cấu hình CDN mà không kiểm tra xem file đã tồn tại trên bucket R2 hay chưa (do truyện mới nạp hoặc chưa từng biên dịch file nguyên cuốn lên R2).
- **Fix**: 
  1. Thêm phương thức `storage_repo.file_exists_in_r2(key)` kiểm tra sự tồn tại trên R2 trước khi chuyển hướng.
  2. Nếu file chưa có trên R2 hoặc có cờ `force_rebuild=true`, backend tự động biên dịch EPUB từ các chương (`library_service.export_full_epub`), tự động nạp ảnh bìa (cover.jpg), đẩy file lên R2 cache (`upload_file_to_r2`), và trả về `FileResponse` cho client.
  3. Lưu song song `full.epub` ngay khi người dùng nạp file EPUB hoàn chỉnh (`import_epub_novel`).
- **Files liên quan**: `app/api/v1/library.py`, `app/core/storage.py`, `app/services/library_service.py`

### Non-ASCII UTF-8 Filename trong FileResponse Header
- **Ngày**: 2026-08-17
- **Vấn đề**: Tên truyện tiếng Việt (như *Cổ Chân Nhân.epub*) khiến trình duyệt mobile hoặc Starlette lỗi header `Content-Disposition`.
- **Fix**: Mã hóa RFC 5987: `headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(utf8_name)}"}`.
- **Files liên quan**: `app/api/v1/library.py`

## How-To

### Thêm LLM Provider Mới
- **Ngày**: 2026-08-10
- **Bước thực hiện**:
  1. Thừa kế `BaseLLMClient` trong `app/llm/base.py`.
  2. Implement 4 phương thức: `extract_book_bible_delta`, `translate_prose_chunk`, `translate_html_json`, `qa_check_chunk`.
  3. Đăng ký provider mới trong `create_llm_client` tại `app/llm/factory.py`.
- **Files liên quan**: `app/llm/base.py`, `app/llm/factory.py`

### Khởi Chạy Server & Web UI Test
- **Ngày**: 2026-08-10
- **Bước thực hiện**:
  1. Chạy `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
  2. Mở trình duyệt tại `http://127.0.0.1:8000/` để test Paste Text và Upload File (.txt / .epub).
- **Files liên quan**: `app/main.py`, `app/static/index.html`

### Phát hành API ra Internet qua Cloudflare Tunnel cho Mobile App
- **Ngày**: 2026-08-17
- **Bước thực hiện**:
  1. Chạy backend `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
  2. Chạy `tools\cloudflared.exe tunnel --url http://127.0.0.1:8000` (hoặc chạy file `start_tunnel.bat`).
  3. Lấy URL `https://*.trycloudflare.com` gán vào `BASE_URL` trong mã nguồn Mobile App.
- **Files liên quan**: `tools/cloudflared.exe`, `start_tunnel.bat`

### Tích hợp chapter-aware Book Bible API
- **Ngày**: 2026-08-17
- **Bước thực hiện**:
  1. Gọi `POST /api/v1/book-bible/books/resolve` với metadata/fingerprint.
  2. Tạo hoặc lấy edition bằng `POST /api/v1/book-bible/books/{book_id}/editions`.
  3. Gửi raw chapter hoặc structured events kèm `X-Idempotency-Key`.
  4. Poll `GET /api/v1/book-bible/submissions/{submission_id}` nếu raw chapter đang chạy AI.
  5. Đọc `GET /api/v1/book-bible/editions/{edition_id}/chapters/{local_chapter}/snapshot`.
- **Files liên quan**: `app/api/v1/character_profiles.py`, `app/schemas/character_profile.py`

### Bật trusted write trong môi trường triển khai
- **Ngày**: 2026-08-17
- **Bước thực hiện**:
  1. Đặt `BOOK_BIBLE_WRITE_TOKEN` trong secret manager.
  2. Client gửi header `X-Book-Bible-Client-Key` cho endpoint ghi.
  3. Dùng App Check/attestation thay static token khi phát hành rộng.
  4. Giữ token trống chỉ cho local development.
- **Files liên quan**: `app/api/v1/character_profiles.py`

## Patterns

### Asynchronous EPUB Import & Live Progress Polling
- **Ngày**: 2026-08-17
- **Chi tiết**: Tác vụ import EPUB chạy trên daemon background thread, trích xuất ảnh bìa và tách từng chương lưu vào R2 đồng thời cập nhật `ImportJobStatus` (`current_chapter`, `total_chapters`, `progress_percentage`, `current_step`). Web UI poll endpoint `GET /api/v1/library/import-jobs/{job_id}` mỗi 600ms để cập nhật giao diện người dùng.
- **Files liên quan**: `app/services/library_service.py`, `app/schemas/library.py`, `app/static/index.html`

### Conservative evidence-group review
- **Ngày**: 2026-08-17
- **Chi tiết**: Candidate không được duyệt chỉ vì nhiều lần gọi cùng model trên cùng chapter. Một evidence group là một edition lineage + chapter fingerprint; cần tối thiểu hai group độc lập và confidence đủ cao. Candidate chưa duyệt không xuất hiện trong shared snapshot.
- **Files liên quan**: `app/services/character_profile_service.py`, `tests/test_character_profile.py`

### Event schema mở rộng nhưng giữ backward compatibility
- **Ngày**: 2026-08-17
- **Chi tiết**: Thêm `character_events` là field optional vào `BookBibleDelta`; các payload extraction cũ vẫn hợp lệ. Service validate từng candidate bằng Pydantic rồi mới append canonical event. Event có `certainty`, `operation`, evidence và schema version để hỗ trợ correction/rebuild.
- **Files liên quan**: `app/schemas/book_bible.py`, `app/schemas/character_profile.py`, `app/services/character_profile_service.py`

### Gemini Pydantic Structured Outputs
- **Ngày**: 2026-08-10
- **Chi tiết**: Sử dụng `types.GenerateContentConfig` với `response_mime_type="application/json"` và `response_schema=PydanticModel` để ép Gemini API trả về JSON chuẩn theo Pydantic schema mà không cần regex parse.
- **Ví dụ code**:
  ```python
  response = self.client.models.generate_content(
      model=model_name,
      contents=prompt,
      config=types.GenerateContentConfig(
          system_instruction=system_prompt,
          response_mime_type="application/json",
          response_schema=BookBibleDelta
      )
  )
  return response.parsed
  ```
- **Files liên quan**: `app/llm/gemini_provider.py`

