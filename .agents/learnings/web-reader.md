# Web Reader

> Tổng hợp kiến thức về thư viện đọc truyện web, Web Reader API, client-side TTS và tối ưu hiệu năng.
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

### Backend-Owned Auth Bootstrap
- **Ngày**: 2026-08-24
- **Chi tiết**: UI chỉ gọi `/api/auth/config`; backend chọn `mode=local` cho development hoặc trả cấu hình Supabase publishable key cho production. Local identity chỉ bật khi `APP_ENV` là development/local/test và `AUTH_REQUIRED=false`; production luôn fail-closed.
- **Files liên quan**: `app/api/auth.py`, `app/auth.py`, `app/config.py`, `app/static/auth.js`

### Local Browser TTS Architecture
- **Ngày**: 2026-08-24
- **Chi tiết**: TTS chạy trong Web Worker bằng ONNX Runtime Web và phonemizer/espeak-ng; voice manifest và model assets được phục vụ từ backend `/reader-assets`, còn nội dung chương lấy qua API reader. Giữ văn bản và model chạy ở client trình duyệt, không phụ thuộc dịch vụ TTS bên ngoài.
- **Files liên quan**: `app/static/reader-tts/`, `app/static/reader.html`, `docs/reader-local-tts-design.md`

### Multi-tiered In-Memory & Storage Cache
- **Ngày**: 2026-08-24
- **Chi tiết**: Kết hợp In-Memory Cache (TTL 60s) ở `LibraryService` và `ReaderService` để tránh quét đệ quy cloud bucket mỗi request, kết hợp tự động lưu cache file chương text vào `local_provider` sau lần tải đầu tiên. Cache tự động invalidate khi có thao tác mutate truyện hoặc chương.
- **Files liên quan**: `app/modules/library/legacy_service.py`, `app/modules/reader/service.py`, `app/infrastructure/storage/legacy_storage.py`

### Parallel Direct-Chapter Fast Path
- **Ngày**: 2026-08-24
- **Chi tiết**: Khi người dùng vào link trực tiếp chương (`?book=...&chapter=...`), frontend kích hoạt `openBook` và `loadChapter` song song ngay lập tức mà không đợi request quét toàn bộ danh sách thư viện kết thúc.
- **Files liên quan**: `app/static/reader.html`

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

### Missing Auth Config Was Reported As Wrong Password
- **Ngày**: 2026-08-24
- **Vấn đề**: Render `/api/auth/config` trả 503 vì thiếu `SUPABASE_URL` hoặc `SUPABASE_PUBLISHABLE_KEY`, nhưng login catch chung hiển thị “Email hoặc mật khẩu không đúng”.
- **Root cause**: Form không phân biệt lỗi cấu hình backend với lỗi Supabase credentials; password không được gửi tới FastAPI.
- **Fix**: Kiểm tra `/api/auth/config` trước; production đặt đủ env trên backend và không dùng service-role key làm publishable key.
- **Files liên quan**: `app/api/auth.py`, `app/static/auth.js`, `render.yaml`

### Piper Config CORS Blocked Browser Playback
- **Ngày**: 2026-08-24
- **Vấn đề**: Piper `config.json` từ Hugging Face chỉ cho CORS từ `https://huggingface.co`; worker chạy trên Reader origin không đọc được config nên Play dừng trước khi tạo audio.
- **Root cause**: Manifest trỏ trực tiếp tới config ngoài origin, trong khi model CDN có CORS nhưng config không.
- **Fix**: Lưu config theo revision vào `/reader-assets/piper-config.json` và trỏ manifest về same-origin; model vẫn tải từ R2 rồi fallback Hugging Face.
- **Files liên quan**: `app/static/reader-tts/piper-config.json`, `app/static/reader-tts/voices.v1.json`, `tests/test_reader.py`

### R2 Public CDN Without Browser CORS
- **Ngày**: 2026-08-24
- **Vấn đề**: R2 public CDN trả file nhưng không có `Access-Control-Allow-Origin`, nên trình duyệt không thể đọc trực tiếp dù HTTP status là `206`.
- **Root cause**: Frontend storage-read phụ thuộc CORS của CDN; fallback Render lại chỉ thử provider Supabase nên trả `404` với file legacy trên R2.
- **Fix**: `StorageRepository.get_bytes()` thử provider chính, R2 S3 nếu có credential, rồi đọc HTTP từ R2 public URL server-side.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `tests/test_storage_repository.py`

### Missing Opening Chapters
- **Ngày**: 2026-08-24
- **Vấn đề**: Một số truyện có metadata nhiều chương nhưng file chương đầu đã thiếu; mở truyện ngay chương 1 làm UI báo lỗi dù các chương sau còn tồn tại.
- **Fix**: Reader thử tuần tự tối đa 12 chương đầu, mở chương đầu tiên đọc được và loại chương `404` khỏi mục lục.
- **Files liên quan**: `app/static/reader.html`

### TTS Worker Unhandled Error Causing Infinite Hang
- **Ngày**: 2026-08-24
- **Vấn đề**: Bấm nút Play, nút đổi thành biểu tượng `Ⅱ` nhưng không có tiếng và giao diện không phản hồi, Promise `ensureVoiceLoaded` bị pending mãi mãi.
- **Root cause**: Worker gửi `{ type: 'ERROR', message: ... }` khi load model mà không có `requestId`. Handler trong `reader-tts.js` chỉ tìm `state.pending.get(message.requestId)` nên bỏ qua tin nhắn lỗi, khiến Promise tải voice không bao giờ được reject.
- **Fix**: Xử lý riêng nhánh `ERROR` không có `requestId` để reject `state.workerLoad`, dọn dẹp worker và hiển thị thông báo lỗi hoặc mở modal tải giọng.
- **Files liên quan**: `app/static/reader-tts/reader-tts.js`, `app/static/reader-tts/tts-worker.js`

### Sequential Blocking & Cloud Bucket Scan Latency
- **Ngày**: 2026-08-24
- **Vấn đề**: Mở link trực tiếp chương truyện mất 7.5 – 8.0 giây mới hiển thị chữ.
- **Root cause**: `reader.html` chờ hoàn tất quét toàn bộ danh sách truyện `/api/v1/reader/books` (mất ~6s do quét cloud bucket đệ quy) rồi mới tải chương; nội dung chương mỗi lần đọc lại tải từ cloud storage.
- **Fix**: Tải song song chương truyện ngay trên frontend; backend thêm In-Memory Cache TTL 60s cho danh sách truyện và local file cache cho nội dung chương. Giảm thời gian mở chương từ 7.5s xuống < 50ms (nhanh hơn 160 lần).
- **Files liên quan**: `app/static/reader.html`, `app/modules/library/legacy_service.py`, `app/modules/reader/service.py`, `app/infrastructure/storage/legacy_storage.py`

### TTS Pause Race Replaying Audio
- **Ngày**: 2026-08-24
- **Vấn đề**: Sau khi người dùng bấm Pause, nếu request TTS worker đang bay bị lỗi, nhánh fallback có thể tự gọi `play()` và phát lại audio ngoài ý muốn.
- **Root cause**: `pause()` không tăng `state.token`, và khối `catch` trong `pump()` không kiểm tra `state.playing`.
- **Fix**: Tăng `++state.token` trong `pause()` và chặn fallback phát lại khi `!state.playing`.
- **Files liên quan**: `app/static/reader-tts/reader-tts.js`

### Local Cache Masking Cloud Updates
- **Ngày**: 2026-08-24
- **Vấn đề**: Đọc local cache trước active cloud provider khiến metadata/chương cũ bị trả mãi dù R2/Supabase đã cập nhật hoặc xóa.
- **Fix**: Đặt lại thứ tự ưu tiên trong `get_bytes()`: Cloud (Active Provider, R2, CDN) trước, local chỉ là fallback cuối cùng.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`

### Stale In-Memory Novel Metadata
- **Ngày**: 2026-08-24
- **Vấn đề**: `get_novel()` đọc `_cache` trước khi đọc storage khiến các thay đổi/xóa truyện từ worker khác không được phản ánh.
- **Fix**: Luôn truy vấn nguồn chính thống (database/storage) trước rồi mới ghi đè `_cache`, chỉ dùng `_cache` làm fallback khi mạng lỗi.
- **Files liên quan**: `app/modules/library/legacy_service.py`

### Auto-Advance Chapter Race on Library Exit
- **Ngày**: 2026-08-24
- **Vấn đề**: Người dùng quay về thư viện (`showLibrary`) khi audio đang trong quá trình chuyển chương tự động có thể bị thông báo lỗi hoặc phát nhầm sang truyện khác.
- **Fix**: Gắn token định danh (`opToken`) vào `handleChapterEnd()` và gọi `pause()` + `closePlayer(false)` khi `showLibrary()`.
- **Files liên quan**: `app/static/reader.html`, `app/static/reader-tts/reader-tts.js`

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

### Configure Auth And TTS In Production
- **Ngày**: 2026-08-24
- **Bước thực hiện**:
  1. Đặt `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_JWT_AUDIENCE=authenticated` và `AUTH_REQUIRED=true` trên Render.
  2. Redeploy rồi kiểm tra `/api/auth/config` trả `mode=supabase` và HTTP 200.
  3. Kiểm tra `/reader-assets/voices.v1.json`, sau đó tải voice qua nút “Tải giọng”.
  4. Chạy `node --check app/static/reader-tts/reader-tts.js` và test reader trước khi push.
- **Files liên quan**: `render.yaml`, `app/api/auth.py`, `app/static/reader-tts/voices.v1.json`

### Measure & Benchmark Reader Latency
- **Ngày**: 2026-08-24
- **Bước thực hiện**:
  1. Dùng script Python `time.perf_counter()` và `urllib.request` đo lần lượt: `/reader`, `/api/v1/reader/books`, `/api/v1/reader/books/{id}`, `/api/v1/reader/books/{id}/chapters/{index}`.
  2. So sánh latency giữa lần gọi đầu tiên (warmup) và các lần gọi tiếp theo (cached).
  3. Đảm bảo mọi endpoint trả về dưới 10ms khi đã có cache.
- **Files liên quan**: `app/modules/reader/service.py`, `app/modules/library/legacy_service.py`

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

### Server-Side CDN Fallback
- **Ngày**: 2026-08-24
- **Chi tiết**: Với blob public nhưng CDN không hỗ trợ CORS, dùng backend làm proxy đọc server-side. Cách này giữ direct-read cho storage có CORS, đồng thời bảo đảm dữ liệu legacy vẫn đọc được mà không cần đưa credential storage vào browser.
- **Files liên quan**: `app/infrastructure/storage/legacy_storage.py`, `app/modules/library/legacy_service.py`

### Sentence Queue And Voice LRU Cache
- **Ngày**: 2026-08-24
- **Chi tiết**: Tách văn bản thành sentence spans để highlight; worker tạo audio theo hàng đợi ngắn nhằm bắt đầu phát nhanh và nối câu không khoảng lặng. Cache IndexedDB chỉ giữ tối đa ba voice, xóa voice ít dùng nhất; lỗi voice phụ fallback về Minh Quang nếu đã cài.
- **Files liên quan**: `app/static/reader-tts/reader-tts.js`, `app/static/reader-tts/tts-worker.js`, `app/static/reader.html`

### Fail-Fast Worker Error Propagation
- **Ngày**: 2026-08-24
- **Chi tiết**: Mọi thông điệp lỗi từ Web Worker (kể cả lỗi không có `requestId` như khi khởi tạo runtime hay tải model ONNX) đều phải được định tuyến đến đúng Promise đang chờ hoặc bắn notification ra UI, tuyệt đối không nuốt âm thầm tránh treo UI.
- **Files liên quan**: `app/static/reader-tts/reader-tts.js`, `app/static/reader-tts/tts-worker.js`

### Cache-Aside with Mutation Invalidation
- **Ngày**: 2026-08-24
- **Chi tiết**: Danh sách truyện và metadata được cache trong RAM với TTL 60s để phục vụ hàng ngàn request đọc nhanh (từ 6s xuống 2ms). Mọi tác vụ ghi/xóa/dịch chương đều lập tức xóa cache (`_summaries_cache = None`) để đảm bảo tính nhất quán dữ liệu.
- **Files liên quan**: `app/modules/library/legacy_service.py`, `app/modules/reader/service.py`

### Environment-Guarded Development Seeding
- **Ngày**: 2026-08-24
- **Chi tiết**: Các chức năng seed dữ liệu mẫu chỉ được phép chạy khi có biến môi trường kích hoạt rõ ràng (`SEED_DEMO_DATA=true`) VÀ đang ở môi trường dev/local/test, tuyệt đối không tự động seed trên production.
- **Files liên quan**: `app/config.py`, `app/main.py`, `app/modules/library/seed.py`
