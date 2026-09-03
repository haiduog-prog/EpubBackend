# Thống kê dữ liệu trích xuất trong màn hình duyệt chapter

## Tóm tắt hiểu biết

- Bổ sung thống kê ngay trong popup `Duyệt draft` của chương vừa dịch.
- Thống kê theo đúng chapter đang mở, không chuyển người dùng sang tab Book Bible.
- Hiển thị nhân vật, địa danh và thuật ngữ mà AI đã trích xuất.
- Bao gồm dữ liệu đã ghi nhận và đề xuất đang `pending`; loại bỏ mục trùng.
- Không thay đổi dữ liệu Book Bible khi chỉ mở hoặc xem thống kê.
- Giữ nguyên luồng chỉnh sửa và `Duyệt & Áp dụng` hiện tại.

## Giả định

- `first_seen_chapter` là chỉ dấu phù hợp để gắn entity với chapter.
- Các pending changes có thể bổ sung thông tin canonical nhưng không thay thế tổng entity đã trích xuất.
- Không cần thống kê số lần entity xuất hiện trong nội dung; phạm vi là số entity và danh sách chi tiết.

## Thiết kế cuối cùng

Backend cung cấp endpoint read-only theo chapter, lấy Book Bible của novel, lọc `characters`, `places`, `terms` theo `first_seen_chapter`, đồng thời gom các pending changes của chapter để hiển thị trạng thái pending. Response có tổng số và danh sách rút gọn phục vụ popup.

Frontend gọi endpoint song song với original/draft khi mở popup. Một panel thống kê nằm trên thanh số từ, gồm ba thẻ màu riêng cho nhân vật, địa danh và thuật ngữ. Mỗi thẻ hiển thị số lượng và chỉ hiển thị tên tiếng Việt; danh sách dài được cuộn trong panel.

Nếu endpoint lỗi, phần diff vẫn có thể sử dụng và panel hiển thị cảnh báo nhẹ. Nếu không có dữ liệu, panel hiển thị empty state.

## Nhật ký quyết định

| Quyết định | Phương án thay thế | Lý do |
|---|---|---|
| Dùng endpoint riêng theo chapter | Nhúng vào endpoint draft hoặc tải toàn bộ Book Bible ở frontend | Tách trách nhiệm, payload nhỏ và lọc đúng chapter |
| Hiển thị cả pending | Chỉ hiển thị entity đã approved | Màn hình đang duyệt cần cho người dùng thấy toàn bộ kết quả AI |
| Chỉ đọc khi xem thống kê | Tự động approve khi mở popup | Tránh side effect ngoài ý muốn |
