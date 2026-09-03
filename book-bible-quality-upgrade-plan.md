# Nâng Cấp Book Bible Và Quality Gate Dịch Truyện

## Goal

Nâng Book Bible thành nguồn canonical có kiểm duyệt cho thuật ngữ, cảnh giới, xưng hô và văn phong; mọi chương chỉ được publish khi giữ đúng nghĩa, đúng cấu trúc, sạch watermark và vượt qua QA. Baseline `van-thu-chien-than`: Bible revision 7 hiện chỉ có 9 nhân vật/4 địa danh/16 thuật ngữ, dữ liệu bắt đầu chủ yếu từ chương 155 nên chưa đủ bảo vệ dải 120–154; đồng thời khắc phục triệt để hiện tượng mất tiêu đề chương (154, 159, 161), sót watermark convert (156–158) và lệch trạng thái giữa Storage và Database.

## Tasks

- [ ] 1. Khóa bộ lỗi hồi quy trong `tests/fixtures/translation_quality/van_thu_0120_0129.json` và `tests/test_translation_quality_regressions.py`: dùng bản sao các đoạn tối thiểu cho lỗi `phi hành thuật -> phù thủy`, `Mộc Linh -> Mộc Cảnh Nam`, `Khí Võ Cảnh -> Tụ Võ Cảnh`, đổi `huynh/muội -> anh/em`, `Cửu Transfer`, ký tự Arabic/Greek, lặp từ, mất tiêu đề chương và sót rác watermark converter (`read.st`, `------oOo------`). → Verify: từng fixture có mã lỗi mong đợi và test đỏ đúng lý do, không đọc/ghi `storage/`.

- [ ] 2. Nâng `BookBible` lên schema v3 trong `app/modules/book_bible/schemas.py`: thêm `source_profile` (`language`, `mode=translate|post_edit`), `scan_state`, policy văn kể/đại từ/đăng ký lời thoại/cấu trúc chương vào `StyleGuide`, `narrative_term` cho nhân vật, và `family`/`rank_order`/`evidence`/`confidence` cho thuật ngữ. Viết migration tương thích ngược trong `app/modules/book_bible/domain/schema_migration.py`. → Verify: payload v2 hiện tại load được với default an toàn, round-trip v3 không mất alias, address timeline hoặc lock.

- [ ] 3. Siết canonical merge trong `app/modules/book_bible/legacy_service.py` và review policy: LLM không được tự thay `vi_name`; canonical khác biệt phải thành `pending_change`; entry đã duyệt được `locked`; biến thể sai đi vào `forbidden_variants`; nguồn `vi_machine` không được bịa tên Hán không có evidence. → Verify: `Khí Võ Cảnh` đã khóa không thể bị delta đổi thành `Tụ Võ Cảnh`, còn đề xuất hợp lệ chỉ có hiệu lực sau approve và tăng `bible_revision`.

- [ ] 4. Bổ sung Book Bible Editor & Tiện ích Backup qua `app/modules/book_bible/api.py` và `app/static/index.html`: sửa/khóa thuật ngữ, chọn source mode, cấu hình ngôi kể và xưng hô theo cặp nhân vật/chapter, duyệt conflict; thêm 2 nút **"📥 Tải Book Bible (JSON)"** và **"📤 Nhập Book Bible (JSON)"** để backup/restore giữa các môi trường; mọi mutation validate schema, tăng revision và làm cache cũ tự miss. → Verify: chỉnh một term/style hoặc import JSON trên UI, reload vẫn giữ dữ liệu; request thiếu quyền ghi bị từ chối.

- [ ] 5. Đồng bộ DB ↔ Storage & Xây luồng backfill trong `scripts/reconcile_storage_chapters.py`, `app/modules/translation/application/terminology_consistency_service.py` và `scripts/backfill_book_bible.py`:
  - *Bước 5a (Reconciliation)*: Quét thư mục `storage/.../translated/` đồng bộ lại trạng thái `chapters` trong SQLite/PostgreSQL cho 43 chương hiện hữu của `van-thu-chien-than` (có chế độ `--dry-run` và `--apply`).
  - *Bước 5b (Coverage/Backfill)*: Quét tuần tự, resumable, có `--from/--through/--dry-run`, cảnh báo gap thay vì dịch với Bible thiếu lịch sử. Chạy dry-run riêng cho `van-thu-chien-than` đến chương 162, seed/duyệt các entry cốt lõi như `Khí Võ Cảnh`, `Ngưng Võ Cảnh`, `Tử Kỳ Lân`, `Thạch Nhũ`, cùng timeline `Đỗ Phong ↔ Mộc Linh`.
  → Verify: DB phản ánh đúng số chương đã dịch; báo cáo coverage không còn khoảng trống trước chương kế tiếp; chạy lại dry-run cho kết quả idempotent.

