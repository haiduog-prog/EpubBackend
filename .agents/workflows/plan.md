---
description: Quy trình thực thi task coding với brainstorming, execution và lưu learnings tự động
---

# Task Execute Workflow

Quy trình 3 pha để thực thi một task coding: **Brainstorm → Execute → Save Learnings**.

---

## Pha 1: BRAINSTORM (Bắt buộc)

Trước khi code bất kỳ thứ gì, AI **PHẢI** thực hiện quy trình brainstorming.

### Bước 1.1: Load Learnings liên quan

1. Đọc tất cả file trong `.agents/learnings/`:
   - `patterns.md` — Coding patterns & conventions
   - `architecture.md` — Quyết định kiến trúc
   - `bugs-solutions.md` — Bugs đã gặp & cách fix
   - `howto.md` — Quy trình/bước thực hiện
2. Xác định các learnings liên quan đến task hiện tại.
3. Tóm tắt ngắn: "Đã load X learnings liên quan đến task này: [danh sách ngắn]".

### Bước 1.2: Gọi Brainstorming Skill

Mở và đọc file skill brainstorming tại một trong các đường dẫn sau:
- `/Users/apple/.cursor/skills/skills/brainstorming/SKILL.md`
- `/Users/apple/.gemini/skills/brainstorming/SKILL.md`

Tuân thủ **toàn bộ** quy trình brainstorming skill:

1. **Understand Context**: Đọc các file liên quan trong project, hiểu trạng thái hiện tại.
2. **Ask Questions**: Hỏi 1 câu/lần, ưu tiên multiple-choice, cho đến khi hiểu rõ yêu cầu.
3. **Non-Functional Requirements**: Làm rõ performance, scale, security nếu relevant.
4. **Understanding Lock**: Tổng hợp 5-7 bullet points, liệt kê assumptions, hỏi xác nhận.
   - **KHÔNG được tiếp tục nếu chưa có xác nhận.**
5. **Design Approaches**: Đề xuất 2-3 phương án, nêu trade-offs.
6. **Present Design**: Trình bày thiết kế chi tiết theo từng phần nhỏ (200-300 từ), hỏi xác nhận sau mỗi phần.
7. **Decision Log**: Ghi lại mọi quyết định đã thống nhất.

### Gate: Không được chuyển sang Pha 2 nếu chưa hoàn thành Understanding Lock và chưa có design được xác nhận.

---

## Pha 2: EXECUTE

### Bước 2.1: Tạo Task Checklist

Dựa trên design đã được xác nhận ở Pha 1, tạo checklist chi tiết:

```markdown
- [ ] Task 1: Mô tả cụ thể
  - [ ] Sub-task 1.1
  - [ ] Sub-task 1.2
- [ ] Task 2: Mô tả cụ thể
```

### Bước 2.2: Thực thi từng bước

Với mỗi task trong checklist:

1. Đánh dấu `[/]` khi bắt đầu.
2. Thực hiện context audit theo quy trình:
   - Context Scan → Variable & State Tracing → Function & Dependency Check → Impact Verification
3. Viết code theo đúng conventions của project (xem `my-rules.md`).
4. Đánh dấu `[x]` khi hoàn thành.

### Bước 2.3: Verify

Sau khi hoàn thành tất cả tasks:

1. Kiểm tra build: `./gradlew assembleDebug`
2. Lint check: `./gradlew lintDebug`
3. Nếu có test: `./gradlew testDebugUnitTest`
4. Nếu lỗi → quay lại Bước 2.2 để fix.

### Gate: Không được chuyển sang Pha 3 nếu build/lint còn lỗi.

---

## Pha 3: SAVE LEARNINGS (Bán tự động)

### Bước 3.1: Hỏi xác nhận

Sau khi task hoàn thành và verify thành công, AI **PHẢI** hỏi:

> "Task hoàn thành. Bạn có muốn lưu learnings từ task này không? (Có/Không)"

- Nếu **Không** → Kết thúc workflow.
- Nếu **Có** → Tiếp tục Bước 3.2.

### Bước 3.2: Trích xuất Learnings

AI phân tích toàn bộ conversation trong task này và trích xuất thông tin có giá trị tái sử dụng, phân loại vào các nhóm:

| File | Loại nội dung | Ví dụ |
|---|---|---|
| `patterns.md` | Coding patterns, conventions, cách dùng API/library | "SharedPrefs dùng pattern X cho boolean flags" |
| `architecture.md` | Quyết định kiến trúc, tại sao chọn approach A thay vì B | "Dùng WorkManager thay AlarmManager vì..." |
| `bugs-solutions.md` | Lỗi đã gặp, root cause, cách fix | "Lỗi NPE khi... → Fix bằng cách..." |
| `howto.md` | Quy trình step-by-step để thực hiện một việc cụ thể | "Cách thêm notification type mới: 1. Tạo model... 2. Thêm channel..." |

### Bước 3.3: Ghi vào file

- **Luôn dùng append mode** — KHÔNG BAO GIỜ ghi đè nội dung cũ.
- Mỗi entry theo format chuẩn:

```markdown
---

### [Tiêu đề ngắn gọn]
- **Ngày**: YYYY-MM-DD
- **Task**: [Mô tả task gốc]
- **Chi tiết**:
  [Nội dung learning — ngắn gọn, tập trung vào giá trị tái sử dụng]
- **Files liên quan**: `path/to/file1.kt`, `path/to/file2.xml`
```

- Mỗi entry tối đa **150 từ** — chỉ giữ những gì thực sự cần thiết.
- Báo kết quả: "Đã lưu X entries vào: [danh sách file]".

---

## Kết thúc Workflow

Tóm tắt ngắn:
1. Task đã hoàn thành gì
2. Learnings đã lưu (nếu có)
3. Các action items còn lại (nếu có)
