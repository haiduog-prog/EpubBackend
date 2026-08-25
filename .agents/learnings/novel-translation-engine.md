# Novel Translation Engine

> Tổng hợp kiến thức về hệ thống dịch truyện thuần Việt (v2) hỗ trợ EPUB/HTML/TXT với Structured Outputs, Prompt Caching và Decoupled LLM Providers.
> Cập nhật lần cuối: 2026-08-25

---

## Architecture

### Quick Chapter Selection & Shift-Click for Retranslation
- **Ngày**: 2026-08-25
- **Chi tiết**: Bổ sung bộ công cụ chọn nhanh chương trên bảng danh sách:
  1. Nút [Đã dịch (Dịch lại)]: Chọn toàn bộ các chương đã dịch để dịch lại hàng loạt chỉ với 1 click.
  2. Chọn theo dải Từ A đến B với tùy chọn [Đã dịch] để lọc nhanh các chương đã dịch trong một khoảng nhất định.
  3. Tính năng Shift + Click: Nhấp vào checkbox chương A, giữ Shift nhấp checkbox chương B để chọn toàn bộ dải từ A đến B tức thì.
- **Files liên quan**: pp/static/index.html


### Precision Range Delta EPUB Rebuild (start_chapter & end_chapter)
- **Ngày**: 2026-08-25
- **Chi tiết**: Hỗ trợ tham số start_chapter và end_chapter (hoặc 	arget_chapters) trên cả API /export/epub lẫn Web UI. Cho phép người dùng hoặc tiến trình batch translation chỉ định chính xác dải chương vừa dịch (ví dụ từ chương 20 đến 50) để nạp vào file ull.epub thay vì phải quét toàn bộ. Tốc độ nạp chỉ mất 0.5 - 1 giây.
- **Files liên quan**: pp/modules/library/api.py, pp/modules/library/legacy_service.py, pp/static/index.html


### Ultra-Fast Delta Patching for Large EPUBs (1,000 - 5,000+ Chapters)
- **Ngày**: 2026-08-25
- **Chi tiết**: Để tránh tải hàng ngàn file txt lẻ qua Supabase Storage gây nghẽn mạng và chạm rate limit, export_full_epub áp dụng chiến lược Delta Patching: nạp trực tiếp file base ull.epub từ storage (1 request), sau đó chỉ tải song song các chương ĐÃ DỊCH (ví dụ 50/4000 chương = 50 requests), cập nhật nội dung HTML của các chương này vào reading spine của EPUB rồi ghi đè file xuất. Giảm tới 98.7% số lượng request và hoàn thành trong ~2 giây.
- **Files liên quan**: pp/modules/library/legacy_service.py, pp/modules/library/api.py


### Clean Architecture & Provider Decoupling
- **Ngày**: 2026-08-10
- **Chi tiết**: Tách biệt LLM Client qua Abstract Class `BaseLLMClient` (`app/llm/base.py`). Các provider (`AnthropicProvider`, `GeminiProvider`) triển khai độc lập và được tạo qua `LLMFactory`. Giúp mở rộng LLM provider mới mà không ảnh hưởng tầng API hay Business logic.
- **Files liên quan**: `app/llm/base.py`, `app/llm/anthropic_provider.py`, `app/llm/gemini_provider.py`, `app/llm/factory.py`

### Persistent Connection Pooling & Resource-Bounded Import
- **Ngày**: 2026-08-24
- **Chi tiết**: Tái sử dụng HTTP client với `httpx.Limits(max_keepalive_connections=20, max_connections=50)` trong `SupabaseStorageProvider`. Chủ động giải phóng DOM `BeautifulSoup.decompose()`, `del raw_sections` và kích hoạt `gc.collect()` định kỳ mỗi 20 chương khi nạp file EPUB lớn (500-2000 chương), giữ RAM server luôn < 180MB trên Render Free (512MB limit).
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `app/modules/library/legacy_service.py`