- [ ] 6. Tách Tiêu đề chủ động, lọc Watermark và nâng cấp Prompt trong `app/modules/book_bible/legacy_service.py`, `address_resolver.py`, `app/parsers/` và `app/prompts/templates.py`:
  - *Tiền xử lý (Sanitizer)*: Tự động loại bỏ rác convert/watermark (`read.st`, `Nguồn:`, `------oOo------`) trước khi nạp prompt.
  - *Bảo toàn tiêu đề chủ động (Proactive Title)*: Code Python chủ động bóc tách dòng tiêu đề `Chương X: [Tên]` ra khỏi văn bản trước khi gọi LLM dịch, sau đó tự động ghép lại tiêu đề chuẩn xác 100% vào đầu bản dịch, loại bỏ hoàn toàn nguy cơ LLM nuốt tiêu đề.
  - *Prompt*: Filter mang theo counterpart quan hệ, global locked realm/style policy; phân biệt dịch Hoa→Việt với hậu biên tập Việt máy→Việt, cấm tự thêm vật thể/chủ thể, buộc giữ metadata/paragraph và hệ đại từ cổ phong đã khóa.
  → Verify: prompt snapshot của chương 122 chứa policy `hắn/nàng`, `huynh/muội`; bản dịch đầu ra luôn có dòng tiêu đề chuẩn và sạch hoàn toàn watermark.

- [ ] 7. Mở rộng deterministic QA trong `app/modules/translation/application/qa_service.py`: phát hiện Unicode ngoài tập cho phép (Arabic/Greek/CJK), token ngoại ngữ không nằm trong allowlist Bible, lặp từ, tiêu đề chương bị mất hoặc sai lệch so với metadata, term family/rank sai, forbidden variant, watermark còn sót và pronoun drift (đại từ hiện đại `anh ấy/cậu/gã đó` trong bối cảnh tiên hiệp); trả `QAIssue` có code/severity/location. → Verify: bắt được `tuوي`, `bầuσ`, `Cửu Transfer`, `ra ra`, mất tiêu đề 121–122, dính `read.st` và đổi `Khí Võ` thành `Tụ Võ`; bản sạch không false-positive.

- [ ] 8. Làm quality gate fail-closed, cách ly drafts và bảo vệ Quota LLM trong `app/modules/library/legacy_service.py`, `semantic_review_service.py` và các pipeline TXT/EPUB/direct:
  - *Semantic Reviewer*: Chạy qua port chung cho mọi provider, kiểm nhầm chủ thể/hành động/thêm-bớt nghĩa; hỗ trợ cấu hình `SEMANTIC_REVIEW_MODE` (`always`, `on_warning`, `manual_only`) để tránh cạn quota/rate limit khi dịch hàng loạt; bổ sung endpoint `POST /chapters/{index}/semantic-review` để re-check độc lập chương `NEEDS_REVIEW` mà không cần dịch lại từ đầu.
  - *Correction & Re-check*: Correction tối đa một lần rồi chạy lại cả deterministic lẫn semantic QA; timeout hoặc reviewer lỗi phải chuyển `NEEDS_REVIEW`.
  - *Cách ly lưu trữ & EPUB Gate*: Bản dịch chỉ được ghi vào `storage/.../translated/` khi đạt `COMPLETED` và `review_status == passed`. Chương `NEEDS_REVIEW` **bắt buộc chỉ lưu tại `storage/.../drafts/`**. EPUB Exporter và Fast Patch chỉ đọc từ `translated/`, tuyệt đối không đóng gói file từ `drafts/`.
  → Verify: lỗi `Mộc Linh -> Mộc Cảnh Nam` và `phi hành thuật -> phù thủy` không được publish/cache; chương chưa đạt không bao giờ lọt vào file EPUB xuất xưởng.

- [ ] 9. Verification cuối: chạy `.venv\Scripts\python.exe -m pytest -q`, `compileall`, chạy script reconcile và backfill, rồi preview-only dịch lại chương 120–129 và tạo báo cáo audit (không apply). So sánh hash trước/sau của `storage/novels`, `storage/uploads` và `data/local_db.sqlite3`. → Verify: 10/10 chương giữ title, 0 ký tự rác/foreign fragment/forbidden variant, 0 watermark sót, 0 sai term khóa hoặc pronoun policy, mọi semantic mutation fixture đều bị chặn; dữ liệu người dùng được bảo vệ nguyên vẹn.

## Done When

- [ ] Book Bible có coverage liên tục đến chương đang dịch và mọi canonical quan trọng đều có provenance, trạng thái review và lock rõ ràng.
- [ ] Không chương nào được publish vào `translated/` hoặc đóng gói vào EPUB khi QA/reviewer bị skip, lỗi hoặc chưa hoàn tất (chỉ nằm tại `drafts/`).
- [ ] 100% chương xuất bản giữ đúng tiêu đề chương và sạch hoàn toàn các dòng watermark converter.
- [ ] Preview chương 120–129 đạt toàn bộ regression gate; chỉ apply/rebuild EPUB sau khi người dùng duyệt.
- [ ] Database SQLite/PostgreSQL đồng bộ hoàn toàn với danh sách file thực tế trên storage.

## Notes

- Critical path: 1 → 2 → 3 → 5 (Reconcile + Backfill) → 6 → 7 → 8 → 9; task 4 (UI Editor + Backup) có thể làm song song sau schema v3.
- Không sửa trực tiếp bản dịch, Bible hay dữ liệu truyện hiện có trong lúc phát triển; fixture dùng `tmp_path`, migration/backfill/reconcile mặc định `--dry-run` và chỉ `--apply` sau khi duyệt báo cáo.
