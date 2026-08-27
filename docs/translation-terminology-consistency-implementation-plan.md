# Implementation plan: nhất quán thuật ngữ dịch truyện

## Mục tiêu:

- Đảm bảo các tên riêng và thuật ngữ fantasy, đặc biệt tên ma thú/chủng tộc như `金毛暴熊 → Kim Mao Bạo Hùng`, được canonical hóa trước khi dịch và được kiểm tra trước khi publish.
- Thay giới hạn extraction `orig_content[:3000]` bằng pre-scan toàn chương có ngưỡng/sliding window.
- Bổ sung glossary do người dùng quản lý, terminology QA deterministic, một lượt correction và draft `NEEDS_REVIEW`.
- Bảo toàn bản publish cũ khi retranslation chưa vượt qua QA; draft không đi vào reader/EPUB.

## Phạm vi đã kiểm tra:

- Prompt và LLM adapters: `app/prompts/templates.py`, `app/llm/base.py`, `app/modules/shared/ports.py`, `app/llm/gemini_provider.py`, `app/llm/anthropic_provider.py`.
- Translation ownership: `app/modules/translation/application/qa_service.py`, `app/modules/translation/legacy_pipeline.py`, `app/modules/translation/application/facade.py`.
- Library chapter flow: `app/modules/library/application/chapter_service.py`, `app/modules/library/application/facade.py`, `app/modules/library/legacy_service.py`, `app/modules/library/api.py`.
- Book Bible: `app/modules/book_bible/schemas.py`, `app/modules/book_bible/legacy_service.py`, `app/modules/book_bible/domain/legacy_review_policy.py`, `app/modules/book_bible/api.py`, storage/repository implementations.
- Persistence và aggregate: `app/modules/library/persistence/legacy_models.py`, `app/modules/library/persistence/legacy_repository.py`, `app/infrastructure/storage/legacy_storage.py`.
- UI/batch/export: `app/static/index.html`, `app/modules/library/application/epub_export_service.py`, `app/modules/library/legacy_service.py`.
- Tests liên quan: `tests/test_bible_filter.py`, `tests/test_book_bible_fixes.py`, `tests/test_translation_support.py`, `tests/test_library_service.py`, `tests/test_llm_adapters.py`, `tests/test_review_regressions.py`.

## Nhận định:

- `PROMPT_2_TRANSLATE_CHUNK_SYSTEM` yêu cầu dùng `vi_name`, nhưng không định nghĩa rõ tên loài fantasy và không có cơ chế cưỡng chế sau model response.
- `PROMPT_1_EXTRACT_BOOK_BIBLE_DELTA` có `new_terms` nhưng under-specified đối với ma thú, chủng tộc và thực thể fantasy qua đường.
- Library flow trích `orig_content[:3000]`, sau đó dịch toàn bộ `orig_content`; đây là scope mismatch trực tiếp.
- `filter_bible_for_text()` hoạt động nếu term đã tồn tại; Bible trống là hậu quả của extraction/merge thiếu dữ liệu, không phải lỗi chính của filter.
- Bản dịch được ghi thẳng vào translated storage mà không gọi `QAService.verify_chunk()`.
- `QAService.fast_rule_check()` chỉ bắt source term còn lọt vào output; case `lông vàng Bạo Hùng` không bị phát hiện.
- Gemini extraction đang fail-open thành `BookBibleDelta()` cho parse/validation error, khiến application không biết scan incomplete.
- `TermEntry` không có alias/locked/forbidden variants; merge không tạo correction cho conflicting term `vi_name`.
- `ChapterStatus` chưa có `NEEDS_REVIEW`; translated aggregate hiện đếm theo `COMPLETED`, cần tách published state khỏi review state.

## Thay đổi/đề xuất:

### Giai đoạn 1 — Khóa regression bằng test trước

1. Thêm fixture `金毛暴熊` và fake LLM vào `tests/test_translation_support.py`:
   - Xác nhận QA cũ bỏ lọt `lông vàng Bạo Hùng` để mô tả regression hiện tại.
   - Định nghĩa behavior đích: canonical vắng mặt hoặc forbidden variant xuất hiện phải tạo `QAIssue`.
2. Mở rộng `tests/test_library_service.py`:
   - Term xuất hiện sau ký tự 3.000 phải được extraction trước translation.
   - Correction pass publish `COMPLETED`.
   - Correction fail lưu draft/report, đặt `NEEDS_REVIEW`, không ghi đè translated file cũ.
3. Mở rộng `tests/test_book_bible_fixes.py` và `tests/test_review_regressions.py`:
   - Locked term không bị LLM delta thay đổi.
   - Conflicting canonical term tạo pending change hoặc giữ canonical theo policy.
   - Mỗi mutation glossary tăng revision đúng một lần.

Gate: các test mới phải fail vì đúng lý do trước khi sửa production code.

### Giai đoạn 2 — Schema và Book Bible term policy