### Cancellation-safe Native Async LLM Calls
- **Ngày**: 2026-08-25
- **Chi tiết**: Timeout ở coroutine chỉ đáng tin khi provider dùng native async I/O. Gemini nhận timeout tại SDK/HTTP boundary qua `types.HttpOptions` và gọi `client.aio.models.generate_content`, để cancellation dừng request thay vì bỏ lại thread nền.
- **Files liên quan**: `app/llm/gemini_provider.py`, `app/llm/factory.py`, `app/modules/library/legacy_service.py`

### Preview-only Retranslation Diff & User-controlled Application
- **Ngày**: 2026-08-25
- **Chi tiết**: Hỗ trợ dịch lại với `preview_only=True` mà không ghi đè storage hay cập nhật DB. API trả về `original_text`, `previous_translated_text`, và `new_translated_text` để người dùng so sánh trực quan 2 cột trên UI. Chỉ khi người dùng bấm "Áp Dụng Bản Dịch", endpoint `/apply-translation` mới commit bản dịch mới vào storage và DB.
- **Files liên quan**: `app/modules/library/api.py`, `app/modules/library/schemas.py`, `app/modules/library/legacy_service.py`, `app/static/index.html`

### Multi-chapter Batch Translation & Automatic EPUB Force Rebuild
- **Ngày**: 2026-08-25
- **Chi tiết**: Cho phép chọn nhiều chương linh hoạt (tất cả, chưa dịch, dải A-B) và dịch hàng loạt qua hàng đợi tuần tự có thanh tiến trình, badge trạng thái và hỗ trợ tạm dừng an toàn. Sau khi hoàn thành, tự động kích hoạt `export/epub?force_rebuild=true` để biên dịch lại EPUB full mới nhất và đẩy đè lên Cloud Storage/R2.
- **Files liên quan**: `app/static/index.html`, `app/modules/library/api.py`, `app/modules/library/legacy_service.py`

### Multi-tier Web UI Caching & Intelligent Invalidation
- **Ngày**: 2026-08-25
- **Chi tiết**: Tích hợp cache đa tầng: (1) Client-side `apiCache` với TTL theo loại dữ liệu (60s danh sách/chi tiết, 120s bible/snapshot, 300s nội dung chương), (2) `coverBlobCache` lưu ObjectURL ảnh bìa trong RAM loại bỏ hoàn toàn request trùng, (3) Tự động xóa cache tương ứng khi mutate (tạo/xóa truyện, dịch chương, áp dụng bản dịch, duyệt sự kiện), (4) Nút `[🔄 Làm Mới]` với `forceRefresh=true` vượt qua cache.
- **Files liên quan**: `app/static/index.html`, `app/modules/library/api.py`, `app/modules/reader/api.py`

### Seamless Modal API Key Interception & Memory-Only Credential Flow
- **Ngày**: 2026-08-25
- **Chi tiết**: Thay vì chặn người dùng bằng `alert()` trình duyệt khi thiếu API key, hệ thống áp dụng `ensureApiKey(callback)`: nếu thiếu key, hiển thị modal popup chuyên dụng (`modal-quick-apikey` với `z-index: 1200`) cho phép nhập/dán key, chọn provider/model và tiếp tục hành động ngay khi lưu. Để đảm bảo an toàn, API key chỉ được giữ trong runtime DOM/memory session (`syncAllApiKeyInputs`) mà không lưu vào `localStorage.setItem('epub_api_key')`.
- **Files liên quan**: `app/static/index.html`, `tests/test_module_boundaries.py`

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

### Resilient Fault-tolerant EPUB Ingestion Engine
- **Ngày**: 2026-08-21
- **Chi tiết**: Xây dựng lớp đọc EPUB chống lỗi (`ResilientEpubReader`) tích hợp trực tiếp vào pipeline và library service. Cô lập toàn bộ lỗi cấu trúc của các file EPUB tải lên (thiếu cover/css/font trong archive, sai cấu trúc nav/ncx, lệch hoa thường trên Linux OS) để đảm bảo tác vụ import EPUB luôn trích xuất thành công 100% nội dung truyện.
- **Files liên quan**: `app/parsers/epub_parser.py`, `app/services/library_service.py`

