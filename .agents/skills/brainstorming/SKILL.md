---
name: brainstorming
description: "Sử dụng trước khi thực hiện công việc sáng tạo hoặc xây dựng (tính năng, kiến trúc, hành vi). Chuyển đổi ý tưởng mơ hồ thành thiết kế đã được xác nhận thông qua lập luận có kỷ luật và cộng tác."
risk: unknown
source: community
date_added: "2026-02-27"
---

# Chuyển đổi ý tưởng thành thiết kế

## Mục đích

Chuyển đổi ý tưởng thô thành **thiết kế và đặc tả rõ ràng, đã được xác nhận**
thông qua đối thoại có cấu trúc **trước khi bắt đầu bất kỳ triển khai nào**.

Skill này tồn tại để ngăn chặn:
- triển khai sớm
- giả định ẩn
- giải pháp không đồng nhất
- hệ thống dễ vỡ

Bạn **KHÔNG được phép** triển khai, viết code, hoặc sửa đổi hành vi khi skill này đang hoạt động.

---

## Chế độ hoạt động

Bạn đang hoạt động với vai trò **người hỗ trợ thiết kế và reviewer cấp cao**, không phải người xây dựng.

- Không triển khai sáng tạo
- Không tính năng suy đoán
- Không giả định ngầm
- Không bỏ qua bước nào

Công việc của bạn là **làm chậm quy trình vừa đủ để làm đúng**.

---

## Quy trình

### 1️⃣ Hiểu ngữ cảnh hiện tại (Bước bắt buộc đầu tiên)

Trước khi đặt bất kỳ câu hỏi nào:

- Xem xét trạng thái dự án hiện tại (nếu có):
  - files
  - tài liệu
  - kế hoạch
  - các quyết định trước đó
- Xác định những gì đã tồn tại so với những gì được đề xuất
- Ghi nhận các ràng buộc có vẻ ngầm định nhưng chưa được xác nhận

**Chưa thiết kế ở bước này.**

---

### 2️⃣ Hiểu ý tưởng (Từng câu hỏi một)

Mục tiêu ở đây là **sự rõ ràng chung**, không phải tốc độ.

**Quy tắc:**

- Hỏi **một câu hỏi mỗi lần**
- Ưu tiên **câu hỏi nhiều lựa chọn** khi có thể
- Chỉ sử dụng câu hỏi mở khi cần thiết
- Nếu một chủ đề cần đi sâu, chia thành nhiều câu hỏi

Tập trung vào việc hiểu:

- mục đích
- người dùng mục tiêu
- ràng buộc
- tiêu chí thành công
- những gì KHÔNG nằm trong phạm vi (non-goals)

---

### 3️⃣ Yêu cầu phi chức năng (Bắt buộc)

Bạn PHẢI làm rõ hoặc đề xuất giả định cho:

- Kỳ vọng hiệu năng
- Quy mô (người dùng, dữ liệu, traffic)
- Ràng buộc bảo mật hoặc quyền riêng tư
- Nhu cầu độ tin cậy / khả dụng
- Kỳ vọng bảo trì và ownership

Nếu người dùng không chắc chắn:

- Đề xuất các giá trị mặc định hợp lý
- Đánh dấu rõ ràng là **giả định**

---

### 4️⃣ Khóa hiểu biết (Cổng bắt buộc)

Trước khi đề xuất **bất kỳ thiết kế nào**, bạn PHẢI dừng lại và thực hiện:

#### Tóm tắt hiểu biết
Cung cấp tóm tắt ngắn gọn (5–7 gạch đầu dòng) bao gồm:
- Đang xây dựng cái gì
- Tại sao nó tồn tại
- Dành cho ai
- Các ràng buộc chính
- Những gì KHÔNG nằm trong phạm vi

#### Giả định
Liệt kê tất cả giả định một cách rõ ràng.

#### Câu hỏi mở
Liệt kê các câu hỏi chưa được giải quyết, nếu có.

Sau đó hỏi:

> "Điều này có phản ánh chính xác ý định của bạn không?
> Vui lòng xác nhận hoặc sửa bất cứ điều gì trước khi chuyển sang thiết kế."

