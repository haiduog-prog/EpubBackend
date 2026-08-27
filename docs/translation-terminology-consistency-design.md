# Thiết kế nhất quán thuật ngữ dịch truyện

## Bối cảnh

Luồng dịch chương hiện tại chỉ trích xuất Book Bible từ `orig_content[:3000]` nhưng dịch toàn bộ chương. Khi một tên loài fantasy như `金毛暴熊` xuất hiện sau cửa sổ này, Book Bible không có canonical term để truyền cho model. Kết quả có thể thay đổi giữa các chương, ví dụ `lông vàng Bạo Hùng`, `tóc vàng bạo hùng` thay vì tên đã chốt `Kim Mao Bạo Hùng`.

Book Bible hiện cũng chỉ là chỉ dẫn mềm: bản dịch được publish ngay sau khi model trả kết quả, không có glossary QA bắt buộc. QA hiện có chủ yếu phát hiện tên gốc còn lọt trong bản dịch, chưa phát hiện một source term bị dịch thành biến thể khác canonical `vi_name`.

## Mục tiêu

- Tên riêng, tên loài ma thú/yêu thú/linh thú/thần thú, chủng tộc, địa danh và thuật ngữ fantasy phải nhất quán xuyên suốt truyện.
- `金毛暴熊` được canonical thành `Kim Mao Bạo Hùng` trong truyện đang xét.
- User glossary là nguồn có độ ưu tiên cao nhất và không bị LLM tự ghi đè.
- Chỉ bản dịch vượt qua terminology QA mới được publish vào reader/EPUB.
- Bản dịch chưa đạt vẫn được giữ làm draft để batch tiếp tục và người dùng có thể duyệt.

## Không nằm trong phạm vi

- Không dùng global regex để thay mọi cụm `lông vàng` hoặc `tóc vàng`.
- Không xây dựng translation memory/RAG tổng quát cho toàn bộ văn phong trong giai đoạn này.
- Không thay đổi provider retry/circuit breaker ngoài việc phân biệt provider error với structured-output error.
- Không bắt mọi danh từ chỉ động vật phải dùng Hán-Việt; quy tắc chỉ áp dụng cho tên loài/thực thể fantasy đã lexicalized hoặc đã có canonical glossary.

## Nguyên tắc dịch thuật

Thứ tự ưu tiên khi dịch:

1. Term do người dùng khóa trong glossary.
2. Canonical `vi_name` đã tồn tại trong Book Bible.
3. Term mới được pre-scan và merge hợp lệ trước khi dịch.
4. Quy tắc thể loại trong prompt.
5. Lựa chọn ngôn ngữ tự nhiên của model.

Tên loài fantasy dùng Hán-Việt nhất quán khi đã được xác định là tên riêng/tên loài, ví dụ `金毛暴熊 → Kim Mao Bạo Hùng`. Khi cùng chữ `毛` được dùng để mô tả ngoại hình động vật, bản dịch tự nhiên là `lông`; không dùng `tóc` cho động vật nếu ngữ cảnh không nhân hóa rõ ràng.

## Kiến trúc đề xuất

### 1. Pre-scan terminology

Thêm `TerminologyConsistencyService` trong translation application context. Service nhận source chapter, Book Bible hiện tại và LLM client, sau đó:

- Quét toàn bộ chương nếu độ dài dưới ngưỡng cấu hình.
- Với chương lớn, chia sliding window có overlap; không cắt cứng 3.000 ký tự.
- Gọi extraction cho từng window và merge delta deterministic.
- Đánh dấu scan là `complete` hoặc `incomplete`; structured-output parse lỗi được retry tối đa một lần và không bị biến thành delta rỗng không dấu vết.

Prompt extraction phải yêu cầu `new_terms` cho các nhóm `beast_species`, `spirit_beast`, `divine_beast`, `race`, `faction`, `artifact`, `skill` và kèm evidence/source span khi có thể.