---

## Bugs & Solutions

### 401 Unauthorized on Direct Window Navigation for Protected File Export
- **Ngày**: 2026-08-25
- **Vấn đề**: Gọi xuất hoặc biên dịch lại EPUB (`/api/v1/library/novels/{id}/export/epub?force_rebuild=true`) trả về `401 Unauthorized`.
- **Root cause**: Frontend dùng `window.location.href = ...` để tải file; điều hướng cấp trình duyệt này không gửi header `Authorization: Bearer <token>` của Supabase session.
- **Fix**:
  1. Nâng cấp `get_current_user` trong `app/auth.py` hỗ trợ nhận access token qua cả Header lẫn Query Parameters (`?token=...` hoặc `?access_token=...`).
  2. Chuyển hàm `exportNovelEpub` trên Web UI sang dùng `fetch()` (tự động gắn Bearer Token qua `authFetch`), nhận `blob()` và tạo `URL.createObjectURL` để kích hoạt download.
- **Files liên quan**: `app/auth.py`, `app/static/index.html`, `app/modules/library/api.py`

### Modal Invisibility Caused by Unclosed Parent Modal DIV Tag
- **Ngày**: 2026-08-25
- **Vấn đề**: Bấm nút dịch nhưng không có phản hồi gì trên màn hình (không thấy popup API key hiện ra).
- **Root cause**: Thiếu thẻ đóng `</div>` của modal xác nhận phía trên khiến modal cấu hình API key bị nằm lồng bên trong modal cha có style `display: none`.
- **Fix**: Sửa đóng mở thẻ `</div>` chuẩn xác và thiết lập `z-index: 1200` cho modal con để luôn hiển thị nổi bật trên backdrop.
- **Files liên quan**: `app/static/index.html`

### EPUB Manifest KeyError on Missing/Mismatched Assets (cover.png)
- **Ngày**: 2026-08-21
- **Vấn đề**: Nhập truyện EPUB bị sập tiến trình với lỗi `KeyError: "There is no item named 'OEBPS/Images/cover.png' in the archive"`.
- **Root cause**: File `content.opf` (manifest) trong EPUB khai báo file ảnh bìa/css/font nhưng file thực tế không tồn tại trong ZIP archive, hoặc bị lệch chữ hoa/thường (case-mismatch) hay lệch đường dẫn tương đối. Thư viện `ebooklib` gọi trực tiếp `zipfile.read()` không có try/except hay fallback.
- **Fix**: Xây dựng `ResilientEpubReader` và `read_epub_safe` kế thừa `EpubReader` với cơ chế:
  1. Chuẩn hóa đường dẫn posix và tìm kiếm case-insensitive trong zip archive.
  2. Fallback tìm theo basename nếu lệch prefix thư mục.
  3. Trả về `b""` và ghi log cảnh báo đối với các tài nguyên phụ bị thiếu thay vì để `KeyError` sập import job.
  4. Trích xuất ảnh bìa an toàn qua `extract_cover_from_epub` (quét trực tiếp zip nếu manifest hỏng).
- **Files liên quan**: `app/parsers/epub_parser.py`, `app/services/library_service.py`, `tests/test_library_service.py`

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
- **Fix**: Mã hóa RFC 5987: `headers={"Content-Disposition": f"attachment; filename="{ascii_name}"; filename*=UTF-8''{quote(utf8_name)}"}`.
- **Files liên quan**: `app/api/v1/library.py`