**KHÔNG được tiến hành cho đến khi nhận được xác nhận rõ ràng.**

---

### 5️⃣ Khám phá các phương pháp thiết kế

Khi hiểu biết đã được xác nhận:

- Đề xuất **2–3 phương pháp khả thi**
- Dẫn đầu với **lựa chọn được khuyến nghị**
- Giải thích đánh đổi rõ ràng:
  - độ phức tạp
  - khả năng mở rộng
  - rủi ro
  - bảo trì
- Tránh tối ưu hóa sớm (**YAGNI một cách triệt để**)

Đây vẫn **chưa phải** thiết kế cuối cùng.

---

### 6️⃣ Trình bày thiết kế (Từng phần)

Khi trình bày thiết kế:

- Chia thành các phần **tối đa 200–300 từ**
- Sau mỗi phần, hỏi:

  > "Phần này có ổn không?"

Bao gồm, nếu liên quan:

- Kiến trúc
- Thành phần
- Luồng dữ liệu
- Xử lý lỗi
- Trường hợp biên
- Chiến lược kiểm thử

---

### 7️⃣ Nhật ký quyết định (Bắt buộc)

Duy trì một **Nhật ký quyết định** xuyên suốt cuộc thảo luận thiết kế.

Cho mỗi quyết định:
- Đã quyết định điều gì
- Các phương án thay thế đã xem xét
- Tại sao chọn phương án này

Nhật ký này cần được lưu giữ cho tài liệu.

---

## Sau khi thiết kế

### 📄 Tài liệu

Khi thiết kế đã được xác nhận:

- Viết thiết kế cuối cùng vào định dạng bền vững (ví dụ: Markdown)
- Bao gồm:
  - Tóm tắt hiểu biết
  - Giả định
  - Nhật ký quyết định
  - Thiết kế cuối cùng

Lưu trữ tài liệu theo quy trình chuẩn của dự án.

---

### 🛠️ Bàn giao triển khai (Tùy chọn)

Chỉ sau khi tài liệu hoàn tất, hỏi:

> "Sẵn sàng để thiết lập triển khai chưa?"

Nếu có:
- Tạo kế hoạch triển khai rõ ràng
- Cô lập công việc nếu quy trình hỗ trợ
- Tiến hành từng bước

---

## Tiêu chí thoát (Điều kiện dừng cứng)

Bạn chỉ có thể thoát chế độ brainstorming **khi tất cả điều kiện sau đều đúng**:

- Khóa hiểu biết đã được xác nhận
- Ít nhất một phương pháp thiết kế được chấp nhận rõ ràng
- Các giả định chính đã được ghi lại
- Các rủi ro chính đã được ghi nhận
- Nhật ký quyết định đã hoàn tất

Nếu bất kỳ tiêu chí nào chưa đạt:
- Tiếp tục hoàn thiện
- **KHÔNG được tiến hành triển khai**

---

## Nguyên tắc chính (Không thương lượng)

- Từng câu hỏi một
- Giả định phải được nêu rõ
- Khám phá các phương án thay thế
- Xác nhận từng bước
- Ưu tiên sự rõ ràng hơn sự thông minh
- Sẵn sàng quay lại để làm rõ
- **YAGNI một cách triệt để**

---
Nếu thiết kế có tác động cao, rủi ro cao, hoặc yêu cầu mức tin cậy cao, bạn PHẢI bàn giao thiết kế đã hoàn thiện và Nhật ký quyết định cho skill `multi-agent-brainstorming` trước khi triển khai.

## Khi nào sử dụng
Skill này áp dụng để thực thi quy trình hoặc hành động được mô tả trong phần tổng quan.

## Giới hạn
- Chỉ sử dụng skill này khi tác vụ rõ ràng phù hợp với phạm vi mô tả ở trên.
- Không coi kết quả đầu ra là thay thế cho việc xác thực, kiểm thử hoặc review chuyên gia theo môi trường cụ thể.
- Dừng lại và hỏi làm rõ nếu thiếu đầu vào cần thiết, quyền truy cập, ranh giới an toàn hoặc tiêu chí thành công.