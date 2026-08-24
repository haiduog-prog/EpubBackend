import logging
from datetime import datetime, timezone
from app.modules.library.application.facade import library_service
from app.schemas.library import NovelCreateRequest, ChapterStatus

logger = logging.getLogger(__name__)

DEMO_NOVEL_ID = "dai-phung-da-canh-nhan"

CHAPTER_1_TITLE = "Chương 1: Tỉnh lại trong ngục tối"
CHAPTER_1_TEXT = """Hứa Thất An từ từ mở mắt, cảm thấy đầu đau như búa bổ.

Không gian xung quanh tối om và nồng nặc mùi ẩm mốc. Hắn đưa tay xoa trán, phát hiện mình đang mặc một bộ tù phục bằng vải gai thô ráp.

"Đây là đâu? Chẳng lẽ mình đã xuyên không rồi sao?" Hắn lẩm bẩm tự hỏi.

Ký ức của cựu chủ nhân thân thể này ùa về như một dòng thác lũ. Năm nay là năm Cảnh Sát thứ hai mươi tư. Hắn là một tiểu bổ khoái của huyện nha phủ Kinh Triệu thuộc Đại Phụng vương triều.

Vào ngày 24/08 vừa qua, nhị thúc của hắn bất ngờ bị liên lụy trong một vụ án thất thoát ngân lượng triều đình, số tiền lên tới 50000 lượng bạc. Cả nhà họ Hứa đều bị tống giam vào thiên lao, chờ ngày xử trảm.

"Không thể ngồi chờ chết như thế này được! Mình phải tìm cách giải oan cho nhị thúc!" Hứa Thất An thầm nghĩ, ánh mắt lóe lên vẻ kiên định."""

CHAPTER_2_TITLE = "Chương 2: Ty Đả Canh Nhân"
CHAPTER_2_TEXT = """Ánh nắng ban mai le lói chiếu qua khe cửa sắt của thiên lao.

Tiếng bước chân dồn dập vang lên ngoài hành lang. Một toán người mặc hắc y thêu phù hiệu la bàn, tay cầm tú xuân đao bước tới.

Đó chính là người của Ty Đả Canh Nhân, cơ quan tình báo và điều tra trực thuộc hoàng đế Đại Phụng.

"Ai là Hứa Thất An? Bước ra đây!" Một vị Ngân Lạc lạnh lùng cất tiếng.

Hứa Thất An đứng dậy, bình tĩnh chỉnh lại áo quần: "Thảo dân chính là Hứa Thất An."

Vị Ngân Lạc nhìn hắn một lượt từ trên xuống dưới, ánh mắt mang theo vài phần tò mò: "Nghe nói trong đơn kêu oan gửi tới Hình Bộ, ngươi đã chỉ ra 3 điểm nghi vấn lớn của vụ án ngân lượng?"

"Bẩm đại nhân, đúng là như vậy. Vụ án từ chương 10-12 của hồ sơ có rất nhiều mâu thuẫn. Nếu đại nhân cho phép, thảo dân có thể chứng minh nhị thúc vô tội chỉ trong vòng 3 ngày."

Nghe câu trả lời dứt khoát đó, vị Ngân Lạc khẽ nhếch môi: "Được lắm, đi theo ta." """


def seed_demo_novel_if_empty():
    """Tự động tạo 1 bộ truyện mẫu nếu thư viện đang trống trong môi trường local."""
    try:
        novels = library_service.list_novels()
        if novels:
            return None

        logger.info("Thư viện đang trống. Đang tự động tạo bộ truyện mẫu: Đại Phụng Đả Canh Nhân...")
        req = NovelCreateRequest(
            novel_id=DEMO_NOVEL_ID,
            title="Đại Phụng Đả Canh Nhân",
            original_title="大奉打更人",
            author="Mại Báo Tiểu Lang Quân",
            genre="Tiên Hiệp, Huyền Huyễn, Trinh Thám",
            description="Hứa Thất An tỉnh dậy sau cơn say, phát hiện mình bị giam trong thiên lao Đại Phụng. Nhờ kiến thức hình sự hiện đại và tài trí hơn người, hắn từng bước phá giải những kỳ án chấn động triều đình, bước lên đỉnh cao thế giới.",
            cover_url=None
        )

        meta = library_service.create_novel(req)

        # Thêm Chương 1
        library_service.add_or_update_chapter(
            novel_id=DEMO_NOVEL_ID,
            chapter_index=1,
            chapter_title=CHAPTER_1_TITLE,
            content=CHAPTER_1_TEXT
        )
        trans_key_1 = library_service._legacy._chapter_key(DEMO_NOVEL_ID, 1, is_translated=True)
        trans_url_1 = library_service._legacy._save_raw_file(trans_key_1, CHAPTER_1_TEXT.encode("utf-8"), content_type="text/plain; charset=utf-8")

        # Thêm Chương 2
        library_service.add_or_update_chapter(
            novel_id=DEMO_NOVEL_ID,
            chapter_index=2,
            chapter_title=CHAPTER_2_TITLE,
            content=CHAPTER_2_TEXT
        )
        trans_key_2 = library_service._legacy._chapter_key(DEMO_NOVEL_ID, 2, is_translated=True)
        trans_url_2 = library_service._legacy._save_raw_file(trans_key_2, CHAPTER_2_TEXT.encode("utf-8"), content_type="text/plain; charset=utf-8")

        # Cập nhật metadata hoàn thành cả 2 chương
        meta = library_service.get_novel(DEMO_NOVEL_ID)
        if meta and meta.chapters:
            now_iso = datetime.now(timezone.utc).isoformat()
            for ch in meta.chapters:
                if ch.chapter_index == 1:
                    ch.status = ChapterStatus.COMPLETED
                    ch.r2_translated_key = trans_key_1
                    ch.r2_translated_url = trans_url_1
                    ch.translated_text_preview = CHAPTER_1_TEXT[:150] + "..."
                    ch.updated_at = now_iso
                elif ch.chapter_index == 2:
                    ch.status = ChapterStatus.COMPLETED
                    ch.r2_translated_key = trans_key_2
                    ch.r2_translated_url = trans_url_2
                    ch.translated_text_preview = CHAPTER_2_TEXT[:150] + "..."
                    ch.updated_at = now_iso

            meta.total_chapters = len(meta.chapters)
            meta.translated_chapters = sum(1 for c in meta.chapters if c.status == ChapterStatus.COMPLETED)
            meta.updated_at = now_iso
            library_service._legacy._save_metadata(meta)
            library_service._legacy._cache[DEMO_NOVEL_ID] = meta

        logger.info("Đã khởi tạo thành công bộ truyện mẫu '%s' với %d chương hoàn tất.", meta.title, meta.translated_chapters)
        return meta
    except Exception as exc:
        logger.warning("Không thể tạo truyện mẫu tự động: %s", exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_demo_novel_if_empty()