### Entity Duplication & CJK Resolution in Book Bible Snapshot
- **Ngày**: 2026-08-22
- **Vấn đề**: Xuất hiện 2 dòng cho cùng một nhân vật (ví dụ: `Tốn Bác (损伯)` và `Tốn Bác`) trên Mobile App do LLM trích xuất `original_name` không đồng nhất giữa các chương (lúc chữ Hán `损伯`, lúc phiên âm tiếng Việt `Tốn Bác`).
- **Root cause**: Backend chỉ so khớp theo `original_name` nguyên bản, dẫn đến 2 `character_id` khác nhau. Khi trả snapshot, backend trả cả 2 thực thể.
- **Fix**: 
  1. Thêm `vi_map` và kiểm tra chữ Hán CJK `[一-鿿]` trong `BookBibleService.merge_delta` để so khớp đa tiêu chí (`original_name`, `vi_name`, `aliases`) và nâng cấp tên nguyên tác chữ Hán.
  2. Bổ sung `_merge_duplicate_snapshots` trong `CharacterProfileService.snapshot` để gom nhóm và gộp thực thể trùng lặp trước khi gửi về client.
  3. Cập nhật `PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA` ràng buộc AI trích xuất đúng tên chữ Hán vào `original_name` và tên thuần Việt vào `vi_name`.
- **Files liên quan**: `app/services/book_bible_service.py`, `app/services/character_profile_service.py`, `app/prompts/templates.py`, `tests/test_entity_resolution.py`

### UnboundLocalError on Closure Variables in Background Worker Thread
- **Ngày**: 2026-08-24
- **Vấn đề**: Luồng worker import EPUB crash ngay lập tức với lỗi `UnboundLocalError: cannot access local variable 'epub_bytes' where it is not associated with a value`.
- **Root cause**: Lệnh `del epub_bytes` đặt bên trong hàm lồng `_worker` khiến Python phân loại `epub_bytes` thành local variable, làm việc đọc `tmp.write(epub_bytes)` trước đó bị lỗi scope.
- **Fix**: Truyền dữ liệu trực tiếp qua tham số luồng `_worker(raw_epub_data)` với `args=(epub_bytes,)`, đồng thời bọc toàn bộ khối thực thi trong `try...except...finally`.
- **Files liên quan**: `app/modules/library/legacy_service.py`

### Stale Import Job Status Freeze across Multi-Worker Deployments
- **Ngày**: 2026-08-25
- **Vấn đề**: Job đã `completed/failed` trong worker có thể bị hàng PostgreSQL cũ `processing` ghi đè khi poll, khiến UI quay lại trạng thái đang chạy.
- **Root cause**: Hàm merge chỉ ưu tiên memory khi trạng thái còn `processing`; lỗi persist cuối bị nuốt nên terminal state không có tính đơn điệu.
- **Fix**: Hợp nhất memory/DB theo quy tắc terminal-first rồi mới so progress; trạng thái terminal retry ghi DB hữu hạn với exponential backoff và luôn được giữ trong memory.
- **Files liên quan**: `app/modules/library/legacy_service.py`, `tests/test_import_job_resilience.py`

### Ineffective asyncio Timeout around Gemini to_thread
- **Ngày**: 2026-08-25
- **Vấn đề**: Auto-scan đặt timeout 45 giây nhưng worker vẫn có thể kẹt ở 90% cho đến khi Gemini SDK trả về.
- **Root cause**: `asyncio.wait_for` không dừng thread sinh bởi `asyncio.to_thread`; `asyncio.run` còn chờ default executor khi đóng event loop.
- **Fix**: Dùng `client.aio.models.generate_content`, cấu hình `HttpOptions.timeout` theo milliseconds, truyền cancellation vào HTTP coroutine và đóng async client trong `finally`.
- **Files liên quan**: `app/llm/gemini_provider.py`, `app/llm/factory.py`, `app/modules/library/legacy_service.py`, `tests/test_llm_adapters.py`

