# Translation Quality Gate & Book Bible Architecture Rule

> **BẮT BUỘC TUÂN THỦ**. Mọi module dịch, kiểm tra chất lượng (QA) và Book Bible phải tuân thủ nghiêm ngặt nguyên tắc phân tách giữa **Quy tắc dùng chung (Shared Engine)** và **Dữ liệu riêng của từng truyện (Novel-specific Data)**.

---

## 1. QUY TẮC DÙNG CHUNG (SHARED / GENERIC RULES)

Mọi bộ truyện (dù là Tiên hiệp, Đô thị, Huyền huyễn hay Khoa huyễn) đều dùng chung các cơ chế sau:

### 1.1 Tiền xử lý & Vệ sinh văn bản (Sanitization Pipeline)
- **Bóc tách Watermark / Rác quảng cáo**: Bắt buộc lọc qua `app/parsers/text_sanitizer.py:clean_raw_text` để xóa sạch dấu vết crawler (`read.st`, `tangthuvien`, `truyenfull`, `------oOo------`, link web).
- **Phục hồi tiêu đề chương**: Dòng đầu tiên của bản dịch phải luôn bắt đầu bằng `Chương {index}: {Tên chương}`. Nếu LLM nuốt mất dòng này, engine phải tự động bóc từ nguyên tác qua `extract_chapter_title_prefix` và gắn lại bằng `reattach_chapter_title`.
- **Loại bỏ ký tự rác ngoại lai**: Tự động lọc sạch ký tự Unicode Arabic, Greek, Cyrillic (`[\u0600-\u06FF\u0750-\u077F\u0370-\u03FF\u1F00-\u1FFF\u0400-\u04FF]`) xuất hiện do lỗi OCR cào web.
- **Sửa lỗi ngữ pháp chức năng**: Tự động sửa từ lặp (`ra ra` ➔ `ra`, `lên lên` ➔ `lên`, `lại lại` ➔ `lại`) và Việt hóa thán từ ngoại lai chưa dịch (`Haizz` ➔ `Than ôi`).

### 1.2 Nguyên tắc Quality Gate & Chứng nhận xuất bản
- **Không xuất bản ngầm**: Không một chương nào được tự động đánh dấu `COMPLETED` chỉ dựa vào kích thước file (>50 bytes). Chương chỉ được xem là hoàn tất khi:
  1. Có nội dung bản gốc (original) để đối soát.
  2. Không còn file nháp tồn đọng trong `storage/novels/{novel_id}/drafts/`.
  3. Đạt `QAService.fast_rule_check == 0` lỗi.
- **Review chương độc lập**: Tuyệt đối cấm fallback gán `orig_content = trans_content` khi review. Nếu thiếu bản gốc, phải trả về lỗi `Thiếu nội dung bản gốc`.

### 1.3 Hợp nhất Book Bible có kiểm duyệt (Unified Canonical Merge)
- **Với Thuật ngữ (Terms)**:
  - Nếu `locked=False`: Cho phép LLM hoàn thiện cách dịch (`existing.vi_name = new_term.vi_name`, tên cũ lưu vào `aliases`).
  - Nếu `locked=True`: Tuyệt đối không ghi đè; tên mới đưa vào `forbidden_variants` và xếp vào `pending_changes` chờ duyệt.
- **Với Nhân vật (Characters)**:
  - Tên tiếng Việt chuẩn (`vi_name`) một khi đã xác lập là canonical thì **bảo toàn tuyệt đối**; tên mới từ LLM chỉ được lưu vào `aliases` (khi chưa khóa) hoặc `forbidden_variants` + `pending_changes` (khi đã khóa).
- **Chế độ Post-edit**:
  - Không nhận entity mới có `original_name` CJK nếu không có bằng chứng chữ Hán trong `evidence`. Tự động gán `original_name = vi_name`.

### 1.4 Lọc xưng hô Cổ phong không bắt nhầm tiếng Việt (Smart Pronoun QA)
- Khi kiểm tra xưng hô hiện đại (`anh/em`) ở thể loại cổ phong / tiên hiệp, **bắt buộc loại trừ**:
  - Tên riêng có họ tiếng Việt đi kèm (`Lý Anh`, `Quy Anh`, `Trần Anh`...).
  - Từ ghép tiếng Việt (`anh hùng`, `anh dũng`, `anh danh`, `anh ruột`, `em ruột`, `em gái`...).
- Kiểm tra biến thể cấm của thuật ngữ (`term.forbidden_variants`) chỉ kích hoạt khi thuật ngữ đó thực sự xuất hiện trong nguyên tác chương đang xét.

---

## 2. DỮ LIỆU CỦA RIÊNG TỪNG TRUYỆN (NOVEL-SPECIFIC DATA)

Tuyệt đối **KHÔNG hardcode** tên thực thể của bất kỳ bộ truyện nào vào mã nguồn chung (`app/modules/`, `app/parsers/`, `app/llm/`).

### 2.1 Thuộc Tính Phải Nằm Trong Book Bible của Từng Truyện
Mỗi bộ truyện có một không gian dữ liệu riêng (`storage_repo.get_bible(novel_id)`):
- **Nhân vật & Đại từ kể**: Danh sách nhân vật (VD: *Vạn Thú Chiến Thần* có Đỗ Phong, Mộc Linh; *Cổ Chân Nhân* có Phương Nguyên, Cổ Nguyệt Phương Chính).
- **Cảnh giới & Thuật ngữ**:
  - Danh sách cảnh giới (VD: *Khí Võ Cảnh*, *Tụ Võ Cảnh* là biến thể cấm của riêng *Vạn Thú Chiến Thần*).
  - Thuật ngữ kỹ năng (VD: *Phi Hành Thuật* cấm *phù thủy* chỉ áp dụng khi bộ truyện đó có thuật ngữ bay này).
- **Quan hệ giữa các nhân vật**: Không đặt cấm chéo (`forbidden_variants`) giữa hai nhân vật có thật cùng xuất hiện trong truyện.

### 2.2 Các ngoại lệ vá theo chương cụ thể (Novel Patches)
- Nếu một chương cụ thể có lỗi đối thoại ngữ cảnh đặc thù (như đoạn Đỗ Phong dỗ Mộc Linh ở chương 122), đoạn vá phải được đóng gói theo namespace `novel_id == [novel_id]` và `chapter_index == [index]`, không áp dụng tràn lan sang các truyện khác.