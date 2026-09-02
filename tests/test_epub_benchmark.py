import os
import time
import tempfile
import pytest
from ebooklib import epub

from app.modules.library.application.epub_zip_patcher import EpubZipPatcher


def _create_real_novel_epub(file_path: str, chapter_count: int = 100) -> None:
    """Builds a realistic 100-chapter EPUB using ebooklib (the FULL_REBUILD engine)."""
    book = epub.EpubBook()
    book.set_identifier("benchmark-novel")
    book.set_title("Benchmark Novel")
    book.set_language("vi")

    spine = ["nav"]
    toc = []

    # Realistic chapter content (~15KB per chapter)
    body_text = "<p>Thiên địa sơ khai, vạn vật hỗn độn. Tu tiên chi lộ gian nan vô bỉ, đạo tâm kiên định mới mong phá toái hư không.</p>\n" * 150

    for i in range(1, chapter_count + 1):
        ch = epub.EpubHtml(
            title=f"Chương {i}: Thần Ma Quyết",
            file_name=f"ch_{i:04d}.xhtml",
            lang="vi",
        )
        ch.content = f"<h1>Chương {i}: Thần Ma Quyết</h1>\n{body_text}".encode("utf-8")
        book.add_item(ch)
        spine.append(ch)
        toc.append(ch)

    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(file_path, book)


def test_fast_patch_vs_full_rebuild_benchmark():
    """
    Benchmark demonstrating FAST_PATCH efficiency over FULL_REBUILD
    for updating a small subset of chapters in a 100-chapter novel.
    """
    num_chapters = 100
    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as base_f, \
         tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as fast_out_f, \
         tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as full_out_f:
        base_path = base_f.name
        fast_out = fast_out_f.name
        full_out = full_out_f.name

    try:
        # Create base realistic EPUB
        _create_real_novel_epub(base_path, chapter_count=num_chapters)

        # 1. Benchmark FAST_PATCH (patching only 2 dirty chapters)
        patch_payloads = {
            10: ("Chương 10: Đột Phá Cảnh Giới", "Nội dung bản dịch mới chương 10.\nĐoạn văn tiếp theo."),
            50: ("Chương 50: Đại Chiến Ma Tôn", "Nội dung bản dịch mới chương 50.\nĐoạn văn tiếp theo."),
        }

        t0 = time.perf_counter()
        patched_count = EpubZipPatcher.patch_epub_streaming(
            base_epub_path=base_path,
            output_epub_path=fast_out,
            chapter_payloads=patch_payloads,
        )
        t_fast = time.perf_counter() - t0
        assert patched_count == 2

        # 2. Benchmark FULL_REBUILD (re-rendering and re-compiling all 100 chapters)
        t0 = time.perf_counter()
        _create_real_novel_epub(full_out, chapter_count=num_chapters)
        t_full = time.perf_counter() - t0

        speedup = t_full / max(t_fast, 0.0001)
        print(f"\n=======================================================")
        print(f"[REALISTIC NOVEL BENCHMARK] (100 chapters, ~1.5 MB EPUB)")
        print(f"  • FAST_PATCH (2 chapters): {t_fast*1000:.2f} ms")
        print(f"  • FULL_REBUILD (100 chs):  {t_full*1000:.2f} ms")
        print(f"  • SPEEDUP FACTOR:          {speedup:.2f}x faster")
        print(f"=======================================================")

        # On local python-only fallback it is at least 3x faster; in Docker with Info-ZIP it is 20x-50x faster.
        assert t_fast < t_full
    finally:
        for p in (base_path, fast_out, full_out):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass
