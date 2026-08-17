# Bản đồ tổng thể EpubBackend

> Cập nhật: 2026-08-12
>
> Phạm vi hiện tại là backend Python/FastAPI. Repository chưa có module Android; Android app sẽ đóng vai trò client gọi REST API.

## 1. Kiến trúc tổng quan

```mermaid
flowchart LR
    A[Android app / Web UI] --> B[FastAPI app.main]
    C[CLI app.cli] --> D[TranslationPipelineService]
    B --> E[/api/v1/translate]
    B --> F[/api/v1/book-bible]
    B --> G[/api/v1/qa]
    E --> D
    G --> H[QAService]
    D --> I[TXTChunker]
    D --> J[EPUBParser + HTMLMerger]
    D --> K[BookBibleService]
    D --> L[BaseLLMClient]
    H --> L
    L --> M[AnthropicProvider]
    L --> N[GeminiProvider]
    D --> O[StorageRepository]
    E --> O
    O --> P[In-memory cache]
    O --> Q[Firebase Firestore optional]
    O --> R[Cloudflare R2 helper optional]
    D --> S[storage/uploads + storage/outputs]
```

Luồng chính là:

`Client → API/CLI → TranslationPipelineService → Parser/Chunker → Book Bible → LLM provider → file output → StorageRepository`.

## 2. Bản đồ thư mục

| Khu vực | Trách nhiệm | File chính |
|---|---|---|
| `app/main.py` | Khởi tạo FastAPI, CORS, router, Web UI root | `app/main.py` |
| `app/api/v1/` | REST endpoints | `translate.py`, `book_bible.py`, `qa.py`, `router.py` |
| `app/services/` | Business flow và orchestration | `pipeline_service.py`, `book_bible_service.py`, `qa_service.py` |
| `app/parsers/` | Đọc/chia/đóng gói nội dung | `txt_chunker.py`, `epub_parser.py`, `html_merger.py` |
| `app/llm/` | Abstraction và adapter cho AI provider | `base.py`, `factory.py`, `anthropic_provider.py`, `gemini_provider.py` |
| `app/schemas/` | Pydantic contract và trạng thái job | `translation.py`, `book_bible.py` |
| `app/core/storage.py` | Job/Book Bible persistence và tích hợp cloud | `storage.py` |
| `app/prompts/` | Prompt dùng chung cho 4 tác vụ AI | `templates.py` |
| `app/static/` | Web UI test thủ công | `index.html` |
| `tests/` | Unit test cho pipeline, parser, Book Bible, alignment | `test_*.py` |
| `storage/` | File upload/output runtime | `uploads/`, `outputs/` |
| `.agents/` | Rule, learning và skill nội bộ của project | `rules/`, `learnings/`, `skills/` |

## 3. Các entry point

### REST server

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- `GET /`: trả Web UI test từ `app/static/index.html`.
- Router API dùng prefix `/api/v1`.

### CLI

```bash
python -m app.cli translate <input.txt|input.epub> -o <output> --provider anthropic
```

## 4. API contract cho Android client

### Dịch text trực tiếp

`POST /api/v1/translate/text`

Body JSON:

```json
{
  "text": "Nội dung cần dịch",
  "novel_id": "optional-novel-id",
  "provider": "anthropic",
  "model": "optional-model",
  "api_key": "optional"
}
```

Response gồm `translated_text` và `book_bible`. Có thể truyền `x-api-key`, `x-provider`, `x-model` thay cho một số field body.

### Dịch file TXT/EPUB

1. `POST /api/v1/translate/file` với multipart field `file`.
2. Nhận `TranslationJob` chứa `job_id` và trạng thái `pending`.
3. Poll `GET /api/v1/translate/job/{job_id}` để đọc `status`, `progress_percentage`, `current_step`.
4. Khi `status=completed`, tải file tại `GET /api/v1/translate/download/{job_id}`.

Trạng thái job: `pending → processing → completed|failed`.

### Book Bible

`GET /api/v1/book-bible/{job_id}` trả dictionary gồm nhân vật, địa danh, thuật ngữ và style guide.

### QA

`POST /api/v1/qa/check` nhận `original_text`, `translated_text`, `job_id` và các header provider/model. QA chạy rule-based trước; chỉ gọi AI nếu phát hiện bất thường hoặc được ép gọi.

## 5. Luồng nghiệp vụ

### Text trực tiếp

1. API tạo LLM client qua `create_llm_client()`.
2. Pipeline trích `BookBibleDelta` từ text mẫu.
3. `BookBibleService.merge_delta()` merge delta bằng code Python.
4. Lọc Book Bible theo nội dung text.
5. Gọi `translate_prose_chunk()` và trả text + Book Bible.

### TXT

1. Đọc toàn bộ file UTF-8.
2. `TXTChunker` chia theo đoạn, khoảng 1.500–3.000 từ/chunk, giữ khoảng 150 từ context trước đó.
3. Trích Book Bible từ hai chunk đầu.
4. Dịch tuần tự từng chunk.
5. Mỗi 5 chunk trích delta mới và merge vào Book Bible.
6. Ghép các bản dịch bằng `

` và ghi vào `storage/outputs`.

### EPUB

1. `EPUBParser` đọc các document HTML trong EPUB.
2. `HTMLMerger` gom mỗi block ngữ nghĩa thành một `HTMLInputItem`, giữ ID node.
3. Mỗi chapter được chia sub-batch tối đa 40 item để gọi structured output.
4. Provider căn chỉnh lại output theo đầy đủ ID; item bị LLM bỏ sót sẽ fallback về text gốc.
5. `HTMLMerger` thay text trong cây HTML nhưng giữ media element.
6. `EPUBParser.rebuild_epub()` đóng gói lại EPUB đầu ra.

## 6. Book Bible và LLM abstraction

`BookBible` là state nhất quán xuyên suốt quá trình dịch:

- `characters`: tên dịch, vai trò, ghi chú giọng văn, quy tắc xưng hô.
- `places`: địa danh và tên dịch.
- `terms`: thuật ngữ, cảnh giới, môn phái, vật phẩm.
- `style_guide`: thể loại, tone, bối cảnh.

`BaseLLMClient` cố định bốn capability:

1. `extract_book_bible_delta()`
2. `translate_prose_chunk()`
3. `translate_html_json()`
4. `qa_check_chunk()`

Thêm provider mới cần implement interface này và đăng ký trong `app/llm/factory.py`. Prompt nằm tập trung trong `app/prompts/templates.py`.

## 7. Persistence và cấu hình

- Mặc định job và Book Bible nằm trong memory của process.
- When Firebase is configured, Firestore is the primary store for translation_jobs and book_bibles; memory is fallback only.
- Firebase Storage is intentionally not used because the project avoids the Blaze billing requirement.
- Input and output files remain local in storage/; file sync is a separate phase.
- API key/provider/model có thể đến từ request hoặc `.env`.

Các biến cấu hình chính: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, Firebase credentials/bucket, Cloudflare R2 credentials/bucket/public URL và các default model/chunking settings.

## 8. Bản đồ tích hợp Android

Android nên tách ba lớp:

```text
UI/ViewModel
  → Repository
    → Retrofit API service
      → EpubBackend REST API
```

Contract nên biểu diễn bằng sealed state:

```text
Idle → Uploading → Processing(progress, currentStep) → Completed(downloadUrl)
                                      └──────────────→ Failed(message)
```

Lưu ý triển khai client:

- Upload dùng `multipart/form-data`, không gửi EPUB/TXT dưới dạng JSON.
- Poll job bằng coroutine có timeout và retry; hiện backend chưa có WebSocket/SSE.
- Download response là binary file; tên file hiển thị nên lấy từ metadata hoặc giữ tên `dich_<filename>`.
- `InputType.HTML` có trong schema nhưng endpoint file hiện chỉ chấp nhận `.txt` và `.epub`.
- Nếu backend quản lý API key bằng `.env`, Android không nên nhúng secret provider vào APK.
- Base URL cần tách theo build variant; CORS không phải cơ chế bảo mật cho Android native.

## 9. Điểm cần xử lý trước production

1. `asyncio.create_task()` chạy job trong cùng process; restart/crash sẽ mất task. Nên chuyển sang worker/queue và persistent job store.
2. In-memory cache khiến nhiều instance không nhìn thấy cùng job. Cần Firestore/DB làm nguồn sự thật duy nhất.
3. Chưa có endpoint cancel/retry job và chưa thấy cơ chế dọn file upload/output cũ.
4. QA chưa được gọi tự động trong `translate_txt_file()` hoặc `translate_epub_file()`; hiện QA là endpoint riêng.
5. `allow_origins=["*"]` kết hợp `allow_credentials=True` cần thu hẹp khi deploy.
6. Kiểm tra quy trình bảo mật của `serviceAccountKey.json` và toàn bộ secret trước khi đưa lên CI/CD; không log API key.
7. Bổ sung API integration tests cho upload → poll → download và lỗi provider/quota, vì test hiện chủ yếu là unit test parser/service.
8. Chuẩn hóa encoding UTF-8 của source/message nếu các chuỗi tiếng Việt bị hiển thị dạng mojibake trong môi trường chạy.

## 10. Điểm bắt đầu khi debug

| Triệu chứng | Kiểm tra đầu tiên |
|---|---|
| Không tạo được client AI | `app/llm/factory.py`, API key và provider/model |
| Dịch TXT sai mạch | `TXTChunker`, `previous_context`, prompt 2 |
| EPUB vỡ layout | `HTMLMerger.extract_semantic_nodes()` và `reconstruct_html()` |
| Thiếu tên/thuật ngữ | `BookBibleService.filter_bible_for_text()` và merge delta |
| Job đứng pending/processing | `asyncio.create_task()`, `StorageRepository`, process lifecycle |
| Không tải được file | `translated_file_path`, thư mục `storage/outputs`, trạng thái job |
| Gemini lỗi model/quota | fallback trong `GeminiProvider._call_with_fallback()` |

