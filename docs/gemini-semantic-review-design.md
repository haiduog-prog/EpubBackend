# Gemini Semantic Review Design

## Tóm tắt

EpubBackend bổ sung một công đoạn semantic review sau khi Gemini hoàn tất dịch từng chương. Reviewer dùng một model Gemini khác model dịch, đối chiếu nguyên văn với bản dịch và chỉ đề xuất các patch cục bộ cho lỗi dịch sai hoặc dịch nhầm. Reviewer không được viết lại toàn chương hoặc biên tập văn phong.

Mục tiêu là bắt các lỗi mà QA rule hiện tại không phát hiện được, như đảo nghĩa, nhầm chủ thể, mất hoặc thêm nội dung, sai phủ định, số lượng, thời gian và phương hướng. Hệ thống vẫn giữ các rule Book Bible hiện tại làm lớp kiểm tra nhanh trước và sau semantic review.

## Phạm vi

- Review mọi chương ngay sau khi dịch xong.
- Xử lý tuần tự một chương tại một thời điểm.
- Chương thường dưới 5.000 ký tự.
- Chỉ tự áp dụng các patch có độ tin cậy cao.
- Hiển thị lỗi chưa chắc chắn trên UI.
- Không tích hợp OpenAI hoặc điều khiển Chrome.
- Không lưu bản dịch trước review hoặc lịch sử patch.

## Giả định

- Gemini tiếp tục là provider dịch và review.
- Model reviewer được cấu hình riêng và không được trùng model dịch.
- Confidence tối thiểu mặc định là `0.90`.
- Tổng vùng nội dung được phép sửa tối đa là `20%` độ dài bản dịch.
- Reviewer thất bại không làm mất bản dịch Gemini hiện tại.
- Nội dung truyện đã được phép gửi tới Gemini trong luồng dịch hiện có.

## Kiến trúc

Luồng xử lý mỗi chương:

```text
Nguyên văn
  -> Gemini dịch chính
  -> QA rule hiện tại
  -> Gemini semantic reviewer
  -> PatchValidator
  -> áp dụng patch an toàn
  -> chạy lại QA rule
  -> lưu bản cuối
```

Các thành phần mới:

- `SemanticReviewService`: điều phối semantic review ở cấp chương.
- `TranslationPatch`: structured schema cho từng thay đổi.
- Gemini provider method dành riêng cho semantic review.
- `PatchValidator`: xác thực patch trước khi thay đổi nội dung.
- Cấu hình reviewer độc lập qua environment variables.

Review chỉ chạy sau khi toàn bộ chương đã được ghép xong. Không gọi reviewer riêng cho từng câu hoặc HTML node, nhằm giới hạn mỗi chương ở một API call.

## Hợp đồng reviewer

Reviewer nhận:

1. Nguyên văn chương.
2. Bản dịch Gemini hiện tại.
3. Phần Book Bible liên quan tới chương.

Reviewer chỉ tìm lỗi sai nghĩa, nhầm chủ thể, mất hoặc thêm nội dung, sai phủ định, số lượng, thời gian, phương hướng, tên và thuật ngữ. Reviewer không sửa phong cách, dấu câu mang tính sở thích hoặc viết lại câu đã đúng.

Structured output:

```json
{
  "issues": [
    {
      "old_text": "đoạn chính xác trong bản dịch",
      "replacement": "đoạn đã sửa",
      "reason": "dịch nhầm hướng đông thành hướng tây",
      "confidence": 0.97
    }
  ]
}
```

## Xác thực và áp dụng patch

Một lượt review chỉ được áp dụng khi tất cả patch thỏa mãn:

- `confidence >= 0.90` đối với patch tự động.
- `old_text` tồn tại đúng một lần trong bản dịch.
- Patch không chồng lấn.
- `replacement` không rỗng.
- Tổng số ký tự thuộc các `old_text` không vượt quá `20%` độ dài bản dịch.
- Số issue không vượt giới hạn cấu hình.
- Kết quả không làm hỏng cấu trúc chương.

Backend chuẩn hóa newline nhưng không fuzzy-match nội dung. Vị trí patch được xác định trước, sau đó patch được áp dụng từ cuối văn bản về đầu để không làm lệch offset.

Nếu có bất kỳ patch không hợp lệ, không áp dụng một phần kết quả của lượt review. Các issue confidence thấp được giữ trong báo cáo và chương chuyển `needs_review`.

Sau khi áp dụng, hệ thống chạy lại QA rule hiện tại. Chỉ khi rule và các invariant đều đạt thì nội dung cuối mới được lưu.

## Trạng thái và dữ liệu

Metadata review của chương:

- `review_status`: `pending`, `reviewing`, `passed`, `needs_review`.
- `review_issues`: danh sách lỗi chưa được tự sửa.
- `reviewer_model`: model thực hiện review.
- `reviewed_at`: thời điểm hoàn tất.
- `review_error`: lỗi kỹ thuật rút gọn.

Chuyển trạng thái:

```text
translating -> reviewing -> completed
                       \-> needs_review
```

Chương chuyển `needs_review` khi:

- Có issue confidence dưới ngưỡng.
- Patch không tìm thấy chính xác, xuất hiện nhiều lần hoặc chồng lấn.
- Tổng thay đổi vượt 20%.
- Reviewer timeout, hết quota hoặc model không khả dụng.
- Structured output sai schema.
- QA rule sau sửa vẫn còn lỗi.

Nội dung dịch chỉ được cập nhật sau khi toàn bộ patch vượt validation. Theo quyết định sản phẩm, hệ thống chỉ giữ bản cuối và không lưu lịch sử patch hoặc bản trước review. Vì vậy không hỗ trợ rollback nội dung đã sửa tự động.

Model reviewer không được fallback sang model dịch. Nếu reviewer riêng không khả dụng, bản dịch hiện tại được giữ và chương chuyển `needs_review`.

## UI

- Danh sách chương hiển thị badge `Đã review` hoặc `Cần xem lại`.
- Chi tiết chương `needs_review` hiển thị trích đoạn, lý do và confidence của lỗi chưa được áp dụng.
- Patch đã áp dụng thành công không được lưu hoặc hiển thị vì hệ thống không giữ lịch sử thay đổi.

## Cấu hình

- `GEMINI_REVIEW_ENABLED`
- `GEMINI_REVIEW_MODEL`
- `GEMINI_REVIEW_MIN_CONFIDENCE=0.90`
- `GEMINI_REVIEW_MAX_CHANGE_RATIO=0.20`
- `GEMINI_REVIEW_TIMEOUT_SECONDS`
- `GEMINI_REVIEW_MAX_ISSUES`

Startup validation phải bảo đảm reviewer model khác translation model khi semantic review được bật.

## Xử lý lỗi và quan sát

Reviewer fail-open đối với nội dung nhưng fail-closed đối với trạng thái review: giữ bản dịch Gemini, không coi review đã vượt qua và chuyển `needs_review`.

Log chỉ ghi chapter ID, reviewer model, thời gian xử lý, số issue và kết quả validation. Không log toàn bộ nội dung truyện hoặc API key.

Metric tối thiểu:

- Thời gian review.
- Tỷ lệ `passed` và `needs_review`.
- Số patch được áp dụng.
- Lỗi structured output.
- Timeout, quota và provider errors.

## Kiểm thử

- Patch hợp lệ, không tìm thấy, xuất hiện nhiều lần và chồng lấn.
- Confidence dưới ngưỡng không được áp dụng.
- Tổng vùng sửa đúng 20% được chấp nhận; trên 20% bị từ chối.
- JSON lỗi, timeout hoặc quota error giữ nguyên bản dịch.
- Rule sau review còn lỗi chuyển `needs_review`.
- Reviewer không được dùng cùng model dịch.
- Luồng dịch hiện tại vẫn hoạt động khi reviewer bị tắt.
- Nội dung không bị cập nhật một phần khi một patch trong lượt review không hợp lệ.

## Triển khai

1. Bật chế độ quan sát: chạy reviewer và ghi issue nhưng chưa áp dụng patch.
2. Kiểm tra kết quả trên một tập chương thật và đo false positive.
3. Bật tự động áp dụng patch confidence cao.
4. Theo dõi tỷ lệ `needs_review`; tắt nhanh bằng feature flag nếu có bất thường.

## Nhật ký quyết định

1. **Dùng Gemini thay OpenAI.** Giữ một provider, giảm công sức tích hợp và không gửi nội dung sang nhà cung cấp mới.
2. **Reviewer dùng model khác model dịch.** Giảm khả năng reviewer lặp lại cùng lỗi của model dịch.
3. **Review mọi chương.** Rule-only không thể bắt đầy đủ lỗi ngữ nghĩa.
4. **Reviewer trả patch thay vì toàn chương.** Hạn chế viết lại không cần thiết và giảm output token.
5. **Chỉ tự sửa confidence cao.** Issue chưa chắc chắn được hiển thị để review thủ công.
6. **Giới hạn vùng sửa 20%.** Thay đổi lớn bị coi là dấu hiệu reviewer đang dịch lại hoặc kết quả không an toàn.
7. **Reviewer lỗi thì giữ bản dịch và chuyển `needs_review`.** Không làm mất nội dung nhưng không báo sai rằng review đã hoàn tất.
8. **Không lưu lịch sử patch hoặc bản trước sửa.** Giảm lưu trữ và độ phức tạp, đổi lại không có khả năng rollback.
9. **Triển khai qua observation mode trước.** Cho phép đo false positive trước khi bật tự động sửa.

## Rủi ro đã chấp nhận

- Confidence do model tự báo không phải xác suất được hiệu chỉnh; validation deterministic là lớp bảo vệ bắt buộc.
- Dùng cùng provider vẫn có thể tạo lỗi tương quan dù model khác nhau.
- Không lưu bản trước review nên không thể khôi phục tự động nếu patch sai.
- Rule và semantic reviewer vẫn có thể bỏ sót lỗi khó hoặc đánh dấu nhầm.
- Mỗi chương thêm một Gemini API call, làm tăng latency và chi phí.