### 2. Canonical glossary

Mở rộng `TermEntry` bằng các field backward-compatible:

- `aliases`: các dạng tên nguồn khác cùng canonical term.
- `forbidden_variants`: các bản dịch đã biết là sai hoặc không được phép.
- `locked`: term do người dùng khóa, LLM không được thay đổi.
- `updated_by`, `updated_at`: metadata audit tùy chọn.

Mọi mutation glossary tăng `bible_revision`. Khi delta đề xuất `vi_name` khác một canonical term đã tồn tại, hệ thống tạo pending term change hoặc bỏ qua nếu term đang locked; không âm thầm ghi đè và cũng không âm thầm nuốt xung đột.

### 3. Translation

Trước khi gọi model, service xác định `expected_terms` bằng cách match source chapter với `original_name` và source aliases trong Book Bible hiệu lực. Chỉ phần Bible liên quan được truyền vào prompt để giảm token và tăng độ nổi bật của canonical mapping.

Luồng đầu tiên giữ cách dịch toàn chương hiện tại để giới hạn regression. `previous_context` lấy phần cuối bản dịch chương liền trước nếu có; context chỉ để tham khảo và không được lặp lại trong output.

Prompt dịch nêu rõ:

- Book Bible/custom glossary có độ ưu tiên cao hơn yêu cầu dùng tiếng Việt thuần.
- Tên loài fantasy phải dùng nguyên canonical `vi_name`.
- Phân biệt tên loài lexicalized với mô tả ngoại hình thông thường.
- Không tạo cụm lai một phần thuần Việt, một phần Hán-Việt.

### 4. QA và correction

QA mặc định chạy deterministic, không gọi LLM khi bản dịch đạt:

- Source term xuất hiện nhưng canonical `vi_name` hoàn toàn vắng mặt là một mismatch.
- Bất kỳ `forbidden_variants` nào xuất hiện đều là mismatch.
- Tên nguồn còn lọt sang bản dịch tiếp tục được báo lỗi như hiện tại.

Không yêu cầu số lần xuất hiện source và target bằng tuyệt đối vì model có thể dùng đại từ hoặc lược bỏ hợp lệ. Khi có mismatch, gọi correction prompt đúng một lần với source, bản dịch hiện tại và canonical map cần sửa, sau đó chạy deterministic QA lần nữa.

### 5. Publish và draft

- QA đạt: ghi atomically vào `novels/{novel_id}/translated/ch_NNNN.txt`, cập nhật `COMPLETED`.
- QA không đạt hoặc terminology scan incomplete: ghi candidate vào `novels/{novel_id}/drafts/ch_NNNN.txt`, ghi report vào `novels/{novel_id}/qa/ch_NNNN.json`, cập nhật `NEEDS_REVIEW`.
- Nếu chương đã có bản publish, draft mới không được ghi đè file published; reader và EPUB tiếp tục dùng bản publish cũ.
- Khi người dùng duyệt draft, hệ thống publish draft, cập nhật metadata và dọn draft/report tương ứng.

`translated_chapters` phải phản ánh số chương có published translation, không chỉ dựa vào status, để một retranslation draft không làm mất số liệu của bản publish cũ.

### 6. API và UI

Book Bible API cung cấp thao tác list/create/update/delete/lock term, tất cả mutation dùng `require_write_access`. Library API cung cấp translation-review detail và approve/reject draft bằng deterministic storage keys.

UI Book Bible cho phép sửa `original_name`, canonical `vi_name`, aliases, forbidden variants và trạng thái locked. Danh sách chương hiển thị `NEEDS_REVIEW`; modal review hiển thị source span, found/expected, candidate translation và thao tác sửa/duyệt.

## Xử lý lỗi