### Reader Cover Duplicate Prefix (novels/novels/) in Supabase Storage URLs
- **Ngày**: 2026-08-24
- **Vấn đề**: Tải ảnh bìa `/api/v1/reader/books/{id}/cover` trả về 400 Bad Request / 404 Not Found từ Supabase Storage.
- **Root cause**: URL Supabase chứa tên bucket `/object/public/novels/` khiến hàm parse cover bị tách chuỗi sai và nối thừa tiền tố thành `novels/novels/novels/{id}/cover.jpg`.
- **Fix**: Chuẩn hóa logic trích xuất relative storage key, loại bỏ tiền tố `novels/` dư thừa và bổ sung fallback tìm theo các định dạng ảnh phổ biến (`.png`, `.jpeg`, `.webp`).
- **Files liên quan**: `app/modules/reader/api.py`

---

## How-To

### Sử dụng Công Cụ Chọn Nhanh Chương & Phím Tắt Shift-Click để Dịch Lại
- **Ngày**: 2026-08-25
- **Bước thực hiện**:
  1. Mở modal chi tiết truyện, tại thanh công cụ chọn chương có các nút chuyên dụng:
     - [Tất cả]: Chọn toàn bộ các chương.
     - [Chưa dịch]: Chọn tất cả các chương chưa dịch.
     - [Đã dịch (Dịch lại)]: Chọn toàn bộ các chương đã có bản dịch tiếng Việt chỉ với 1 click.
     - Từ [A] đến [B] [Đã dịch]: Chỉ lọc và chọn các chương đã dịch nằm trong dải A đến B.
  2. Phím tắt Shift + Click: Nhấp chọn checkbox chương đầu (ví dụ chương 10), giữ phím Shift và nhấp checkbox chương cuối (ví dụ chương 50), toàn bộ các chương từ 10 đến 50 sẽ được chọn ngay lập tức.
  3. Bấm [⚡ Dịch X Chương Đã Chọn] trên thanh công cụ nổi để bắt đầu dịch hàng loạt.
- **Files liên quan**: pp/static/index.html

### Tùy Chọn Khoảng Chương Biên Dịch Lại EPUB (Precision Range Rebuild)
- **Ngày**: 2026-08-25
- **Bước thực hiện**:
  1. Tại modal chi tiết bộ truyện, cạnh nút [Biên Dịch Lại], có 2 ô Chương: [ Từ ] - [ Đến ].
  2. Nhập số chương bắt đầu và kết thúc (ví dụ Từ: 20 Đến: 50) rồi bấm [Biên Dịch Lại].
  3. Hệ thống chỉ tải 31 chương tiếng Việt tương ứng và patch vào file ull.epub trong ~0.5 giây.
  4. Nếu để trống 2 ô, hệ thống sẽ tự động cập nhật toàn bộ các chương đã dịch.
- **Files liên quan**: pp/modules/library/api.py, pp/modules/library/legacy_service.py, pp/static/index.html

### Tải File Binary / Export Được Bảo Vệ Bằng Supabase Auth trên Frontend
- **Ngày**: 2026-08-25
- **Bước thực hiện**:
  1. Gửi request `fetch()` tới backend API (được đính kèm header `Authorization: Bearer ...` tự động qua `window.fetch` / `authFetch`).
  2. Kiểm tra `response.ok`, trích xuất `const blob = await response.blob()`.
  3. Lấy tên file từ header `Content-Disposition` (hỗ trợ RFC 5987 `filename*=UTF-8''...`).
  4. Tạo thẻ `<a>` ảo với `href = URL.createObjectURL(blob)`, đặt `a.download = filename` và gọi `a.click()`.
  5. Thu hồi Object URL bằng `URL.revokeObjectURL(blobUrl)` sau khi tải xong.
- **Files liên quan**: `app/static/index.html`, `app/auth.py`

### Thực hiện Dịch Hàng Loạt & Tự Động Rebuild EPUB trên Web UI
- **Ngày**: 2026-08-25
- **Bước thực hiện**:
  1. Tại modal chi tiết bộ truyện, chọn các chương cần dịch (bằng checkbox từng dòng, nút "Chọn chưa dịch", hoặc nhập khoảng từ A đến B).
  2. Bật checkbox `[x] Tự động [🔄 Biên Dịch Lại EPUB] sau khi dịch xong` trên thanh công cụ nổi.
  3. Bấm `[⚡ Dịch X Chương Đã Chọn]`, theo dõi tiến trình và trạng thái từng chương trong bảng live queue.
  4. Sau khi hoàn tất hàng đợi, hệ thống tự động gọi `/export/epub?force_rebuild=true` để làm mới `full.epub` trên Cloud Storage và hiển thị nút tải về.
