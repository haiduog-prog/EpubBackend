# Kế hoạch sửa database local và các nhánh dịch còn thiếu

Mục tiêu: tránh mất cập nhật trên SQLite, bảo đảm cache phản ánh Book Bible hiện tại và giữ đầy đủ nội dung/định dạng khi dịch. Phạm vi là local; giữ nguyên các thay đổi đang có trong workspace.

Thực hiện theo thứ tự dưới đây. Mỗi mục có kiểm thử hồi quy riêng; xác minh tích hợp ở bước cuối.

| Bước | Thay đổi cụ thể | Điều kiện đạt |
|---|---|---|
| 1. Cô lập kiểm thử | Chuẩn bị SQLite và storage tạm cho từng phiên; chuyển cấu hình trước khi import app. Giữ fixture cleanup hoạt động trong vùng tạm, đối chiếu hash dữ liệu gốc trước/sau. | Không ghi/xóa bản ghi test cũ hoặc dữ liệu truyện có sẵn. |
| 2. Chặn ghi đè snapshot | Trong repository/library service, tách cập nhật metadata truyện khỏi cập nhật chương; luồng dịch chỉ lưu chương đã thay đổi. Dùng điều kiện revision hoặc cơ chế kiểm soát xung đột cho hai cập nhật cùng chương; tính lại tổng số chương trong transaction. | Hai tác vụ cập nhật hai chương cùng lúc đều được giữ; snapshot cũ không xóa đường dẫn/trạng thái mới. Xung đột cùng chương được phát hiện. |
| 3. Revision và cache | Tăng `bible_revision` nguyên tử trong transaction merge Book Bible; rà các đường save/import/review để bảo đảm revision không lùi. Chỉ cache theo revision của bản đã commit; tăng phiên bản cache khi triển khai để loại kết quả tạo bởi logic cũ. | Sau đổi thuật ngữ, cache cũ không hit; merge đồng thời không mất nội dung hoặc dùng chung revision cho hai trạng thái khác nhau. |
| 4. Nhận job SQLite nguyên tử | Thay SELECT rồi UPDATE trong `claim_next_job` bằng UPDATE có điều kiện trạng thái/lease; chỉ worker cập nhật thành công mới nhận job. Kiểm tra token khi heartbeat, hoàn tất và thu hồi lease. | Hai kết nối SQLite tranh cùng job chỉ có một bên thắng; worker cũ không hoàn tất job đã chuyển lease. |
| 5. Chặn Claude trả thiếu | Kiểm tra `stop_reason` và output rỗng cho dịch prose, hiệu đính và structured HTML; trả lỗi có kiểu khi output bị cắt, giữ bản dịch trước đó khi correction lỗi. | `max_tokens` không được đánh dấu hoàn tất, đưa vào cache hoặc ghi đè bản dịch đầy đủ. Response hoàn chỉnh vẫn qua. |
| 6. Bảo toàn HTML | Trích xuất cả text ngoài các block quen thuộc, tránh lặp text ở block lồng nhau. Bảo vệ inline tags/link/anchor/media bằng marker ổn định trong đơn vị dịch; kiểm tra marker trước khi dựng lại cây HTML. | Không sót text ở `div`/`br`; link, ID, nhấn mạnh, ảnh và chú thích còn nguyên. Marker thiếu/sai khiến batch cần xử lý lại thay vì xuất EPUB hỏng. |
| 7. Cách ly cooldown Gemini | Khóa cooldown theo fingerprint thông tin xác thực và model; quota dùng chung chỉ ảnh hưởng pool của thông tin xác thực đó. Không ghi API key thô vào cache/log. | Key A bị quota không chặn key B; cùng key vẫn tuân thủ cooldown và retry-after. |
| 8. Đóng client AI | Bổ sung giao diện `aclose()` nhất quán cho provider; dùng `try/finally` tại nơi tạo client trong dịch trực tiếp, job file và dịch/review chương. | Client được đóng đúng một lần khi thành công, lỗi hoặc hủy; lỗi đóng không che lỗi chính. |
| 9. Cấu hình local | Tắt `GOOGLE_DRIVE_SYNC_ENABLED` trong cả `start_local.ps1` và `.bat`; xác minh biến môi trường trỏ SQLite và storage local. | Khởi động theo script local không bật đồng bộ Drive; cấu hình hai script nhất quán. |
| 10. Xác minh tích hợp | Chạy test liên quan Book Bible/dịch và hồi quy mới; kiểm thử cạnh tranh bằng nhiều kết nối SQLite file tạm, job phục hồi, cache miss sau sửa Bible, EPUB xuất ra đọc lại được. Kiểm tra diff và hash dữ liệu gốc. | Tất cả kiểm thử mục tiêu qua; dữ liệu gốc không đổi; báo rõ giới hạn kiểm thử AI giả lập. |

Nếu bước 2 cần bổ sung cột revision, tạo migration tương thích SQLite và kiểm tra nâng cấp trên database tạm trước. Các phép thử dùng AI giả lập; đánh giá chất lượng câu dịch thực tế là bước riêng.
