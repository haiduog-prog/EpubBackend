---
description: Lưu learnings từ conversation hiện tại vào .agents/learnings/ theo chủ đề
---

# AI Learning Workflow

Trích xuất kiến thức từ conversation hiện tại và lưu vào **1 file duy nhất theo tên feature/chức năng**, tổng hợp cả 4 khía cạnh: Architecture, Bugs, How-to, Patterns.

---

## Bước 1: Scan file hiện tại & Xác định Feature

### 1.1: Scan thư mục learnings

AI **PHẢI** đọc danh sách file trong `.agents/learnings/` trước tiên để biết những feature nào đã có file.

### 1.2: Xác định feature từ conversation

AI phân tích conversation hiện tại và xác định tên feature/chức năng chính đã thực hiện.

### 1.3: Quyết định tạo mới hay cập nhật

- **Nếu đã có file chứa feature đó** (ví dụ: conversation về notification mà `notification.md` đã tồn tại) → **Cập nhật file đó**. KHÔNG tạo file mới.
- **Nếu chưa có file nào liên quan** → Tạo file mới với tên **kebab-case**, ngắn gọn.
  - Ví dụ: `notification.md`, `user-segmentation.md`, `splash-screen.md`

### 1.4: Thông báo ngắn

Nếu **cập nhật file cũ**:
> "Đang cập nhật learnings vào: `[tên file]`"

Nếu **tạo file mới**:
> "Đang tạo file learnings mới: `[tên file]`"

## Bước 2: Trích xuất Learnings

AI quét toàn bộ conversation và phân loại thông tin có giá trị tái sử dụng vào 4 nhóm:

| Section | Nội dung | Ví dụ |
|---|---|---|
| **Architecture** | Quyết định kiến trúc, design rationale, tại sao chọn A thay B | "Dùng WorkManager thay AlarmManager vì..." |
| **Bugs & Solutions** | Lỗi đã gặp, root cause, cách fix | "Lỗi NPE khi... → Fix bằng cách..." |
| **How-To** | Quy trình step-by-step để thực hiện một việc cụ thể | "Cách thêm notification type mới: 1. Tạo model..." |
| **Patterns** | Coding patterns, conventions, cách dùng API/library | "SharedPrefs dùng pattern X cho boolean flags" |

**Tự động thực thi** — KHÔNG hỏi xác nhận, trực tiếp ghi vào file.

## Bước 3: Merge & Ghi file

### File path: `.agents/learnings/[feature-name].md`

### Nếu file CHƯA tồn tại → Tạo mới theo template:

```markdown
# [Feature Name]

> Tổng hợp kiến thức về [mô tả ngắn feature] trong dự án.
> Cập nhật lần cuối: YYYY-MM-DD

---

## Architecture

### [Tiêu đề]
- **Ngày**: YYYY-MM-DD
- **Chi tiết**: [Nội dung — ngắn gọn, tập trung vào giá trị tái sử dụng]
- **Files liên quan**: `path/to/file.kt`

---

## Bugs & Solutions

### [Tiêu đề]
- **Ngày**: YYYY-MM-DD
- **Vấn đề**: [Mô tả bug]
- **Root cause**: [Nguyên nhân gốc]
- **Fix**: [Cách fix]
- **Files liên quan**: `path/to/file.kt`

---

## How-To

### [Tiêu đề]
- **Ngày**: YYYY-MM-DD
- **Bước thực hiện**:
  1. Step 1
  2. Step 2
- **Files liên quan**: `path/to/file.kt`

---

## Patterns

### [Tiêu đề]
- **Ngày**: YYYY-MM-DD
- **Chi tiết**: [Mô tả pattern]
- **Ví dụ code** (nếu cần):
  ```kotlin
  // code snippet
  ```
- **Files liên quan**: `path/to/file.kt`
```

### Nếu file ĐÃ tồn tại → Merge thông minh:

1. **Đọc file hiện tại** — hiểu các entries đã có.
2. **So sánh** entries mới với entries cũ:
   - Nếu entry mới **trùng topic** với entry cũ → **Cập nhật** entry cũ với thông tin mới, giữ ngày mới nhất.
   - Nếu entry mới **khác topic** → **Thêm** entry mới vào đúng section.
   - Nếu entry cũ **đã lỗi thời** do thay đổi trong conversation → **Sửa** cho đúng.
3. **Cập nhật** dòng "Cập nhật lần cuối" ở đầu file.
4. **Viết lại file** hoàn chỉnh — gọn gàng, không trùng lặp.

### Quy tắc ghi:
- Mỗi entry tối đa **150 từ**.
- Không lưu thông tin quá cụ thể chỉ áp dụng cho 1 lần (giá trị hardcode, tên biến tạm).
- Ưu tiên thông tin có giá trị **tái sử dụng lâu dài**.

## Bước 4: Báo kết quả

```
✅ Đã lưu learnings vào: .agent/learnings/[feature-name].md
   - Architecture: X entries
   - Bugs & Solutions: X entries
   - How-To: X entries
   - Patterns: X entries
   Tổng: Y entries (Z mới, W cập nhật)
```