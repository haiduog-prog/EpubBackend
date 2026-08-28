# Fix Translation CJK Leakage

## Goal

Ngăn Book Bible đưa xưng hô tiếng Trung vào prompt dịch, không publish/cache output còn chữ Hán, và tự hiệu đính tối đa một lần trước khi chuyển sang `NEEDS_REVIEW`.

## Tasks

- [x] 1. Khóa regression trong `tests/test_translation_regression_fixes.py`: dùng đúng đoạn `萧炎/药老`, fake extractor trả `老师/好小子/老夫`, và chứng minh output hiện tại bị QA bỏ lọt. Verify: test mới fail đúng vì CJK không được phát hiện.

- [x] 2. Sửa contract trong `app/prompts/templates.py`: quy định `self_term`/`other_term` luôn là tiếng Việt; chỉ `counterpart_original_name`, `counterpart_text` và `evidence` được giữ chữ gốc; thêm ví dụ `老师 -> sư phụ`, `老夫 -> lão phu`. Verify: `tests/test_prompts.py` khóa các yêu cầu và ví dụ này.

- [x] 3. Thêm policy kiểm tra address term trước merge trong Book Bible domain: CJK trong `self_term`/`other_term` không được thành rule confirmed; dữ liệu lỗi được đánh dấu pending hoặc bỏ khỏi Bible hiệu lực. Verify: `tests/test_book_bible_enhancements.py` xác nhận term tiếng Việt được nhận và term CJK bị chặn.

- [x] 4. Sửa `app/modules/book_bible/domain/legacy_address_resolver.py`: resolve `counterpart_id` sang tên canonical, không ưu tiên `counterpart_text`, và group legacy/new observations về cùng character ID để loại rule trùng. Verify: `tests/test_book_bible_timeline.py` không còn `with=老师`/`with=好小子` và chỉ còn một rule cho mỗi cặp nhân vật.

- [x] 5. Mở rộng deterministic QA trong `app/modules/translation/application/qa_service.py` và `terminology_consistency_service.py`: phát hiện span CJK còn sót, source address term bị copy, canonical/forbidden mismatch; không gọi AI khi output sạch. Verify: exact reproduction báo đủ `老师`, `好小子`, `老夫`, còn bản dịch sạch trả `issues=[]`.

- [x] 6. Hoàn thiện correction contract đã được thiết kế nhưng chưa triển khai: thêm `correct_translation_terms(...)` vào `app/llm/base.py`, `app/modules/shared/ports.py`, Gemini và Anthropic adapters; dùng `PROMPT_5_CORRECT_TERMINOLOGY`; giới hạn đúng một correction call. Verify: `tests/test_llm_adapters.py` và fake LLM xác nhận provider giữ nguyên nội dung ngoài các issue.

- [x] 7. Wire luồng `translate -> verify -> correct once -> verify` vào direct text, TXT, EPUB và library chapter; dùng một application service chung. Output vẫn lỗi không được publish, ghi cache hoặc báo `COMPLETED`; direct API trả `qa_status`/`qa_issues`, library dùng `NEEDS_REVIEW`. Verify: `tests/test_pipeline.py`, `tests/test_library_service.py` và API tests bao phủ clean, corrected và failed cases.

- [x] 8. Harden cache và dữ liệu cũ: thêm `translation_policy_version` + `qa_status=passed` vào `DirectTranslationCache`; cache cũ tự miss; tạo `scripts/repair_cjk_address_terms.py` có dry-run để phát hiện/làm sạch Bible lỗi, tăng `bible_revision`, rồi vô hiệu cache liên quan. Verify: `tests/test_translation_support.py` xác nhận cache lỗi không được đọc/ghi và migration không sửa Bible sạch.

- [x] 9. Verification cuối: chạy nhóm test hồi quy và toàn bộ `python -m pytest -q`; compileall, smoke HTTP root/OpenAPI và API regression bằng fake LLM đều đạt. Migration dữ liệu chạy dry-run, chưa `--apply`.

## Done When

- [x] `老师`, `好小子`, `老夫` không xuất hiện trong bản dịch tiếng Việt hoặc Book Bible hiệu lực.
- [x] Model fallback vẫn được phép, nhưng mọi model đều đi qua cùng quality gate.
- [x] Bản dịch chưa đạt QA không được cache/publish và có trạng thái review rõ ràng.
- [x] Bible/cache cũ được xử lý có dry-run, không xóa mù dữ liệu người dùng.

## Notes

- Không coi việc đổi thứ tự model pool là fix chính; `gemini-flash-lite-latest` chỉ làm lộ rõ prompt/Bible sai.
- Triển khai theo thứ tự trên; task 1-5 là critical path, task 6-8 phụ thuộc deterministic QA đã ổn định.