- Provider quota/timeout/unavailable tiếp tục dùng typed provider errors hiện có và truyền đúng HTTP status.
- Structured-output parse error có error type riêng, retry một lần, sau đó tạo draft/incomplete scan thay vì giả vờ extraction thành công với delta rỗng.
- Term conflict không tự ghi đè canonical.
- Correction failure không làm mất bản publish cũ.
- Preview mode không persist Bible, draft hoặc chapter metadata; có thể dùng Bible merge tạm thời để tạo preview và QA report.

## Yêu cầu phi chức năng

- Extraction threshold, window size, overlap và maximum correction attempts phải cấu hình được.
- Ghi timing/count metrics cho số window, term phát hiện, QA mismatch, correction và trạng thái publish/draft.
- Deterministic QA không làm phát sinh model call khi bản dịch đạt.
- Dữ liệu từ UI được validate độ dài, chuẩn hóa khoảng trắng và escape khi render.
- Field schema mới có default để Book Bible cũ deserialize được.
- Rollout nên hỗ trợ `observe` trước `enforce`: observe chỉ ghi QA metrics/report; enforce mới chặn publish và tạo draft.

## Chiến lược kiểm thử

- Unit: windowing/overlap, source-term matching, locked merge, pending conflict, revision increment, deterministic QA.
- Prompt/adapter: extraction nhóm ma thú; structured-output retry; correction prompt trên Gemini và Anthropic.
- Service: term sau ký tự 3.000 vẫn được pre-scan; correction thành công; correction thất bại tạo draft; retranslation không ghi đè published file.
- API/persistence: glossary authorization, backward compatibility, `NEEDS_REVIEW`, approve/reject draft, translated count.
- UI/export: hiển thị review state; cache invalidation; EPUB chỉ đọc published translation.

## Giả định

- Batch translation tiếp tục chạy tuần tự như hiện tại.
- Chấp nhận tối đa một model correction call bổ sung cho chương có mismatch.
- Phần lớn chương có độ dài khoảng 4.000–10.000 ký tự, nhưng implementation không phụ thuộc vào giả định này.
- User glossary được xem là quyết định biên tập cuối cùng.

## Rủi ro

- Presence check có thể false-positive/false-negative khi model chủ động lược danh từ; vì vậy chỉ dùng để gate những canonical term thực sự match source, không so count tuyệt đối.
- Window overlap có thể tạo delta trùng; merge phải idempotent.
- Thêm `NEEDS_REVIEW` ảnh hưởng UI filter, aggregate count và batch rebuild.
- Model fallback có thể làm văn phong khác nhau, nhưng không được phép thay canonical term.
- Rollout enforce ngay có thể tạo nhiều draft cho Bible cũ thiếu dữ liệu; cần observe/audit trước.

## Decision log

| ID | Quyết định | Phương án khác | Lý do |
|---|---|---|---|
| D1 | Hybrid có kiểm soát | Strict fail-closed; prompt-only | Batch vẫn tiến triển nhưng bản lỗi không được publish |
| D2 | User glossary ưu tiên cao nhất | Cho LLM tự cập nhật canonical | Đảm bảo quyền biên tập và tính lặp lại |
| D3 | QA đạt mới publish | Lưu trực tiếp rồi audit sau | Ngăn lỗi lọt vào reader/EPUB |
| D4 | Draft lưu tách khỏi published translation | Ghi đè rồi rollback | Bảo toàn bản dịch tốt trước đó |
| D5 | Full scan có ngưỡng, sau đó sliding window | Cắt 3.000; gửi không giới hạn | Không bỏ sót nhưng kiểm soát latency/cost |
| D6 | Deterministic QA + tối đa một correction | AI QA mọi chương; regex replacement | Giảm chi phí và tránh sửa nhầm mô tả thông thường |
| D7 | Không so count source/target tuyệt đối | Bắt số lần xuất hiện bằng nhau | Cho phép đại từ và lược bỏ hợp lệ |
| D8 | Rollout observe rồi enforce | Bật enforce ngay | Đo false positive và làm sạch Bible cũ trước khi gate production |
