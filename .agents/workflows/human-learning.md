---
description: Tạo file giải thích chi tiết dạng "coffee talk" để developer hiểu sâu task vừa thực hiện
---

# Human Learning Workflow

Sau khi hoàn thành task, AI viết một file markdown chi tiết giải thích toàn bộ quá trình bằng ngôn ngữ dễ hiểu — như một người bạn giỏi đang giải thích qua ly cà phê, không phải sách giáo khoa.

---

## Bước 1: Xác định nội dung

AI quét toàn bộ conversation hiện tại, xác định:
- Task đã thực hiện là gì
- Các quyết định đã đưa ra
- Các vấn đề đã giải quyết
- Kết quả cuối cùng

## Bước 2: Viết file learning

Tạo file tại: `.agents/learnings/human/FOR_DEVELOPER_YYYY-MM-DD_[tên-task-ngắn].md`

File **PHẢI** bao gồm đầy đủ 9 phần sau, viết bằng giọng văn tự nhiên, dùng ví dụ thực tế và analogies:

### Phần 1: Approach & Reasoning
> Tôi đã chọn cách tiếp cận nào và tại sao? Điểm xuất phát là gì? Tôi đã xem xét gì trước tiên?

Giải thích logic suy nghĩ từ đầu đến cuối. Không chỉ nói "tôi đã làm X", mà phải nói "tôi đã làm X vì Y, và điều đó ảnh hưởng đến Z".

### Phần 2: Roads Not Taken
> Những cách tiếp cận khác đã xem xét nhưng từ bỏ? Tại sao loại bỏ chúng? Chúng sai ở đâu?

**Đây là phần quan trọng nhất** — hiểu những con đường không đi giúp developer học được nhiều nhất. Giải thích rõ tại sao mỗi alternative bị loại.

### Phần 3: How Things Connect
> Các phần trong công việc kết nối với nhau như thế nào?

Nếu có plan, structure, hoặc flow — show cách mỗi mảnh ghép khớp với nhau và tại sao theo thứ tự đó. Dùng analogy nếu cần.

### Phần 4: Tools & Methods
> Dùng tools, methods, hoặc frameworks nào? Tại sao cụ thể những cái đó mà không phải cái khác?

Giải thích điều gì sẽ thay đổi nếu chọn khác.

### Phần 5: Tradeoffs
> Đã ưu tiên gì và hy sinh gì?

Mọi quyết định đều có chi phí — show cả hai mặt. Ví dụ: "Chọn WorkManager nghĩa là đánh đổi real-time accuracy lấy battery efficiency".

### Phần 6: Mistakes & Dead Ends
> Những sai lầm, ngõ cụt, hoặc rẽ sai đã gặp? Fix như thế nào?

**Không giấu những phần lộn xộn** — sự lộn xộn là nơi kiến thức sống. Kể lại quá trình debug, cái gì đã thử mà không work.

### Phần 7: Future Pitfalls
> Cạm bẫy nên cẩn thận nếu làm điều tương tự trong tương lai?

Đưa ra lời khuyên dạng "Ước gì ai đó nói cho tôi điều này sớm hơn".

### Phần 8: Expert vs Beginner
> Một expert sẽ nhận ra điều gì mà beginner sẽ bỏ lỡ?

Show sự khác biệt giữa tư duy tốt và tư duy trung bình.

### Phần 9: Transferable Lessons
> Bài học nào có thể áp dụng cho các projects hoàn toàn khác?

Kết nối các điểm lại với nhau, tìm những nguyên tắc chung.

---

## Quy tắc viết

- **KHÔNG viết như sách giáo khoa hoặc technical docs**
- Viết như đang ngồi đối diện giải thích qua ly cà phê
- Dùng **analogies, short stories, và ví dụ thực tế** để ý tưởng dễ nhớ
- Nếu concept trừu tượng → ground nó vào thứ có thể hình dung được
- Người đọc phải cảm thấy **thực sự hiểu** chuyện gì đã xảy ra và tại sao — không chỉ thấy kết quả cuối
- Viết bằng **tiếng Việt**, trộn thuật ngữ tiếng Anh khi cần thiết

## Bước 3: Báo kết quả

Sau khi viết xong, AI thông báo:
> "Đã tạo file learning tại: [đường dẫn file]. Bạn có thể đọc để review lại toàn bộ quá trình."