1. Sửa `app/modules/book_bible/schemas.py`:
   - Thêm default fields vào `TermEntry`: `aliases`, `forbidden_variants`, `locked`, `updated_by`, `updated_at`.
   - Thêm schema request/response cho term CRUD và pending term correction nếu đặt cùng bounded context.
2. Sửa `app/modules/book_bible/legacy_service.py`:
   - Match term bằng `original_name` và aliases.
   - Merge idempotent qua overlapping windows.
   - Không thay `vi_name` của locked/existing canonical term bằng LLM delta.
   - Trả conflict rõ ràng thay vì bỏ qua âm thầm.
3. Sửa `app/modules/book_bible/domain/legacy_review_policy.py`:
   - Bổ sung `canonical_term_correction` hoặc type tương đương cho term conflict.
   - Review approve mới được thay canonical `vi_name`.
4. Chuẩn hóa revision ownership trong `app/infrastructure/storage/legacy_storage.py` và `app/modules/book_bible/persistence/legacy_repository.py`:
   - Mỗi mutation persisted tăng `bible_revision` đúng một lần ở application/persistence boundary.
   - Tránh double increment với `HybridPolicyEngine.apply_delta()`.

Gate: Bible JSON cũ vẫn deserialize; tests merge/timeline hiện có vẫn pass.

### Giai đoạn 3 — Prompt và LLM contract

1. Refactor `app/prompts/templates.py`:
   - Thêm shared terminology policy text để prose/HTML prompt không lệch nhau.
   - Extraction bắt buộc nhận diện tên ma thú/yêu thú/linh thú/thần thú, chủng tộc và fantasy species vào `new_terms` với category chuẩn.
   - Translation nêu thứ tự ưu tiên glossary và phân biệt tên loài với mô tả ngoại hình; thêm ví dụ `金毛暴熊 → Kim Mao Bạo Hùng`.
   - Thêm correction prompt chỉ sửa các terminology mismatch được cung cấp, không viết lại tùy ý toàn chương.
2. Mở rộng `app/llm/base.py` và `app/modules/shared/ports.py` bằng `correct_translation_terms(...)`.
3. Implement contract trong `app/llm/gemini_provider.py` và `app/llm/anthropic_provider.py`.
4. Thêm provider-independent structured-output exception trong `app/llm/errors.py`:
   - Extraction parse/validation lỗi retry một lần.
   - Sau retry, raise typed error để application đánh dấu scan incomplete; không trả delta rỗng không dấu vết.
5. Cập nhật fake/mock LLM trong tests cho method mới.

Gate: adapter tests xác nhận preferred model, fallback/provider errors và correction structured output giữ nguyên semantics.

### Giai đoạn 4 — TerminologyConsistencyService và deterministic QA

1. Tạo `app/modules/translation/application/terminology_consistency_service.py` với các API nhỏ:
   - `build_windows(text, threshold, window_size, overlap)`.
   - `scan_chapter(source, bible, chapter_index, ...)` trả Bible tạm/cập nhật và scan status.
   - `expected_terms(source, bible)` match original names/source aliases.
   - `verify_translation(source, translated, expected_terms)` trả `QAReport`.
   - `correct_once(...)` và verify lại.
2. Mở rộng `app/modules/translation/schemas.py`:
   - Bổ sung optional `source_span`, `translated_span`, `term_original_name` cho `QAIssue` hoặc schema terminology-specific tương thích cũ.
   - Thêm result models cho scan/verification/correction để không truyền tuple rời rạc.
3. Sửa `app/modules/translation/application/qa_service.py`:
   - Giữ check source leakage.
   - Thêm canonical-presence và forbidden-variant checks.
   - Không so occurrence count tuyệt đối.
4. Wire service qua `app/modules/translation/application/facade.py`; tái sử dụng trong library flow và sau đó trong TXT/EPUB/direct flows để tránh hai implementation khác nhau.

Gate: deterministic QA không gọi LLM khi clean; mismatch chỉ tạo tối đa một correction call.

### Giai đoạn 5 — Orchestrate chapter translation và draft lifecycle

1. Refactor `app/modules/library/legacy_service.py::translate_chapter()` theo thứ tự:
   - Load source, previous published translation và previous chapter tail.
   - Pre-scan toàn source bằng `TerminologyConsistencyService`; persist Bible chỉ khi không phải preview.
   - Build filtered Bible/expected terms rồi translate.
   - Verify → correction once → verify.
   - Publish hoặc lưu draft/report theo kết quả.
   - Xóa biến `TranslationPipelineService` hiện được khởi tạo nhưng không dùng.
2. Thêm `ChapterStatus.NEEDS_REVIEW` trong `app/modules/library/schemas.py`.
3. Dùng deterministic keys:
   - `novels/{novel_id}/drafts/ch_NNNN.txt`.
   - `novels/{novel_id}/qa/ch_NNNN.json`.
4. Bổ sung helper trong `app/modules/library/application/chapter_service.py`/facade:
   - Đọc review detail.
   - Approve draft atomically thành published translation.
   - Reject/delete draft và report mà không ảnh hưởng published file.
