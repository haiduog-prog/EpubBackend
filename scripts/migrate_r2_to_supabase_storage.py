"""
Script di chuyển toàn bộ Blob Files (ảnh bìa, file EPUB, file TXT chương, JSON) từ Cloudflare R2 sang Supabase Storage.

Cách sử dụng:
  # Chạy thử nghiệm (không upload thật):
  python scripts/migrate_r2_to_supabase_storage.py --dry-run

  # Chạy chuyển đổi thực tế:
  python scripts/migrate_r2_to_supabase_storage.py

  # Chỉ chuyển đổi một tiền tố cụ thể (ví dụ chỉ truyện novels/):
  python scripts/migrate_r2_to_supabase_storage.py --prefix novels/
"""

import argparse
import logging
import mimetypes
import os
import sys
import time
from typing import List

# Đảm bảo root thư mục nằm trong PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.core.storage import R2StorageProvider, SupabaseStorageProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("MigrateR2ToSupabase")


def migrate(
    prefix: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    batch_size: int = 20,
):
    logger.info("=== BẮT ĐẦU QUY TRÌNH DI CHUYỂN R2 SANG SUPABASE STORAGE ===")
    logger.info("Tùy chọn: prefix='%s', dry_run=%s, overwrite=%s", prefix, dry_run, overwrite)

    r2 = R2StorageProvider()
    supabase = SupabaseStorageProvider()

    if not r2.is_active:
        logger.error("Cloudflare R2 chưa được cấu hình hoặc thiếu credentials!")
        logger.error("Kiểm tra biến môi trường: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_R2_ACCESS_KEY_ID, CLOUDFLARE_R2_SECRET_ACCESS_KEY, CLOUDFLARE_R2_BUCKET_NAME")
        return False

    if not supabase.is_active:
        logger.error("Supabase Storage chưa được cấu hình hoặc thiếu credentials!")
        logger.error("Kiểm tra biến môi trường: SUPABASE_URL, SUPABASE_KEY, SUPABASE_STORAGE_BUCKET")
        return False

    logger.info("1. Đang quét danh sách file trên Cloudflare R2 (prefix='%s')...", prefix)
    try:
        r2_files = r2.list_files(prefix, raise_on_error=True)
    except Exception as exc:
        logger.error("Lỗi khi kết nối lấy danh sách file từ R2: %s", exc)
        return False

    total_files = len(r2_files)
    logger.info("Tìm thấy %d file trên Cloudflare R2.", total_files)

    if total_files == 0:
        logger.info("Không có file nào để di chuyển.")
        return True

    success_count = 0
    skipped_count = 0
    error_count = 0
    errors: List[str] = []

    start_time = time.time()

    for idx, key in enumerate(r2_files, 1):
        logger.info("[%d/%d] Đang xử lý: %s", idx, total_files, key)

        if not overwrite:
            # Kiểm tra xem file đã tồn tại trên Supabase chưa
            if not dry_run and supabase.file_exists(key):
                logger.info("  -> Bỏ qua (đã tồn tại trên Supabase): %s", key)
                skipped_count += 1
                continue

        if dry_run:
            logger.info("  [DRY-RUN] Sẽ tải %s từ R2 và upload lên Supabase", key)
            success_count += 1
            continue

        # 1. Tải nội dung từ R2
        try:
            content = r2.get_bytes(key, raise_on_error=True)
            if content is None:
                raise ValueError("Nội dung file rỗng hoặc không đọc được từ R2")
        except Exception as exc:
            logger.error("  -> Lỗi khi tải từ R2 (%s): %s", key, exc)
            error_count += 1
            errors.append(f"Download error on {key}: {exc}")
            continue

        # 2. Xác định Content-Type
        guessed_type, _ = mimetypes.guess_type(key)
        if key.endswith(".json"):
            content_type = "application/json; charset=utf-8"
        elif key.endswith(".epub"):
            content_type = "application/epub+zip"
        elif key.endswith(".txt"):
            content_type = "text/plain; charset=utf-8"
        elif key.endswith(".jpg") or key.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif key.endswith(".png"):
            content_type = "image/png"
        else:
            content_type = guessed_type or "application/octet-stream"

        # 3. Upload lên Supabase Storage
        try:
            pub_url = supabase.put_bytes(key, content, content_type=content_type)
            if pub_url:
                logger.info("  -> Thành công -> %s", pub_url)
                success_count += 1
            else:
                raise ValueError("Supabase trả về None khi upload")
        except Exception as exc:
            logger.error("  -> Lỗi khi upload lên Supabase (%s): %s", key, exc)
            error_count += 1
            errors.append(f"Upload error on {key}: {exc}")

    elapsed = time.time() - start_time
    logger.info("=== KẾT QUẢ DI CHUYỂN ===")
    logger.info("Tổng số file: %d", total_files)
    logger.info("Thành công: %d", success_count)
    logger.info("Bỏ qua (đã có): %d", skipped_count)
    logger.info("Lỗi: %d", error_count)
    logger.info("Thời gian thực hiện: %.2f giây", elapsed)

    if errors:
        logger.warning("Danh sách lỗi (%d):", len(errors))
        for err in errors[:20]:
            logger.warning(" - %s", err)
        if len(errors) > 20:
            logger.warning(" ... và %d lỗi khác", len(errors) - 20)

    return error_count == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Di chuyển dữ liệu Blob từ Cloudflare R2 sang Supabase Storage")
    parser.add_argument("--prefix", default="", help="Tiền tố thư mục cần di chuyển (vd: novels/)")
    parser.add_argument("--dry-run", action="store_true", help="Chạy thử nghiệm không upload thật")
    parser.add_argument("--overwrite", action="store_true", help="Ghi đè file nếu đã tồn tại trên Supabase")
    args = parser.parse_args()

    success = migrate(
        prefix=args.prefix,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )
    sys.exit(0 if success else 1)
