# Thiết kế Supabase Auth và đồng bộ trạng thái đọc

Ngày xác nhận: 2026-08-24

## 1. Tóm tắt hiểu biết

- Dùng Supabase Auth với email và mật khẩu.
- Không có đăng ký công khai; tài khoản được quản lý trong Supabase Dashboard.
- Không phân quyền: mọi tài khoản hợp lệ truy cập được cả reader và quản trị.
- Trang reader, trang quản trị và toàn bộ `/api/v1` đều yêu cầu xác thực.
- Đồng bộ chương đang đọc, vị trí cuộn, thời điểm gần nhất và tùy chọn hiển thị.
- Dữ liệu `localStorage` hiện có được nhập lên server đúng một lần sau lần đăng nhập đầu.
- Quy mô dự kiến dưới 10 tài khoản; phiên đăng nhập được giữ đến khi đăng xuất.
- Khi đồng bộ tạm lỗi, reader tiếp tục dùng local state và thử lại sau.

## 2. Phạm vi và non-goals

### Trong phạm vi

- Login/logout và khôi phục/refresh Supabase session.
- Xác minh Supabase JWT cho FastAPI.
- Khóa toàn bộ API nghiệp vụ và hai UI hiện có.
- Lưu preferences và tiến độ theo Supabase user ID trong PostgreSQL.
- Migration dữ liệu reader từ localStorage.
- Bảo vệ nội dung chương, cover và EPUB khỏi URL storage công khai.

### Không nằm trong phạm vi

- Đăng ký công khai.
- Phân quyền admin/reader.
- UI quản lý tài khoản, quên mật khẩu hoặc tự đổi mật khẩu.
- Social login, MFA, bookmark, yêu thích hoặc bình luận.
- Đồng bộ thời gian thực; mô hình last-write-wins là đủ.

## 3. Giả định

- UI chỉ được phục vụ từ backend Render hiện tại.
- Supabase project hiện tại bật Auth và dùng asymmetric JWT signing key.
- Người vận hành tạo/xóa/reset tài khoản và quản lý signing key trong Supabase Dashboard.
- Production dùng PostgreSQL và Supabase Storage; R2 chỉ là fallback và không công khai nội dung được bảo vệ.
- Frontend chỉ nhận Supabase URL và publishable key; secret/service-role key chỉ tồn tại ở server.

## 4. Phương án đã chọn

Frontend dùng `supabase-js` để đăng nhập, lưu session trong browser và tự refresh access token. Mọi request API gửi `Authorization: Bearer <token>`. FastAPI xác minh token tại dependency cấp router bằng JWKS của Supabase và dùng claim `sub` làm `user_id`.

Trạng thái cá nhân nằm trong PostgreSQL hiện tại thay vì truy cập Supabase Database trực tiếp. Cách này giữ một data plane cho nghiệp vụ, dễ test và không cần RLS cho các bảng reader.

Tài liệu tham khảo:

- https://supabase.com/docs/reference/javascript/auth
- https://supabase.com/docs/guides/auth/jwts
- https://supabase.com/docs/guides/auth/signing-keys

## 5. Kiến trúc xác thực

### Frontend

- `/login` là trang công khai duy nhất cho người dùng.
- `auth.js` được dùng chung bởi reader và admin.
- Supabase client bật `persistSession` và `autoRefreshToken`.
- UI không hiển thị dữ liệu nghiệp vụ trước khi session được khôi phục.
- Fetch wrapper gắn Bearer token vào mọi `/api/v1` request.
- Khi gặp `401`, frontend refresh session đúng một lần; nếu vẫn lỗi thì sign out và chuyển về `/login`.
- Redirect sau login chỉ chấp nhận `/` hoặc `/reader`.
- Cả hai UI có nút logout và hiển thị email từ session.

### Backend

- Dependency `get_current_user` đọc Bearer token và trả `AuthenticatedUser(user_id, email)`.
- Xác minh signature, `iss`, `aud=authenticated`, `exp` và `sub`.
- JWKS được cache ngắn hạn; không có key hợp lệ thì fail closed.
- Dependency được áp dụng tại `api_v1_router` để mặc định bảo vệ tất cả endpoint.
- `/login`, static assets và endpoint public auth config nằm ngoài router được bảo vệ.
- `BOOK_BIBLE_WRITE_TOKEN` không còn là credential UI; mọi authenticated user có cùng quyền.
- Production không có cờ bypass auth.

