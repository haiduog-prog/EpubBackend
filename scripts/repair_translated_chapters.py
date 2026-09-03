"""
Script vá và chuẩn hóa tự động các chương dịch cũ bị lỗi.
Áp dụng Book Bible Canonical, bóc tách Watermark, phục hồi tiêu đề và chạy Quality Gate.

Cách dùng:
    # Chế độ xem trước (dry-run, không sửa file)
    python scripts/repair_translated_chapters.py --novel-id van-thu-chien-than --start 122 --end 162 --dry-run

    # Chế độ thực thi (apply, ghi đè file và cập nhật DB)
    python scripts/repair_translated_chapters.py --novel-id van-thu-chien-than --start 122 --end 162 --apply
"""

import argparse
import glob
import os
import re
import sys
from pathlib import Path

# Đảm bảo import được module của project
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infrastructure.storage.facade import storage_repo
from app.modules.translation.application.qa_service import QAService
from app.parsers.text_sanitizer import (
    clean_raw_text,
    extract_chapter_title_prefix,
    reattach_chapter_title,
)
from app.modules.library.schemas import ChapterStatus


def repair_chapter_text(
    chapter_index: int,
    trans_text: str,
    orig_text: str,
    bible=None,
    novel_id: str = "",
) -> tuple[str, list[str]]:
    """
    Áp dụng các bước chuẩn hóa và vá lỗi mang tính tất định (deterministic).
    Phân tách rõ:
      - Phần dùng chung (Generic): Watermark, tiêu đề, ký tự lạ, lặp từ, thay thế theo Book Bible.
      - Phần của riêng truyện: Các ngoại lệ đối thoại theo từng chapter index cụ thể.
    """
    changes: list[str] = []
    text = trans_text

    # =========================================================================
    # 1. QUY TẮC DÙNG CHUNG (GENERIC CHO MỌI TRUYỆN)
    # =========================================================================

    # 1.1 Bóc sạch watermark và rác quảng cáo cào web
    cleaned_wm = clean_raw_text(text)
    cleaned_wm = re.sub(r"(?im)^\s*nguồn:\s*read\.st\s*$", "", cleaned_wm)
    cleaned_wm = re.sub(r"(?im)^\s*read\.st\s*$", "", cleaned_wm)
    cleaned_wm = re.sub(r"(?im)^\s*------oOo------\s*$", "", cleaned_wm)
    cleaned_wm = re.sub(r"\n{3,}", "\n\n", cleaned_wm).strip()

    if cleaned_wm != text.strip():
        changes.append("Bóc sạch watermark / quảng cáo rác")
        text = cleaned_wm

    # 1.2 Phục hồi dòng tiêu đề chương nếu bị nuốt mất
    has_title = bool(re.match(r"^\s*(?:chương|hồi|tiết|bài)\s+\d+", text, re.I))
    if not has_title and orig_text:
        title_prefix, _ = extract_chapter_title_prefix(orig_text)
        if title_prefix:
            text = reattach_chapter_title(title_prefix, text, chapter_index=chapter_index)
            changes.append(f"Gắn lại tiêu đề: '{title_prefix}'")
        else:
            text = f"Chương {chapter_index}\n\n{text}"
            changes.append(f"Gắn tiêu đề mặc định: 'Chương {chapter_index}'")

    # 1.3 Chuẩn hóa động mọi thuật ngữ/cảnh giới lệch chuẩn theo Book Bible của truyện
    if bible and getattr(bible, "terms", None):
        for term in bible.terms:
            for forbidden in getattr(term, "forbidden_variants", []) or []:
                if forbidden and re.search(rf"\b{re.escape(forbidden)}\b", text, re.I):
                    text = re.sub(rf"\b{re.escape(forbidden)}\b", term.vi_name, text, flags=re.I)
                    changes.append(f"Sửa theo Book Bible: '{forbidden}' -> '{term.vi_name}'")

    # 1.4 Ký tự rác ngoại lai (Arabic, Greek, Cyrillic)
    if re.search(r"[\u0600-\u06FF\u0750-\u077F\u0370-\u03FF\u1F00-\u1FFF\u0400-\u04FF]", text):
        text = re.sub(r"[\u0600-\u06FF\u0750-\u077F\u0370-\u03FF\u1F00-\u1FFF\u0400-\u04FF]", "", text)
        changes.append("Xóa ký tự ngoại lai lạ (Arabic/Greek)")

    # 1.5 Sửa lặp từ ngữ pháp
    if re.search(r"\bra ra\b", text):
        text = re.sub(r"\bra ra\b", "ra", text)
        changes.append("Sửa lặp từ: 'ra ra' -> 'ra'")
    if re.search(r"\blên lên\b", text):
        text = re.sub(r"\blên lên\b", "lên", text)
        changes.append("Sửa lặp từ: 'lên lên' -> 'lên'")
    if re.search(r"\blại lại\b", text):
        text = re.sub(r"\blại lại\b", "lại", text)
        changes.append("Sửa lặp từ: 'lại lại' -> 'lại'")

    # 1.6 Từ than thở tiếng Anh Haizz -> Than ôi
    if re.search(r"\bHaizz\b", text):
        text = re.sub(r"\bHaizz\b", "Than ôi", text)
        changes.append("Việt hóa: 'Haizz' -> 'Than ôi'")

    # =========================================================================
    # 2. QUY TẮC CỦA RIÊNG TỪNG TRUYỆN (NOVEL-SPECIFIC OVERRIDES)
    # =========================================================================
    if novel_id == "van-thu-chien-than":
        # Sửa xưng hô hội thoại đặc thù giữa Đỗ Phong và Mộc Linh ở chương 122
        if chapter_index == 122:
            before = text
            text = re.sub(r"\b[Nn]hìn em bị dọa kìa\b", "nhìn muội bị dọa kìa", text)
            text = re.sub(r"\b[Aa]nh còn dẫn em đi\b", "huynh còn dẫn muội đi", text)
            text = re.sub(r"\b[Aa]nh nói cho em nghe này\b", "huynh nói cho muội nghe này", text)
            text = re.sub(r"\b[Aa]nh dậy rồi à\b", "huynh dậy rồi à", text)
            text = re.sub(r"\b[Aa]nh dẫn em đi\b", "huynh dẫn muội đi", text)
            if text != before:
                changes.append("Sửa xưng hô Đỗ Phong - Mộc Linh (Chương 122): anh/em -> huynh/muội")

        # Sửa câu văn so sánh nhân hóa ở chương 151
        if chapter_index == 151:
            before = text
            text = text.replace(
                "người anh hàng xóm đang dỗ dành đứa em gái nhỏ",
                "người ca ca hàng xóm đang dỗ dành đứa tiểu muội nhỏ",
            )
            if text != before:
                changes.append("Sửa xưng hô so sánh (Chương 151): anh/em gái -> ca ca/tiểu muội")

        # Sửa đại từ xưng hô ở chương 160
        if chapter_index == 160:
            before = text
            text = text.replace("Tại sao anh ấy vẫn chưa gọi Quỷ Vương", "Tại sao hắn vẫn chưa gọi Quỷ Vương")
            if text != before:
                changes.append("Sửa đại từ (Chương 160): 'anh ấy' -> 'hắn'")

        # Sửa xưng hô trường học/hiện đại của đám sư đệ ở chương 170
        if chapter_index == 170:
            before = text
            text = re.sub(r"\b[Cc]húng em\b", "chúng đệ", text)
            text = re.sub(r"\b[Bb]ọn em\b", lambda m: "Bọn đệ" if m.group(0)[0].isupper() else "bọn đệ", text)
            text = re.sub(r"\b[Tt]ụi em\b", lambda m: "Bọn đệ" if m.group(0)[0].isupper() else "bọn đệ", text)
            text = re.sub(r"\bđông đảo anh em\b", "đông đảo huynh đệ", text)
            if text != before:
                changes.append("Sửa xưng hô sư đệ (Chương 170): 'chúng em/bọn em/tụi em' -> 'chúng đệ/bọn đệ'")

    return text.strip() + "\n", changes


