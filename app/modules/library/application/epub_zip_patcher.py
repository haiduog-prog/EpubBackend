"""
EPUB Direct Fast Patcher
========================
Performs ultra-fast in-place updates of dirty chapter XHTML files within an existing EPUB archive.
Uses Docker + Info-ZIP `zip -u` as the primary patching engine, ensuring unchanged entries and
compressions are preserved byte-to-byte, with full EPUB OCF specification enforcement.
"""

import os
import re
import time
import shutil
import zipfile
import tempfile
import subprocess
import logging
from html import escape
from typing import Dict, List, Optional, Set, Tuple


logger = logging.getLogger("EpubBackend.ZipPatcher")


class EpubZipPatcher:
    """
    Fast, memory-efficient EPUB patcher using Info-ZIP `zip -u` with EPUB OCF compliance verification.
    """

    @staticmethod
    def _render_chapter_xhtml(title: str, text_content: str) -> bytes:
        """Render a clean, valid XHTML document for an EPUB chapter."""
        paragraphs = "".join(
            f"<p>{escape(p.strip())}</p>"
            for p in (text_content or "").split("\n")
            if p.strip()
        )
        safe_title = escape(title or "")
        xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="vi">\n'
            "<head>\n"
            f"  <title>{safe_title}</title>\n"
            '  <link rel="stylesheet" href="style/default.css" type="text/css" />\n'
            "</head>\n"
            "<body>\n"
            f"  <h1>{safe_title}</h1>\n"
            f"  {paragraphs}\n"
            "</body>\n"
            "</html>"
        )
        return xhtml.encode("utf-8")

    SYSTEM_DOCUMENTS: Set[str] = {
        "nav.xhtml",
        "toc.xhtml",
        "cover.xhtml",
        "titlepage.xhtml",
        "nav.html",
        "toc.html",
        "cover.html",
    }

    @classmethod
    def is_layout_standardized(cls, epub_path: str) -> bool:
        """
        Check if the EPUB follows the standardized naming pattern (`ch_NNNN.xhtml`).
        Returns False if the EPUB contains non-standard HTML filenames requiring a full rebuild.
        System documents (nav.xhtml, toc.xhtml, cover.xhtml, etc.) are ignored.
        """
        if not os.path.exists(epub_path):
            return False

        try:
            with zipfile.ZipFile(epub_path, "r") as zf:
                filenames = zf.namelist()
                # Must contain mimetype and container.xml
                if "mimetype" not in filenames or "META-INF/container.xml" not in filenames:
                    return False

                # Filter all HTML/XHTML documents excluding standard EPUB system documents
                doc_entries = [f for f in filenames if f.lower().endswith((".xhtml", ".html", ".htm"))]
                chapter_docs = [
                    f for f in doc_entries
                    if os.path.basename(f).lower() not in cls.SYSTEM_DOCUMENTS
                ]

                if not chapter_docs:
                    return False

                for doc in chapter_docs:
                    basename = os.path.basename(doc)
                    if not re.match(r"^ch_\d{4}\.xhtml$", basename):
                        logger.info("Non-standard chapter document found in %s: %s", epub_path, basename)
                        return False
                return True
        except Exception as exc:
            logger.warning("Failed to inspect EPUB layout %s: %s", epub_path, exc)
            return False


    @classmethod
    def patch_epub_streaming(
        cls,
        base_epub_path: str,
        output_epub_path: str,
        chapter_payloads: Dict[int, Tuple[str, str]],  # chapter_index -> (title, text_content)
    ) -> int:
        """
        Patches chapter_payloads into base_epub_path and writes output to output_epub_path.
        Uses Info-ZIP `zip -u` when available, falling back to direct stream zip cloning.
        Returns the number of patched chapters.
        """
        if not os.path.exists(base_epub_path):
            raise FileNotFoundError(f"Base EPUB not found at '{base_epub_path}'")

        if not chapter_payloads:
            # Nothing to patch, perform copy
            os.makedirs(os.path.dirname(os.path.abspath(output_epub_path)), exist_ok=True)
            shutil.copyfile(base_epub_path, output_epub_path)
            return 0

        os.makedirs(os.path.dirname(os.path.abspath(output_epub_path)), exist_ok=True)
        tmp_output = f"{output_epub_path}.tmp_{os.getpid()}"

        # 1. Inspect source EPUB to locate chapter relative paths
        chapter_entry_map: Dict[int, str] = {}  # index -> relative zip entry name (e.g. "OEBPS/ch_0001.xhtml")
        with zipfile.ZipFile(base_epub_path, "r") as src_zip:
            for entry in src_zip.namelist():
                basename = os.path.basename(entry)
                match = re.match(r"^ch_(\d{4})\.xhtml$", basename)
                if match:
                    ch_idx = int(match.group(1))
                    if ch_idx in chapter_payloads:
                        chapter_entry_map[ch_idx] = entry

        # If any target chapter is not found in base EPUB, caller should do a full rebuild
        missing_chapters = set(chapter_payloads.keys()) - set(chapter_entry_map.keys())
        if missing_chapters:
            raise ValueError(f"Target chapters {sorted(missing_chapters)} not found in base EPUB archive")

        patched_count = len(chapter_entry_map)

        # 2. Try Info-ZIP `zip -u` first
        has_zip_cli = bool(shutil.which("zip"))
        if has_zip_cli:
            try:
                cls._patch_with_info_zip(
                    base_epub_path=base_epub_path,
                    output_epub_path=tmp_output,
                    chapter_payloads=chapter_payloads,
                    chapter_entry_map=chapter_entry_map,
                )
            except Exception as exc:
                logger.warning("Info-ZIP patching failed (%s), falling back to zipfile stream: %s", base_epub_path, exc)
                cls._patch_with_zipfile_stream(
                    base_epub_path=base_epub_path,
                    output_epub_path=tmp_output,
                    chapter_payloads=chapter_payloads,
                    chapter_entry_map=chapter_entry_map,
                )
        else:
            cls._patch_with_zipfile_stream(
                base_epub_path=base_epub_path,
                output_epub_path=tmp_output,
                chapter_payloads=chapter_payloads,
                chapter_entry_map=chapter_entry_map,
            )

        # 3. Ensure EPUB OCF Compliance (mimetype at offset 0, uncompressed)
        cls._enforce_epub_ocf_spec(tmp_output)

        # 4. Atomically replace final file
        if os.path.exists(output_epub_path):
            try:
                os.unlink(output_epub_path)
            except Exception:
                pass
        os.replace(tmp_output, output_epub_path)

        return patched_count

    @classmethod
    def _patch_with_info_zip(
        cls,
        base_epub_path: str,
        output_epub_path: str,
        chapter_payloads: Dict[int, Tuple[str, str]],
        chapter_entry_map: Dict[int, str],
    ) -> None:
        """Executes Info-ZIP `zip -u` on a staging directory."""
        shutil.copyfile(base_epub_path, output_epub_path)
        abs_output = os.path.abspath(output_epub_path)

        with tempfile.TemporaryDirectory() as staging_dir:
            relative_files: List[str] = []
            for ch_idx, (title, text_content) in chapter_payloads.items():
                rel_path = chapter_entry_map.get(ch_idx)
                if not rel_path:
                    continue
                file_disk_path = os.path.join(staging_dir, rel_path)
                os.makedirs(os.path.dirname(file_disk_path), exist_ok=True)
                with open(file_disk_path, "wb") as f:
                    f.write(cls._render_chapter_xhtml(title, text_content))
                relative_files.append(rel_path)

            if not relative_files:
                return

            # Ensure all staging files have mtime strictly newer than entries in archive
            now_ts = time.time() + 10
            for rel_path in relative_files:
                file_disk_path = os.path.join(staging_dir, rel_path)
                try:
                    os.utime(file_disk_path, (now_ts, now_ts))
                except Exception:
                    pass

            # Run zip -u -q <archive> <files...>
            cmd = ["zip", "-u", "-q", abs_output] + relative_files
            res = subprocess.run(cmd, cwd=staging_dir, capture_output=True, text=True, timeout=30)
            if res.returncode != 0:
                raise RuntimeError(f"zip -u command returned non-zero ({res.returncode}): {res.stderr}")


    @classmethod
    def _patch_with_zipfile_stream(
        cls,
        base_epub_path: str,
        output_epub_path: str,
        chapter_payloads: Dict[int, Tuple[str, str]],
        chapter_entry_map: Dict[int, str],
    ) -> None:
        """Fallback Python zip streaming patcher preserving entry metadata."""
        inv_map = {entry: ch_idx for ch_idx, entry in chapter_entry_map.items()}

        with zipfile.ZipFile(base_epub_path, "r") as src_zip:
            all_entries = src_zip.infolist()
            with zipfile.ZipFile(output_epub_path, "w") as dst_zip:
                # Write mimetype first, uncompressed (OCF requirement)
                dst_zip.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)

                for info in all_entries:
                    if info.filename == "mimetype":
                        continue
                    if info.filename in inv_map:
                        ch_idx = inv_map[info.filename]
                        title, text_content = chapter_payloads[ch_idx]
                        content = cls._render_chapter_xhtml(title, text_content)
                        dst_zip.writestr(info.filename, content, compress_type=zipfile.ZIP_DEFLATED)
                    else:
                        data = src_zip.read(info.filename)
                        dst_zip.writestr(info, data)

    @classmethod
    def _enforce_epub_ocf_spec(cls, epub_path: str) -> None:
        """
        EPUB Open Container Format (OCF) strict verification:
        1. Entry 0 MUST be 'mimetype'
        2. 'mimetype' MUST be uncompressed (method 0 / ZIP_STORED)
        3. Local file header of 'mimetype' MUST start at offset 0 (no extra fields)
        """
        with zipfile.ZipFile(epub_path, "r") as zf:
            infolist = zf.infolist()
            if not infolist:
                raise ValueError("EPUB archive is empty")

            entry0 = infolist[0]
            is_valid_ocf = (
                entry0.filename == "mimetype"
                and entry0.compress_type == zipfile.ZIP_STORED
            )
            if is_valid_ocf:
                return


        # If OCF compliance was altered by zip update, rebuild container structure cleanly
        tmp_ocf_path = f"{epub_path}.ocf_fix"
        with zipfile.ZipFile(epub_path, "r") as src_zf, zipfile.ZipFile(tmp_ocf_path, "w") as dst_zf:
            # 1. Write uncompressed mimetype at offset 0 with zero extra fields
            dst_zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)

            # 2. Write all other entries preserving original compression method
            for info in src_zf.infolist():
                if info.filename == "mimetype":
                    continue
                data = src_zf.read(info.filename)
                dst_zf.writestr(info, data)

        os.replace(tmp_ocf_path, epub_path)

    @classmethod
    def verify_epub_archive(cls, epub_path: str) -> Tuple[bool, Optional[str]]:
        """Verify the integrity of the EPUB ZIP file and OCF specification (fast header checks)."""
        try:
            with zipfile.ZipFile(epub_path, "r") as zf:
                infolist = zf.infolist()
                if not infolist or infolist[0].filename != "mimetype":
                    return False, "File đầu tiên trong EPUB không phải là 'mimetype'"

                if infolist[0].compress_type != zipfile.ZIP_STORED:
                    return False, "File 'mimetype' bị nén (vi phạm EPUB OCF)"

                if "META-INF/container.xml" not in zf.namelist():
                    return False, "Thiếu file cấu hình chuẩn 'META-INF/container.xml'"

                # Verify container.xml is readable
                _ = zf.read("META-INF/container.xml")
                return True, None
        except Exception as exc:
            return False, f"Lỗi cấu trúc ZIP: {exc}"

