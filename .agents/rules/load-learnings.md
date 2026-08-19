---
description: Tự động load learnings từ .agents/learnings/ ở đầu mỗi session
globs: "**/*.{kt,java,xml,gradle,kts}"
trigger: always_on
---

# Load Learnings Rule

## Khi nào áp dụng
Rule này áp dụng **tự động** mỗi khi bắt đầu một session hoặc conversation mới liên quan đến dự án này.

## Hành vi bắt buộc

### Đầu mỗi session, AI PHẢI:

1. **Đọc danh sách file** trong `.agents/learnings/` (chỉ tên file, KHÔNG đọc nội dung).

2. **Xác định** file nào liên quan đến task/câu hỏi hiện tại dựa trên tên file.
   - Ví dụ: task về notification → đọc `notification.md`
   - Task về splash screen → đọc `splash-screen.md`
   - Nếu không có file nào liên quan → bỏ qua, không đọc gì cả.

3. **Chỉ đọc nội dung** các file liên quan — KHÔNG đọc tất cả để tiết kiệm quota.

4. **Tóm tắt ngắn** cho user, ghi rõ tên file đã đọc:
   > "Đã đọc [số lượng] file learnings: `notification.md`, `splash-screen.md`"
   
   Nếu không có file nào liên quan:
   > "Không có file learnings liên quan đến task này."

5. **Áp dụng ngầm** các learnings đã đọc vào việc trả lời/thực thi — không cần hỏi lại những gì đã biết.

### Quy tắc:
- Nếu file learnings không tồn tại hoặc rỗng → bỏ qua, không báo lỗi.
- Không đọc lại nếu đã đọc trong cùng một session.
- **KHÔNG đọc tất cả file** — chỉ đọc file có tên liên quan trực tiếp đến task hiện tại.
- Nếu task liên quan đến nhiều feature → đọc nhiều file tương ứng.