def run_repair(
    novel_id: str,
    start_chapter: int = 122,
    end_chapter: int | None = None,
    dry_run: bool = True,
):
    print("=" * 80)
    print(f"[*] REPAIR TRANSLATED CHAPTERS FOR NOVEL: '{novel_id}'")
    print(f"    - Chapter range: {start_chapter} to {end_chapter or 'MAX'}")
    print(f"    - Mode: {'DRY-RUN (Xem trước, KHÔNG ghi đè)' if dry_run else 'APPLY (Thực thi ghi đè & cập nhật DB)'}")
    print("=" * 80)

    bible = storage_repo.get_bible(novel_id)
    if not bible:
        print(f"[!] Không tìm thấy Book Bible cho novel '{novel_id}'. Hủy thao tác.")
        return

    qa_service = QAService(None)

    trans_dir = Path("storage/novels") / novel_id / "translated"
    orig_dir = Path("storage/novels") / novel_id / "original"
    drafts_dir = Path("storage/novels") / novel_id / "drafts"

    if not trans_dir.exists():
        print(f"[!] Thư mục {trans_dir} không tồn tại!")
        return

    all_indexes = set()
    for f in trans_dir.glob("ch_*.txt"):
        m = re.search(r"\d+", f.stem)
        if m:
            all_indexes.add(int(m.group(0)))
    if drafts_dir.exists():
        for f in drafts_dir.glob("ch_*.txt"):
            m = re.search(r"\d+", f.stem)
            if m:
                all_indexes.add(int(m.group(0)))

    target_files = []
    for idx in sorted(all_indexes):
        if idx >= start_chapter and (end_chapter is None or idx <= end_chapter):
            fname = f"ch_{idx:04d}.txt"
            trans_f = trans_dir / fname
            draft_f = drafts_dir / fname
            # Ưu tiên lấy file translated nếu có, nếu chưa có thì lấy từ draft
            fpath = trans_f if trans_f.exists() else draft_f
            target_files.append((idx, fpath))

    if not target_files:
        print(f"[!] Không tìm thấy file dịch nào trong dải {start_chapter}..{end_chapter or 'MAX'}")
        return

    print(f"[*] Tìm thấy {len(target_files)} chương cần kiểm tra đối soát.\n")

    results = []
    for idx, fpath in target_files:
        orig_path = orig_dir / fpath.name
        orig_text = orig_path.read_text(encoding="utf-8", errors="ignore") if orig_path.exists() else ""
        trans_text = fpath.read_text(encoding="utf-8", errors="ignore")

        issues_before = qa_service.fast_rule_check(orig_text, trans_text, bible)
        repaired_text, fixes_applied = repair_chapter_text(
            idx, trans_text, orig_text, bible=bible, novel_id=novel_id
        )
        issues_after = qa_service.fast_rule_check(orig_text, repaired_text, bible)

        source_missing = not orig_text or not orig_text.strip()
        remaining_issues = [i.issue for i in issues_after]
        if source_missing:
            remaining_issues.insert(0, "Thiếu nội dung bản gốc; không được tự động chứng nhận COMPLETED")
        passed = not source_missing and len(issues_after) == 0
        results.append({
            "chapter": idx,
            "file": fpath.name,
            "issues_before": len(issues_before),
            "issues_after": len(issues_after),
            "fixes_applied": fixes_applied,
            "remaining_issues": remaining_issues,
            "passed": passed,
            "repaired_text": repaired_text,
        })

    print(f"{'Chương':<10} | {'QA Trước':<10} | {'QA Sau':<8} | {'Trạng Thái':<12} | {'Các sửa đổi đã thực hiện'}")
    print("-" * 80)

    clean_count = 0
    for r in results:
        ch_str = f"Chương {r['chapter']:04d}"
        before_str = f"{r['issues_before']} lỗi"
        after_str = f"{r['issues_after']} lỗi"
        status_str = "[OK] SẠCH" if r["passed"] else "[!] CÒN LỖI"
        fixes_str = "; ".join(r["fixes_applied"]) if r["fixes_applied"] else "(Bản dịch đã sạch, không cần sửa)"

        if r["passed"]:
            clean_count += 1

        print(f"{ch_str:<10} | {before_str:<10} | {after_str:<8} | {status_str:<12} | {fixes_str}")
        if r["remaining_issues"]:
            for issue in r["remaining_issues"]:
                print(f"             └── ⚠️ Lỗi còn lại: {issue}")

    print("\n" + "=" * 80)
    print(f"[*] TỔNG KẾT KIỂM TOÁN:")
    print(f"    - Tổng số chương kiểm tra: {len(results)}")
    print(f"    - Số chương SẠCH 100% (0 lỗi QA): {clean_count} / {len(results)} ({clean_count / len(results) * 100:.1f}%)")
    print(f"    - Số chương còn lỗi cần LLM can thiệp: {len(results) - clean_count}")
    print("=" * 80)

    if not dry_run:
        print("\n[*] Đang áp dụng các thay đổi vào file storage và cập nhật database...")
        from app.db.session import db_session
        from app.modules.library.persistence.legacy_models import ChapterModel, NovelModel

        applied_count = 0
        for r in results:
            draft_path = drafts_dir / r["file"]
            if r["passed"] and draft_path.exists():
                try:
                    draft_path.unlink()
                except Exception:
                    pass

            trans_path = trans_dir / r["file"]
            if not r["fixes_applied"] and r["passed"] and trans_path.exists():
                continue

            trans_path.write_text(r["repaired_text"], encoding="utf-8")
            applied_count += 1

        # Cập nhật database
        try:
            with db_session() as session:
                novel = session.query(NovelModel).filter(NovelModel.novel_id == novel_id).first()
                if novel:
                    db_chapters = {c.chapter_index: c for c in novel.chapters}
                    for r in results:
                        ch_model = db_chapters.get(r["chapter"])
                        if ch_model:
                            if r["passed"]:
                                ch_model.status = ChapterStatus.COMPLETED.value
                                ch_model.error_message = None
                            else:
                                ch_model.status = ChapterStatus.NEEDS_REVIEW.value
                                ch_model.error_message = "; ".join(r["remaining_issues"][:3])
                            ch_model.translated_text_preview = r["repaired_text"][:200]
                    novel.translated_chapters = sum(
                        1 for c in novel.chapters if c.status == ChapterStatus.COMPLETED.value
                    )
                    session.commit()
                    print(f"[✓] Đã cập nhật trạng thái chapters trong Database thành công! (Novel có {novel.translated_chapters} chương COMPLETED)")
        except Exception as e:
            print(f"[!] Cảnh báo DB: {e}")

        print(f"[✓] Đã ghi đè thành công {applied_count} file chương vào storage!")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Vá và chuẩn hóa tự động các chương dịch cũ.")
    parser.add_argument("--novel-id", default="van-thu-chien-than", help="ID của truyện")
    parser.add_argument("--start", type=int, default=122, help="Chương bắt đầu")
    parser.add_argument("--end", type=int, default=162, help="Chương kết thúc")
    parser.add_argument("--apply", action="store_true", help="Ghi thay đổi vào file và cập nhật DB (mặc định là dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ xem trước các thay đổi")

    args = parser.parse_args()
    is_dry_run = not args.apply

    run_repair(
        novel_id=args.novel_id,
        start_chapter=args.start,
        end_chapter=args.end,
        dry_run=is_dry_run,
    )


if __name__ == "__main__":
    main()
