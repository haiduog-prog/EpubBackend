"""CLI Script to merge two duplicate book entities in PostgreSQL.

Usage:
    python scripts/merge_duplicate_books.py --source <source_book_id> --target <target_book_id>
    python scripts/merge_duplicate_books.py --dry-run
"""

import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, update, delete, func
from app.db.session import db_session
from app.db.models.character_profile import (
    ProfileBookModel,
    ProfileEditionModel,
    ProfileSubmissionModel,
    ProfileEventModel,
)


def merge_books(source_book_id: str, target_book_id: str, dry_run: bool = False) -> bool:
    print(f"\n========================================================")
    print(f" BOOK MERGE UTILITY")
    print(f" Source (duplicate to delete) : {source_book_id}")
    print(f" Target (canonical to keep)   : {target_book_id}")
    print(f" Dry Run Mode                 : {dry_run}")
    print(f"========================================================\n")

    if source_book_id == target_book_id:
        print("[!] Source và Target book_id giống nhau. Không có thao tác nào được thực hiện.")
        return False

    with db_session() as session:
        source_book = session.get(ProfileBookModel, source_book_id)
        target_book = session.get(ProfileBookModel, target_book_id)

        if not source_book:
            print(f"[-] LỖI: Source book '{source_book_id}' không tồn tại trong database.")
            return False

        if not target_book:
            print(f"[-] LỖI: Target book '{target_book_id}' không tồn tại trong database.")
            return False

        print(f"[+] Tìm thấy Source Book: '{source_book.title}' ({source_book.author})")
        print(f"[+] Tìm thấy Target Book: '{target_book.title}' ({target_book.author})")

        # Đếm số lượng thực thể con cần chuyển
        ed_count = session.scalar(
            select(func.count(ProfileEditionModel.edition_id)).where(ProfileEditionModel.book_id == source_book_id)
        ) or 0
        sub_count = session.scalar(
            select(func.count(ProfileSubmissionModel.submission_id)).where(ProfileSubmissionModel.book_id == source_book_id)
        ) or 0
        ev_count = session.scalar(
            select(func.count(ProfileEventModel.event_id)).where(ProfileEventModel.book_id == source_book_id)
        ) or 0

        print(f"\n[*] Thống kê dữ liệu cần chuyển từ '{source_book_id}':")
        print(f"    - Editions    : {ed_count}")
        print(f"    - Submissions : {sub_count}")
        print(f"    - Events      : {ev_count}")

        if dry_run:
            print("\n[DRY RUN] Đã kiểm tra đối chiếu dữ liệu thành công. Không có thay đổi nào được ghi vào DB.")
            return True

        # 1. Chuyển ProfileEditionModel
        if ed_count > 0:
            session.execute(
                update(ProfileEditionModel)
                .where(ProfileEditionModel.book_id == source_book_id)
                .values(book_id=target_book_id)
            )
            print(f"[+] Đã chuyển {ed_count} editions sang target book.")

        # 2. Chuyển ProfileSubmissionModel
        if sub_count > 0:
            session.execute(
                update(ProfileSubmissionModel)
                .where(ProfileSubmissionModel.book_id == source_book_id)
                .values(book_id=target_book_id)
            )
            print(f"[+] Đã chuyển {sub_count} submissions sang target book.")

        # 3. Chuyển ProfileEventModel
        if ev_count > 0:
            session.execute(
                update(ProfileEventModel)
                .where(ProfileEventModel.book_id == source_book_id)
                .values(book_id=target_book_id)
            )
            print(f"[+] Đã chuyển {ev_count} events sang target book.")

        # 4. Xóa source ProfileBookModel
        session.execute(
            delete(ProfileBookModel).where(ProfileBookModel.book_id == source_book_id)
        )
        print(f"[+] Đã xóa bản ghi nguồn '{source_book_id}' khỏi profile_books.")

        session.commit()
        print(f"\n[✓] HOÀN TẤT: Gộp thành công '{source_book_id}' vào '{target_book_id}'!")
        return True


def main():
    parser = argparse.ArgumentParser(description="Gộp 2 đầu sách trùng lặp trong Book Bible PostgreSQL")
    parser.add_argument(
        "--source",
        default="tram-than-ta-hoc-tram-than-o-benh-vien-tam-than",
        help="Source book_id cần xóa (mặc định: tram-than-ta-hoc-tram-than-o-benh-vien-tam-than)",
    )
    parser.add_argument(
        "--target",
        default="ta-tai-benh-vien-tam-than-hoc-tram-than-ai",
        help="Target book_id cần giữ lại (mặc định: ta-tai-benh-vien-tam-than-hoc-tram-than-ai)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chạy kiểm tra thử mà không ghi vào database",
    )

    args = parser.parse_args()
    success = merge_books(args.source, args.target, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
