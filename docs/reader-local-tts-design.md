# Thiết kế TTS cục bộ cho Web Reader

## 1. Tóm tắt

Bổ sung chế độ nghe truyện vào `/reader`. Nội dung chương tiếp tục được tải từ
Reader API hiện có, còn chuẩn hóa văn bản, tổng hợp giọng nói và phát audio diễn
ra hoàn toàn trong trình duyệt.

MVP chỉ hỗ trợ Chrome và Edge trên desktop. Runtime được chọn là Piper kết hợp
ONNX Runtime Web, chạy trong Web Worker. Nguồn voice là
[`doof-ferb/nghitts-copy`](https://huggingface.co/doof-ferb/nghitts-copy), một
mirror không chính thức của NGHI-TTS. Voice mặc định là Minh Quang
(`minhquang.onnx`).

Mục tiêu chính:

- Người dùng chủ động tải từng voice và thấy rõ tiến độ tải.
- Phát/tạm dừng, câu trước/sau, đổi tốc độ và highlight câu đang nghe.
- Tự chuyển sang chương tiếp theo và nhớ vị trí nghe.
- Bắt đầu phát trong khoảng ba giây sau khi model đã sẵn sàng.
- Không có khoảng ngắt do thiếu buffer trong quá trình nghe bình thường.
- Không gửi nội dung truyện tới dịch vụ TTS hoặc tạo audio trên backend.

## 2. Phạm vi

### Trong phạm vi MVP

- Chrome và Edge Chromium trên desktop.
- Toàn bộ 25 voice đang có trong `nghitts-copy`.
- Minh Quang được ưu tiên làm voice mặc định.
- Minh Quang được phân phối qua Cloudflare R2.
- Các voice còn lại được tải trực tiếp từ Hugging Face bằng URL khóa revision.
- Tối đa ba voice được cài cùng lúc, dọn theo LRU.
- Chuẩn hóa số, ngày tháng, đơn vị, phần trăm, tiền tệ, số La Mã và chữ viết tắt
  bằng logic của NGHI-TTS.
- Player audiobook cơ bản, highlight câu và tự chuyển chương.

### Ngoài phạm vi MVP

- Android và trình duyệt mobile.
- Safari và Firefox.
- Phát nền khi tắt màn hình hoặc chuyển ứng dụng.
- Lưu toàn bộ truyện để đọc/nghe offline.
- Sinh audio hoặc cache audio ở backend.
- Timeline audio toàn chương, sleep timer và playlist nâng cao.
- Đồng bộ tiến độ giữa nhiều thiết bị.

## 3. Giả định và yêu cầu phi chức năng

- Đây là deployment cá nhân, phi thương mại.
- Reader vẫn public và read-only như thiết kế hiện tại.
- Frontend `/reader` vẫn là HTML/CSS/JavaScript; không chuyển sang Vue hoặc một
  SPA framework.
- Một model hiện có kích thước khoảng 63.5 MB. Ba model cần khoảng 190.5 MB,
  chưa tính runtime và metadata.
- Trình duyệt có thể tự thu hồi Cache Storage; ứng dụng phải kiểm tra cache thực
  tế thay vì chỉ tin metadata.
- Chỉ một ONNX session được giữ trong RAM tại một thời điểm.
- Không log nội dung chương, văn bản đã chuẩn hóa hoặc audio.
- Main thread phải tiếp tục phản hồi trong khi model khởi tạo và inference.

## 4. Phương án đã chọn

### Piper + ONNX Runtime Web

Sử dụng model trong thư mục `piper-tts/` của `nghitts-copy`. Piper, phonemizer
và ONNX Runtime Web chạy trong Web Worker. Tái sử dụng có chọn lọc các phần của
[NGHI-TTS](https://github.com/nghimestudio/nghitts): chuẩn hóa tiếng Việt,
worker inference, tải model động và xử lý streaming.

Lý do chọn:

- Nguồn voice đã có pipeline browser hoạt động với đúng định dạng model.
- Worker tách inference khỏi main thread.
- Ít thành phần runtime và bước đóng gói hơn sherpa-onnx WASM cho use case này.
- Dễ đạt thời gian phát câu đầu và queue audio liên tục hơn trong MVP.

Phương án sherpa-onnx WebAssembly vẫn có thể được thêm sau qua một interface TTS
chung, nhưng không được triển khai song song trong MVP.

## 5. Kiến trúc

```text
Reader API
    |
    v
Chapter text
    |
    v
Sentence segmentation -> Vietnamese normalization -> TTS Worker
                                                   |
                                                   v
                                             PCM/AudioBuffer
                                                   |
                                                   v
Reader DOM <-> ReaderAudioController <-> AudioQueue
                       |
                       +-> progress/preferences

Voice manifest -> VoiceStore -> Cache Storage
                       |
                       +-> IndexedDB metadata + LRU
```

### 5.1 Thành phần frontend

#### Voice manifest

Manifest tĩnh, được version hóa cùng ứng dụng, chứa:

- `id` và tên hiển thị.
- URL model và nguồn `r2` hoặc `huggingface`.
- Revision, số byte và SHA-256.
- Cờ `default` cho Minh Quang.
- Ghi chú ngắn để hiển thị trong voice manager.

Ứng dụng không nhận URL model tùy ý từ query parameter hoặc input người dùng.

#### VoiceStore

Chịu trách nhiệm:

- Kiểm tra dung lượng với Storage API.
- Tải streaming, báo tiến độ và hỗ trợ hủy.
- Xác minh kích thước và SHA-256.
- Ghi model hoàn chỉnh vào Cache Storage.
- Quản lý metadata và `lastUsedAt` trong IndexedDB.
- Giữ tối đa ba voice và xóa voice LRU trước khi cài voice thứ tư.
- Không xóa voice đang được TTS Worker sử dụng.

Khóa cache:

```text
tts-model/{voiceId}/{revision}
```

#### VietnameseTextPipeline

Xử lý theo đơn vị câu:

1. Chia văn bản gốc thành đoạn và câu.
2. Giữ nguyên `originalText` để render an toàn và highlight.
3. Tạo `speechText` bằng bộ chuẩn hóa tiếng Việt.
4. Chia câu quá dài tại ranh giới mệnh đề an toàn cho inference.

Các audio chunk nhỏ của một câu dài vẫn ánh xạ về cùng một câu hiển thị.

#### TtsWorker

- Khởi tạo phonemizer và ONNX Runtime Web.
- Chỉ load một model tại một thời điểm.
- Nhận `speechText`, voice/session ID và cấu hình inference.
- Trả PCM cùng sample rate về main thread bằng transferable buffer.
- Gắn mọi kết quả với `sessionId` để caller bỏ callback quá hạn.
- Giải phóng session cũ khi đổi voice.

#### AudioQueue

- Phát audio bằng Web Audio API.
- Bắt đầu khi câu đầu sẵn sàng và tạo trước 2-3 câu.
- Theo dõi offset để pause/resume.
- Áp dụng tốc độ 0.75x-1.5x bằng `playbackRate`.
- Giải phóng buffer đã phát, chỉ giữ vùng gần cursor.
- Phát sự kiện bắt đầu/kết thúc/buffering cho controller.

#### ReaderAudioController

Owner duy nhất của state player, cursor và session:

```text
NO_VOICE -> LOADING_MODEL -> READY -> GENERATING
                                         |
                                         v
ERROR <- BUFFERING <- PLAYING <-> PAUSED
```

Controller kết nối TTS với chapter navigation, DOM highlight, lưu tiến độ và
prefetch chương tiếp theo.

### 5.2 Đóng gói

TTS được build thành bundle độc lập và output vào vùng static của ứng dụng.
Reader hiện tại chỉ import entry module và không phụ thuộc vào Vue của dự án
NGHI-TTS. Các asset WASM của ONNX Runtime và phonemizer được self-host cùng
deployment để tránh phụ thuộc CDN runtime.

## 6. Voice catalog và cài đặt

Catalog ban đầu gồm 25 file:

```text
adam1, banmai, calmwoman3688, chieuthanh, deepman3909,
duyoryx3175, lacphi, maiphuong, manhdung, minhkhang,
minhquang, minhthu, mytam2, mytam2794, ngochuyen,
ngochuyennew, ngocngan3701, phuongtrang, taian2, taian4,
thanhphuong2, thientam, tranthanh3870, vietthao3886, yannew
```

Mỗi voice có trạng thái:

```text
NOT_INSTALLED | DOWNLOADING | INSTALLED | ACTIVE | ERROR
```

Luồng cài đặt:

1. Người dùng bấm `Tải giọng`.
2. VoiceStore kiểm tra quota.
3. Nếu đã có ba voice, UI thông báo voice LRU sắp bị xóa.
4. Model được tải với tiến độ byte và có thể hủy.
5. Bản tải dở không được đưa vào cache chính thức.
6. Sau khi checksum hợp lệ, metadata được commit vào IndexedDB.
7. Model có thể được chọn và load vào worker.

Khi revision hoặc checksum thay đổi, bản cũ được xem là hết hạn. Ứng dụng không
tự update ngầm một model lớn; người dùng được yêu cầu tải lại.

### Cấu hình R2 cho Minh Quang

- Cho phép CORS từ origin của Reader.
- Trả `Content-Length`.
- Dùng object key có revision.
- Trả `Cache-Control: public, max-age=31536000, immutable`.
- Không ghi đè object đang được một manifest phát hành tham chiếu.

## 7. Luồng phát

### 7.1 Chuẩn bị chương

- Nội dung chương được chia bằng `Intl.Segmenter('vi', {granularity:
  'sentence'})`, có fallback được kiểm thử.
- Mỗi câu được render trong `span` bằng `textContent`; không dùng `innerHTML`.
- Mỗi câu có ID ổn định theo chapter và sentence index.
- Normalization chỉ thay đổi `speechText`, không thay đổi text hiển thị.

### 7.2 Bắt đầu phát

1. Khôi phục cursor nếu có.
2. Kiểm tra voice đang chọn còn trong cache.
3. Load model vào worker.
4. Generate câu tại cursor.
5. Phát ngay câu đầu và generate các câu tiếp theo song song với playback.
6. Highlight câu và chỉ auto-scroll nếu câu nằm ngoài viewport.

### 7.3 Điều khiển

- Pause lưu offset hiện tại và dừng source.
- Resume tạo source mới từ offset đã lưu.
- Câu trước/sau hủy source hiện tại, cập nhật cursor và tạo queue mới.
- Đổi tốc độ áp dụng trong khoảng 0.75x-1.5x.
- Đổi voice giữ nguyên cursor, giải phóng model cũ rồi tạo lại câu hiện tại.
- `Space` phát/tạm dừng khi focus không ở control hoặc input.
- `Alt+Left/Right` chuyển câu; `Left/Right` hiện có vẫn chuyển chương.

### 7.4 Chuyển chương

Reader tiếp tục dùng prefetch chương kế tiếp hiện có. Sau câu cuối:

1. Chuyển chapter state và DOM.
2. Cập nhật URL và reading progress.
3. Đặt cursor ở câu đầu chương mới.
4. Generate và phát tiếp.

Nếu chương tiếp theo lỗi, player dừng ở cuối chương, giữ nội dung hiện tại và
cho phép retry.

## 8. Persistence

Tiến độ nghe theo novel:

```json
{
  "chapterIndex": 12,
  "sentenceIndex": 18,
  "offsetSeconds": 1.4,
  "voiceId": "minhquang",
  "speed": 1.0,
  "updatedAt": "ISO-8601"
}
```

Tiến độ được ghi:

- Khi kết thúc một câu.
- Khi pause.
- Khi tab chuyển sang hidden.
- Trước khi chuyển chương hoặc voice.

Không lưu PCM, AudioBuffer hoặc nội dung chương vào IndexedDB. Không autoplay
khi mở lại trang; UI hiển thị vị trí có thể tiếp tục và chờ thao tác người dùng.

## 9. Giao diện

### Player

Thanh cố định ở cuối reader desktop:

- Tên voice và trạng thái runtime.
- Câu trước, play/pause và câu sau.
- Chọn tốc độ.
- Đổi voice và đóng player.
- Chỉ số `Câu x / y`.

Reader tăng bottom padding khi player mở. Highlight có màu phù hợp với light,
sepia và dark theme.

### Voice manager

- Liệt kê toàn bộ voice.
- Hiển thị dung lượng, nguồn và trạng thái cài đặt.
- Tải, hủy tải hoặc xóa từng voice.
- Hiển thị tiến độ theo byte.
- Đánh dấu Minh Quang là mặc định.
- Thông báo voice LRU sẽ bị xóa trước khi tải voice thứ tư.
- Hiển thị link nguồn và lưu ý mirror không chính thức.

Các trạng thái tải, buffering và lỗi dùng vùng `aria-live`; mọi nút có accessible
name.

## 10. Xử lý lỗi

- **Checksum sai hoặc cache hỏng:** xóa entry và yêu cầu tải lại.
- **Voice khác lỗi:** thông báo và fallback về Minh Quang nếu đã cài.
- **Không có voice khả dụng:** giữ cursor, không phát và mở voice manager.
- **Worker crash/OOM:** restart một lần; nếu còn lỗi thì fallback hoặc dừng.
- **Generation chậm:** chuyển sang `BUFFERING`, không nhảy cursor.
- **Mất mạng:** tiếp tục đọc chương đã tải; dừng khi cần chương mới.
- **Điều hướng nhanh:** tăng `sessionId`, abort fetch và bỏ callback cũ.
- **Cache bị browser thu hồi:** đồng bộ trạng thái về `NOT_INSTALLED`.
- **Thiếu quota:** không dọn dữ liệu ngoài voice cache; báo dung lượng cần thiết.

Mọi lỗi phải giữ nguyên vị trí nghe và không phát trùng câu sau retry.

## 11. Bảo mật và riêng tư

- Chỉ tải model từ URL trong manifest đã đóng gói.
- Xác minh SHA-256 trước khi sử dụng model.
- Render text động bằng DOM text node hoặc `textContent`.
- Không gửi chapter text tới R2, Hugging Face hoặc một TTS API.
- Không log `originalText`, `speechText`, PCM hoặc nội dung lỗi chứa text.
- Log được phép gồm voice ID, chỉ số chapter/câu, latency và mã lỗi.
- Giữ nguyên boundary Reader public/read-only và không thêm write API.

## 12. Hiệu năng

Máy tham chiếu: desktop tối thiểu 4 core, 8 GB RAM, Chrome/Edge hiện hành.

Mục tiêu:

- Sau khi model đã sẵn sàng, phát audio đầu tiên trong khoảng ba giây.
- Không underrun trong mười phút phát liên tục ở tốc độ 1x.
- Buffer mục tiêu 2-3 câu, điều chỉnh theo thời lượng audio đã sẵn sàng.
- Chỉ một model session trong RAM.
- Worker transfer PCM bằng transferable buffer, không copy không cần thiết.
- Main thread vẫn phản hồi khi tải model và inference.

## 13. Kiểm thử

### Unit

- Chuẩn hóa tiếng Việt và tập câu hồi quy cho số/ngày/đơn vị/tên riêng.
- Chia câu và ánh xạ original/speech text.
- State machine và callback theo session ID.
- Pause/resume offset và sentence navigation.
- LRU tối đa ba voice.
- Serialize/restore tiến độ.

### Integration

- Download progress, cancel, retry và checksum failure.
- Cache bị thiếu trong khi metadata còn tồn tại.
- Worker init/generate/error bằng mock runtime.
- Queue tạo trước, buffering và không phát trùng.
- Đổi voice giữa câu.
- Chuyển chương tự động và lỗi chương kế tiếp.

### Browser E2E

- Playwright Chromium: tải voice -> phát -> highlight -> pause -> reload -> tiếp
  tục.
- Kiểm tra keyboard và ARIA state.
- Kiểm tra light/sepia/dark và player không che nội dung.
- Smoke thủ công trên Chrome và Edge.

CI thông thường dùng mock worker hoặc model fixture nhỏ. `minhquang.onnx` không
được tải trong mỗi run CI; một smoke test riêng dùng model thật trước release.

## 14. Tiêu chí chấp nhận

- Reader hoạt động bình thường khi chưa mở hoặc chưa cài TTS.
- Danh mục hiển thị đủ voice và Minh Quang đứng đầu.
- Model tải có tiến độ, hủy/retry và checksum validation.
- Cache không vượt ba voice và không xóa voice đang dùng.
- Player đạt đủ control đã chốt và highlight đúng câu.
- Sau khi model sẵn sàng, audio đầu phát trong khoảng ba giây trên máy tham
  chiếu.
- Phát liên tục không có queue underrun trong bài test mười phút.
- Tự chuyển chương và khôi phục đúng vị trí nghe.
- Lỗi voice khác fallback về Minh Quang đã cài kèm thông báo.
- Không có nội dung truyện trong network TTS request, persistent audio hoặc log.
- Reader API vẫn public `GET` only và không regress chức năng đọc hiện tại.

## 15. Rủi ro

- Mirror Hugging Face là nguồn không chính thức; revision và checksum phải được
  khóa, đồng thời giữ link attribution.
- Một số voice mô phỏng người nổi tiếng. Phạm vi đã chốt là cá nhân/phi thương
  mại; không mặc định phân phối lại model dưới thương hiệu của ứng dụng.
- Hugging Face có thể thay đổi CORS hoặc khả năng tải; Minh Quang trên R2 bảo vệ
  luồng mặc định nhưng không loại bỏ lỗi ở voice khác.
- Model lớn có thể bị trình duyệt thu hồi hoặc gây áp lực RAM.
- Máy yếu có thể inference chậm hơn playback; UI buffering phải rõ ràng.
- Chuẩn hóa và phát âm tên riêng/Hán-Việt có thể chưa chính xác; cần corpus hồi
  quy từ nội dung truyện thực tế.

## 16. Nhật ký quyết định

1. TTS chạy client-side; backend không sinh audio.
2. Chương vẫn tải online; không xây full offline reader.
3. MVP chỉ hỗ trợ Chrome/Edge desktop.
4. Dùng toàn bộ voice từ `doof-ferb/nghitts-copy`.
5. Minh Quang (`minhquang.onnx`) là voice mặc định.
6. Voice chỉ tải sau thao tác rõ ràng của người dùng.
7. Minh Quang phân phối qua R2; voice khác từ Hugging Face pinned revision.
8. Cache tối đa ba voice và dọn theo LRU.
9. Fallback về Minh Quang đã cài, luôn kèm thông báo.
10. Dùng chuẩn hóa tiếng Việt của NGHI-TTS.
11. Player hoạt động theo câu, tự chuyển chương và lưu vị trí.
12. Chọn Piper + ONNX Runtime Web thay vì sherpa-onnx WASM cho MVP.
13. Giữ frontend Reader hiện tại, chỉ thêm một TTS bundle độc lập.