### Cấu hình

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`
- `SUPABASE_JWT_AUDIENCE=authenticated`
- Issuer được suy ra là `${SUPABASE_URL}/auth/v1`.
- Service/secret key hiện có không được tái sử dụng làm publishable key.

## 6. Mô hình dữ liệu

### `reader_user_settings`

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `user_id` | string UUID | PK, Supabase JWT `sub` |
| `preferences` | JSON/JSONB | theme, fontSize, lineHeight, readingWidth |
| `local_migrated_at` | timestamp nullable | Chặn migration local lặp lại |
| `created_at` | timestamp | Server time |
| `updated_at` | timestamp | Server time |

### `reader_progress`

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `user_id` | string UUID | Một phần composite PK |
| `novel_id` | string | Một phần composite PK, FK tới `novels` |
| `chapter_index` | integer | `>= 1` |
| `scroll_top` | integer | `>= 0` |
| `created_at` | timestamp | Server time |
| `updated_at` | timestamp | Server time, last request wins |

Foreign key `reader_progress.novel_id -> novels.novel_id` dùng `ON DELETE CASCADE`. Không tạo FK sang `auth.users` vì Supabase Auth và PostgreSQL ứng dụng là hai database khác nhau.

## 7. API

### Authenticated reader state

- `GET /api/v1/reader/me/state`
  - Trả email/user ID, preferences, `local_migrated_at` và danh sách progress.
- `POST /api/v1/reader/me/migrate-local`
  - Nhận preferences và progress snapshot từ localStorage.
  - Chạy transaction và chỉ thành công lần đầu.
  - Request lặp lại không ghi đè server state.
- `PUT /api/v1/reader/me/preferences`
  - Validate các giới hạn UI hiện có rồi upsert preferences.
- `PUT /api/v1/reader/me/progress/{novel_id}`
  - Validate truyện/chương tồn tại và upsert progress với server timestamp.

Mọi truy vấn repository luôn lấy `user_id` từ JWT dependency, không nhận user ID từ body/query/path.

## 8. Luồng đồng bộ

1. Frontend khôi phục Supabase session.
2. Gọi state endpoint.
3. Nếu `local_migrated_at` rỗng, đọc `reader.preferences` và tất cả `reader.progress.*`, rồi gọi migrate endpoint.
4. Sau migration, server state là nguồn khởi tạo UI.
5. Mỗi thay đổi được ghi localStorage ngay để reader không bị gián đoạn.
6. Preferences và progress được gửi server sau debounce khoảng 2 giây.
7. Pending write được giữ trong localStorage và retry khi browser online, tab được mở lại hoặc chuyển chương.
8. Server time quyết định request sau cùng thắng, không tin đồng hồ client.

## 9. Bảo vệ nội dung

- Reader lấy nội dung chương qua FastAPI authenticated endpoint.
- Reader API không trả hoặc ưu tiên URL chapter public.
- Supabase bucket chứa chương/EPUB được chuyển sang private.
- Cover và EPUB export được proxy qua endpoint authenticated.
- R2 fallback không được cấu hình public cho nội dung truyện.
- Với dưới 10 người, proxy qua backend không tạo áp lực hiệu năng đáng kể.

## 10. Xử lý lỗi

- Thiếu/sai/hết hạn JWT: `401` và `WWW-Authenticate: Bearer`.
- JWT sai issuer/audience/signature: từ chối, không fallback anonymous.
- JWKS tạm lỗi nhưng còn cache hợp lệ: dùng cache; không có cache: `503` fail closed.
- Sync lỗi: giữ local state, đánh dấu pending và retry.
- Migration local chạy transaction và idempotent.
- Truyện bị xóa: progress liên quan tự xóa theo foreign key.
- Login dùng thông báo lỗi chung, không tiết lộ email có tồn tại hay không.

## 11. Chiến lược kiểm thử

- JWT hợp lệ, hết hạn, sai chữ ký, sai `iss`, sai `aud`, thiếu header.
- Tất cả `/api/v1` trả `401` cho anonymous.
- Hai user không đọc hoặc cập nhật được state của nhau.
- Upsert progress/preferences và last-request-wins.
- Migration local idempotent.
- Reader tiếp tục chạy khi sync API lỗi.
- Login, logout, refresh và redirect allowlist.
- Reader API không làm lộ URL nội dung public.
- Unit test dùng signing key/JWKS giả; integration test Supabase chỉ chạy khi có credential CI.

## 12. Triển khai

1. Chuyển Supabase Auth sang asymmetric signing key.
2. Tạo publishable key cho frontend; xác nhận secret key chỉ có ở server.
3. Chạy Alembic tạo các bảng reader state.
4. Chuyển bucket truyện sang private và kiểm tra backend vẫn đọc được.
5. Deploy backend và UI.
6. Tạo tài khoản trong Supabase Dashboard.
7. Smoke-test login, reader, admin, sync hai thiết bị, logout và truy cập anonymous.

## 13. Rủi ro chính

- Session browser nằm trong localStorage nên XSS có thể đánh cắp token. Giảm thiểu bằng audit render dữ liệu động, không log token, giới hạn nguồn script và dùng access token ngắn hạn.
- Chuyển bucket private có thể làm hỏng cover/export nếu còn code dùng public URL; cần integration test toàn bộ media flow.
- JWKS rotation có cache delay; cache phải ngắn hạn và có cơ chế làm mới khi gặp `kid` mới.
- Khóa router toàn cục có thể làm hỏng test/CLI cũ; test phải override dependency bằng authenticated user giả thay vì thêm production bypass.

## 14. Nhật ký quyết định

| Quyết định | Phương án khác | Lý do |
| --- | --- | --- |
| Supabase Auth | Firebase Auth; local password/JWT | Đã có Supabase, giảm vận hành mật khẩu |
| Không public signup | Self-service signup | Hệ thống riêng dưới 10 người |
| Không phân quyền | Admin/reader roles | Chủ hệ thống xác nhận mọi account có cùng quyền |
| Frontend Supabase session + Bearer JWT | Backend HttpOnly session; direct Supabase RLS | Ít custom session code, phù hợp UI hiện tại |
| PostgreSQL lưu reader state | Supabase Database trực tiếp | Giữ một data plane nghiệp vụ, dễ test |
| Last request wins bằng server timestamp | Client timestamp; vector clock | Đủ cho quy mô nhỏ, tránh clock skew |
| Local-first với retry | Chặn đọc khi sync lỗi | Giữ trải nghiệm đọc ổn định |
| Private blob + backend proxy | Public URLs; signed URLs | Thực thi yêu cầu phải đăng nhập trước khi đọc |
| Quản lý user trong Dashboard | Xây admin user UI | YAGNI và giảm bề mặt bảo mật |