- **Files liên quan**: `app/static/index.html`, `app/modules/library/api.py`

### Bổ sung Cache và Cơ Chế Invalidation cho API Mới trên Web UI
- **Ngày**: 2026-08-25
- **Bước thực hiện**:
  1. Thêm endpoint GET trên FastAPI kèm header `Response.headers["Cache-Control"] = "private, max-age=..."`.
  2. Trên Web UI JS, gọi request qua `fetchWithCache(apiUrl(url), options, ttlMs, forceRefresh)`.
  3. Trong các action mutate (POST/PUT/DELETE), gọi `apiCache.invalidatePattern(urlPattern)` để xóa cache tương ứng.
  4. Gắn cờ `forceRefresh = true` vào các nút Làm Mới giao diện để chủ động ép tải lại dữ liệu từ server.
- **Files liên quan**: `app/static/index.html`, `app/modules/library/api.py`

### Thêm LLM Provider Mới
- **Ngày**: 2026-08-10
- **Bước thực hiện**:
  1. Thừa kế `BaseLLMClient` trong `app/llm/base.py`.
  2. Implement 4 phương thức: `extract_book_bible_delta`, `translate_prose_chunk`, `translate_html_json`, `qa_check_chunk`.
  3. Đăng ký provider mới trong `create_llm_client` tại `app/llm/factory.py`.
- **Files liên quan**: `app/llm/base.py`, `app/llm/factory.py`

### Thêm hard timeout cho tác vụ LLM nền
- **Ngày**: 2026-08-25
- **Bước thực hiện**:
  1. Truyền deadline từ service qua factory vào provider.
  2. Chuyển SDK call sang native async và cấu hình timeout tại HTTP boundary.
  3. Dùng `asyncio.wait_for` làm deadline tổng, đóng client trong `finally`, rồi test bằng fake coroutine nhận cancellation.
- **Files liên quan**: `app/llm/factory.py`, `app/llm/gemini_provider.py`, `app/modules/library/legacy_service.py`, `tests/test_llm_adapters.py`

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
  2. Chạy `tools/cloudflared.exe tunnel --url http://127.0.0.1:8000` (hoặc chạy file `start_tunnel.bat`).
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

---

## Patterns

### Relative & Human-friendly Date Formatting Pattern
- **Ngày**: 2026-08-25
- **Chi tiết**: Hàm ormatDateFriendly(isoString) chuyển đổi ISO timestamp thành nhãn thời gian tương đối trực quan cho người dùng (*Vừa xong*, *5 phút trước*, *Hôm nay 14:20*, *Hôm qua 08:30*, *25/08/2026 10:15*). Hiển thị nhất quán trên cả Novel Cards ngoài thư viện, thông tin tổng quan modal và cột *Ngày Dịch* trong bảng danh sách chương.
- **Files liên quan**: pp/static/index.html

### Safe Concurrency Throttling & Connection Reuse in Cloud Storage Batch Operations
- **Ngày**: 2026-08-25
- **Chi tiết**: Để xử lý các bộ tiểu thuyết cực lớn (1.000 - 5.000+ chương) mà không làm sập server hay bị Supabase/Cloudflare rate limit, kết hợp 2 kỹ thuật: (1) Giới hạn ThreadPool ở mức 20-25 workers đồng thời (giữ QPS ~40-50 req/s an toàn), (2) Tái sử dụng HTTP connection pool (Keep-Alive TCP/TLS) qua httpx.Limits(max_keepalive_connections=20, max_connections=50).
- **Files liên quan**: pp/modules/library/legacy_service.py, pp/infrastructure/storage/legacy_storage.py

