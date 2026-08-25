# Import Job State & Gemini Timeout Design

> Ngày xác nhận: 2026-08-25

## Tóm tắt hiểu biết

- Sửa hai regression trong luồng import EPUB bất đồng bộ.
- Trạng thái import phải đơn điệu; `completed`/`failed` không được quay lại `processing`.
- Polling phải hoạt động với nhiều worker dùng PostgreSQL.
- Auto-scan giữ deadline 45 giây và phải hủy được request Gemini đang chạy.
- Không thay đổi API schema, không thêm dependency và không gọi mạng trong test.
- Kết quả phải có regression test, cập nhật AI learning, commit và push.

## Giả định

- Production dùng `STRUCTURED_STORAGE_BACKEND=postgres` và `STRUCTURED_STORAGE_READ_SOURCE=postgres`.
- Lỗi ghi trạng thái job không làm mất nội dung EPUB đã lưu.
- `google-genai` hiện tại cung cấp native async API qua `client.aio.models.generate_content`.
- Retry trạng thái terminal phải hữu hạn để background worker không bị giữ vô thời hạn.

## Thiết kế cuối cùng

### Hợp nhất trạng thái import

Khi memory và database cùng có job, trạng thái terminal (`completed`, `failed`) thắng trạng thái chưa kết thúc (`pending`, `processing`). Nếu cả hai chưa kết thúc, chọn bản có `progress_percentage` lớn hơn. Nếu cả hai terminal, worker sở hữu bản memory giữ kết quả cục bộ của nó. Worker không có bản memory vẫn trả dữ liệu database, duy trì polling đa worker.

Persist checkpoint thường chỉ thử một lần. Persist trạng thái terminal retry hữu hạn với backoff ngắn. Lỗi cuối cùng được log, còn trạng thái terminal trong memory vẫn được giữ để endpoint cùng worker không hồi quy về dữ liệu database cũ.

### Timeout Gemini

Gemini provider dùng native async SDK thay cho `asyncio.to_thread` quanh lời gọi đồng bộ. `asyncio.wait_for(..., timeout=45)` vì vậy truyền cancellation vào HTTP coroutine thật, không còn chờ default executor kết thúc. Chuỗi model fallback và cách ghi nhận model lỗi được giữ nguyên.

### Kiểm thử

- Terminal memory thắng database stale.
- Job đang chạy chọn progress cao hơn.
- Terminal persist retry đúng giới hạn.
- Gemini gọi native async API và truyền cancellation.
- Toàn bộ test suite phải pass và worktree không có artifact ngoài phạm vi.

## Nhật ký quyết định

1. Chọn sửa cân bằng thay vì bản tối thiểu vì cần tăng độ tin cậy cho terminal persistence.
2. Không dùng durable job queue vì vượt phạm vi hai regression hiện tại.
3. Dùng native async Gemini thay vì cố hủy thread vì Python không thể dừng an toàn thread đang chạy SDK đồng bộ.
4. Không đổi API/schema để giữ tương thích với Web và Android client.
