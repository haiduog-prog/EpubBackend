# 📜 QEC Project Development Rules

Các quy định dưới đây là **bắt buộc và không thể thương lượng**. Mọi AI khi tham gia phát triển dự án phải tuân thủ nghiêm ngặt để đảm bảo chất lượng và tính nhất quán của mã nguồn.

---

## 🧼 1. Tầng Nghiệp vụ Nghiêm ngặt (Strict Domain Layer)
*   **Pure TypeScript**: Toàn bộ mã nguồn trong `src/domain/` tuyệt đối **không** được import bất kỳ thư viện bên ngoài nào (ngoại trừ các file nội bộ cùng tầng hoặc các hàm helper cực kỳ cơ bản).
*   **Không Web APIs**: Không sử dụng các biến hay API của trình duyệt (như `window`, `document`, `localStorage`, `fetch`) trong tầng Domain.
*   **Unit Tests**: Khi thay đổi bất kỳ logic tính toán nào trong `src/domain/reportCalculations.ts` hoặc `src/domain/month.ts`, **bắt buộc** phải cập nhật hoặc bổ sung Unit Test tương ứng trong `src/domain/reportCalculations.test.ts` và chạy bộ test để xác thực.

---

## 📊 2. Định dạng xuất Excel (Excel Export Alignment)
*   **Kiểu dữ liệu số**: Khi ghi dữ liệu vào các ô tính toán của Excel trong `writeReportWorkbook.ts`, **tuyệt đối không** ghi dưới dạng chuỗi (String) chứa ký tự định dạng. Luôn ghi kiểu số (`Number`) nguyên thủy.
*   **Cập nhật `numFmt`**: Khi thay đổi cấu trúc bảng, thêm cột hoặc bớt cột trong các sheets của báo cáo, bạn **phải cập nhật lại** logic tính toán chỉ số cột trong hàm `applyMetricFormats(...)` của `writeReportWorkbook.ts`. Lệch chỉ số cột sẽ làm hỏng định dạng số hoặc hiển thị sai phần trăm/tiền tệ trên Excel.

---

## 🎨 3. Thiết kế giao diện cao cấp (Premium UI/UX Design)
*   **CSS Variables**: Tất cả các màu sắc, khoảng cách, font chữ, shadow, border radius phải sử dụng hệ thống biến CSS (CSS Custom Properties) được định nghĩa tại `:root` trong [`src/presentation/styles.css`](file:///d:/Work/ToolsForFen/src/presentation/styles.css). Nghiêm cấm hardcode mã màu hoặc giá trị pixel cục bộ.
*   **No Tailwind CSS**: Không sử dụng Tailwind CSS trừ khi người dùng yêu cầu rõ ràng. Sử dụng Vanilla CSS có tổ chức tốt, tận dụng Flexbox, Grid, và `clamp()` cho fluid typography.
*   **Tương tác sống động (Alive UI)**:
    *   Mọi nút bấm (buttons), tabs, vùng kéo thả file (dropzone), và thẻ (cards) phải có trạng thái Hover và Active mượt mà với `transition: all 0.3s ease`.
    *   Tích hợp hiệu ứng micro-interactions và hiệu ứng Glassmorphism khi thích hợp.
*   **Responsive**: Đảm bảo giao diện co giãn hoàn hảo và hiển thị tốt trên cả thiết bị di động (mobile), máy tính bảng (tablet) và màn hình máy tính (desktop) mà không có thanh cuộn ngang (horizontal scrollbar) không mong muốn.

---

## 🚀 4. Quy trình kiểm tra & Đóng gói (Validation Lifecycle)
Trước khi bàn giao bất kỳ task code nào liên quan đến logic hoặc UI, bạn phải đề xuất chạy:
1.  **Kiểm tra cú pháp & TypeScript**:
    ```bash
    npm run tsc (hoặc chạy build để kiểm tra lỗi biên dịch TS)
    ```
2.  **Chạy Unit Tests**:
    ```bash
    npm run test
    ```
    Đảm bảo 100% test case đều vượt qua (`passed`).
3.  **Kiểm tra Đóng gói (Build Validation)**:
    ```bash
    npm run build
    ```
    Đảm bảo quá trình biên dịch ra sản phẩm hoàn chỉnh trong thư mục `/dist` không phát sinh lỗi.