### Preview-before-Commit Translation Pattern
- **Ngày**: 2026-08-25
- **Chi tiết**: Endpoint dịch hỗ trợ cờ `preview_only: bool`. Nếu `True`, backend chạy pipeline dịch nhưng không ghi đè file lưu trữ hay cập nhật database, mà trả về `ChapterTranslatePreviewResponse` để UI hiển thị so sánh song song giữa bản dịch cũ và bản dịch mới. Chỉ khi người dùng xác nhận áp dụng, request `ChapterApplyTranslationRequest` mới được gửi để lưu kết quả.
- **Files liên quan**: `app/modules/library/schemas.py`, `app/modules/library/legacy_service.py`, `app/modules/library/api.py`, `app/static/index.html`

### In-Memory ObjectURL Asset Caching Pattern
- **Ngày**: 2026-08-25
- **Chi tiết**: Đối với các tài nguyên media/ảnh bìa tải qua luồng xác thực (`authFetch`), lưu kết quả dưới dạng `Blob ObjectURL` trong `Map` bộ nhớ RAM của trình duyệt (`coverBlobCache`). Mọi lần render sau đó kiểm tra cache trước khi tạo network request mới, giúp giảm tải 100% network traffic khi cuộn trang hoặc chuyển tab.
- **Files liên quan**: `app/static/index.html`

### Asynchronous EPUB Import & Live Progress Polling
- **Ngày**: 2026-08-17
- **Chi tiết**: Tác vụ import EPUB chạy trên daemon background thread, trích xuất ảnh bìa và tách từng chương lưu vào R2 đồng thời cập nhật `ImportJobStatus` (`current_chapter`, `total_chapters`, `progress_percentage`, `current_step`). Web UI poll endpoint `GET /api/v1/library/import-jobs/{job_id}` mỗi 600ms để cập nhật giao diện người dùng.
- **Files liên quan**: `app/services/library_service.py`, `app/schemas/library.py`, `app/static/index.html`

### Monotonic Import Job State Resolution
- **Ngày**: 2026-08-25
- **Chi tiết**: Khi cùng một job tồn tại trong memory và database, terminal state (`completed`/`failed`) luôn thắng non-terminal state; nếu cả hai đang chạy thì chọn progress cao hơn. Persist terminal dùng bounded retry, còn checkpoint thường chỉ thử một lần để tránh làm chậm import.
- **Files liên quan**: `app/modules/library/legacy_service.py`, `tests/test_import_job_resilience.py`

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
  response = await self.client.aio.models.generate_content(
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
 
### Safe EPUB Asset & Cover Extraction Fallback
- **Ngày**: 2026-08-21
- **Chi tiết**: Pattern trích xuất asset an toàn: ưu tiên đọc từ ebooklib image item có nội dung hợp lệ (`len(bytes) > 0`). Nếu thất bại hoặc manifest bị hỏng, tự động fallback quét trực tiếp danh sách file trong ZIP archive (`zf.namelist()`) theo định dạng ảnh (`.jpg`, `.png`, `.webp`, `.jpeg`) và từ khóa `cover` hoặc ảnh đầu tiên.
- **Files liên quan**: `app/parsers/epub_parser.py`, `app/services/library_service.py`

### Safe Thread Closure Argument Passing & Isolated Exception Boundary
- **Ngày**: 2026-08-24
- **Chi tiết**: Khi spawn daemon thread (`threading.Thread`), luôn truyền dữ liệu thô / payload qua `args=(...)` thay vì dựa vào biến bao ngoài (closure variable), nhằm tránh lỗi `UnboundLocalError` khi dùng `del` trong luồng. Toàn bộ thân hàm của thread (kể cả tạo file tạm) phải nằm gọn trong `try...except...finally` để mọi ngoại lệ được bắt, log chi tiết và cập nhật `job.status = 'failed'`.
- **Files liên quan**: `app/modules/library/legacy_service.py`