5. Sửa `app/modules/library/persistence/legacy_repository.py` và aggregate trong legacy service:
   - `translated_chapters` đếm chapter có published translation key, không chỉ status `COMPLETED`.
   - Unknown legacy status vẫn fallback an toàn; `NEEDS_REVIEW` round-trip qua Postgres/legacy metadata.
6. Preview mode:
   - Dùng ephemeral merged Bible và trả QA report mở rộng.
   - Không persist Bible/draft/chapter metadata.

Gate: retranslation thất bại giữ nguyên reader content và existing EPUB source.

### Giai đoạn 6 — API và Web UI

1. Thêm term CRUD/lock endpoints trong `app/modules/book_bible/api.py`:
   - List/create/update/delete/lock term theo `novel_id`.
   - Tất cả mutation dùng `require_write_access`, validate input và tăng revision.
2. Thêm review endpoints trong `app/modules/library/api.py`:
   - GET review detail/draft.
   - POST approve draft.
   - DELETE hoặc POST reject draft.
3. Sửa `app/static/index.html`:
   - Book Bible terms có nút thêm/sửa/khóa, aliases và forbidden variants.
   - Chapter/batch queue hiểu `needs_review`, không tính là failed/completed.
   - Modal review hiển thị mismatch và cho sửa candidate trước khi approve.
   - Invalidates Bible/chapter/content caches sau mutation.
   - Auto rebuild chỉ lấy các chapter vừa publish thành công, bỏ draft.
4. Bảo đảm mọi dữ liệu term/issue render qua `escapeHtml` và không đưa API key vào report/log.

Gate: user có thể chốt `金毛暴熊 → Kim Mao Bạo Hùng`, khóa term, dịch lại chương lỗi và approve bản đạt QA từ UI.

### Giai đoạn 7 — Config, observability và rollout

1. Thêm settings trong `app/config.py`:
   - Enforcement mode `off|observe|enforce`.
   - Full-scan threshold, window size, overlap.
   - Maximum correction attempts, mặc định 1.
2. Thêm structured logs/timing:
   - Window count, extracted term count, Bible revision.
   - QA mismatch types, correction attempted/succeeded.
   - Published/draft outcome; không log raw API key hoặc toàn bộ chapter.
3. Rollout:
   - Chạy `observe` trên một tập chương đã dịch, thu false positives và seed forbidden variants.
   - Audit/correct Book Bible hiện tại, thêm `金毛暴熊 → Kim Mao Bạo Hùng` và lock.
   - Chuyển `enforce` khi regression suite và sample audit đạt.
   - Dịch lại hoặc review các chương đã chứa `金毛暴熊`; không global replace theo tiếng Việt.

Gate: metrics đủ để xác định latency/cost tăng thêm và tỷ lệ draft trước khi bật enforce toàn bộ.

## Validation:

- Đã chạy:
  - `python -m pytest tests/test_translation_support.py -vv` — 4 passed.
  - `python -m pytest tests/test_bible_filter.py tests/test_book_bible_fixes.py -q` — 4 passed.
  - Reproduction trực tiếp: Bible `金毛暴熊 → Kim Mao Bạo Hùng`, output `lông vàng Bạo Hùng` làm QA hiện tại trả `[]`.
- Cần chạy sau từng giai đoạn:
  - `python -m pytest tests/test_translation_support.py tests/test_bible_filter.py tests/test_book_bible_fixes.py -q`.
  - `python -m pytest tests/test_library_service.py tests/test_review_regressions.py -q`.
  - `python -m pytest tests/test_llm_adapters.py tests/test_pipeline.py tests/test_book_bible_enhancements.py -q`.
  - `python -m pytest -q` trước khi chuyển enforcement mode.
  - Manual UI smoke test: term CRUD/lock, batch translation, review/approve draft, reader và EPUB export.
  - Benchmark ít nhất các chapter cỡ nhỏ/trung bình/lớn; ghi input chars/tokens, extraction latency, total latency và correction rate.

## Rủi ro còn lại:

- Canonical-presence QA không chứng minh mọi lần xuất hiện đều đúng; tránh count tuyệt đối để không phạt đại từ/lược bỏ hợp lệ.
- Bible cũ có canonical sai có thể khiến enforce tạo output nhất quán nhưng vẫn sai; cần user audit và lock glossary quan trọng.
- Sliding-window delta có thể trùng/xung đột; merge/revision phải idempotent và concurrency-safe.
- `NEEDS_REVIEW` tác động tới aggregate, selection UI, cache và export; cần regression test toàn bộ các consumer của `ChapterStatus`.
- Correction prompt có thể thay đổi văn phong ngoài phạm vi nếu không ràng buộc chặt; deterministic recheck chỉ bảo vệ terminology.
- Provider/model fallback vẫn có thể làm văn phong khác nhau giữa chương, dù canonical terms được giữ.
- Observe mode cần thời gian thu dữ liệu; bật enforce ngay có nguy cơ tạo nhiều draft với Book Bible chưa được làm sạch.
