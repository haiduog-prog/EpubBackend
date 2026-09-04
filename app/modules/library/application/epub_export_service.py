"""
EPUB Export & Build Orchestrator
=================================
Orchestrates high-speed FAST_PATCH and FULL_REBUILD workflows with zero in-memory buffer,
manages immutable versioned artifact keys on Object Storage, and enforces retention policies.
"""

import os
import tempfile
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple, Set, Callable

from app.config import settings
from app.infrastructure.storage.facade import storage_repo
from app.modules.library.application.epub_zip_patcher import EpubZipPatcher
from app.schemas.library import ChapterStatus, NovelMetadata

logger = logging.getLogger("EpubBackend.EpubExportService")


class EpubBuildCancelledException(Exception):
    """Raised when an EPUB build job is cancelled by the user."""
    pass


class EpubExportService:
    """Owns EPUB assembly, fast delta patching, and versioned storage publishing."""

    def __init__(self, legacy):
        self._legacy = legacy

    def export(self, novel_id: str, output_path: Optional[str] = None, **kwargs) -> str:
        """Backward-compatible entry point returning the path to a compiled EPUB file."""
        return self._legacy.export_full_epub(novel_id, output_path, **kwargs)

    def build_and_publish_epub(
        self,
        novel_id: str,
        target_chapters: Optional[str] = None,
        force_rebuild: bool = False,
        dirty_chapters: Optional[List[int]] = None,
        job_id: Optional[str] = None,
        progress_callback: Optional[Callable[[str, Optional[int], int, int, int], None]] = None,
        is_cancelled_callback: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """
        Builds or patches an EPUB with streaming disk I/O, publishes the versioned artifact to storage,
        and manages retention without keeping large byte payloads in RAM.
        """
        novel: Optional[NovelMetadata] = self._legacy.get_novel(novel_id)
        if not novel:
            raise ValueError(f"Không tìm thấy bộ truyện '{novel_id}'")

        target_indexes: Set[int] = set()
        if dirty_chapters:
            target_indexes.update(dirty_chapters)
        elif target_chapters:
            for part in str(target_chapters).split(","):
                part = part.strip()
                if "-" in part:
                    p1, _, p2 = part.partition("-")
                    if p1.strip().isdigit() and p2.strip().isdigit():
                        target_indexes.update(range(int(p1.strip()), int(p2.strip()) + 1))
                elif part.isdigit():
                    target_indexes.add(int(part))
        elif novel.dirty_chapters:
            target_indexes.update(novel.dirty_chapters)

        # Determine strategy
        is_scoped_range = bool(target_indexes or dirty_chapters)
        use_fast_patch = bool(
            settings.epub_fast_patch_enabled
            and not force_rebuild
            and (not novel.is_structural_dirty or is_scoped_range)
        )

        # Determine candidate base EPUB keys in priority order:
        # 1. novel.current_epub_key
        # 2. Canonical full.epub keys
        # 3. Any existing revision files in exports/ sorted newest-to-oldest
        base_candidates: List[str] = []
        if novel.current_epub_key:
            base_candidates.append(novel.current_epub_key)

        base_candidates.extend([
            f"novels/{novel_id}/full.epub",
            f"{novel_id}/full.epub",
        ])

        def _rev_num(fn: str) -> int:
            try:
                base_fn = os.path.basename(fn)
                m = re.match(r"^r(\d+)", base_fn)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
            return -1

        exports_dir = os.path.join("storage", "novels", novel_id, "exports")
        if os.path.exists(exports_dir):
            try:
                local_epubs = sorted(
                    [f for f in os.listdir(exports_dir) if f.endswith(".epub")],
                    key=_rev_num,
                    reverse=True,
                )
                for f in local_epubs:
                    cand = f"novels/{novel_id}/exports/{f}"
                    if cand not in base_candidates:
                        base_candidates.append(cand)
            except Exception:
                pass

        try:
            remote_epubs = storage_repo.list_files(f"novels/{novel_id}/exports/")
            for rpath in sorted(remote_epubs, key=_rev_num, reverse=True):
                if rpath not in base_candidates:
                    base_candidates.append(rpath)
        except Exception:
            pass

        base_key: Optional[str] = None
        tmp_base_path: Optional[str] = None
        final_output_path: Optional[str] = None
        strategy = "full_rebuild"
        patched_count = 0
        created_temp_files: List[str] = []
        uploaded_versioned_key: Optional[str] = None
        build_completed_successfully = False
        artifact_token = uuid.uuid4().hex

        try:
            # Attempt FAST_PATCH if eligible
            if use_fast_patch:
                for cand_key in base_candidates:
                    try:
                        if not storage_repo.file_exists(cand_key):
                            continue
                    except Exception as chk_err:
                        logger.debug("Error checking base candidate '%s' on %s: %s", cand_key, novel_id, chk_err)
                        continue

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as tmp_candidate:
                        candidate_tmp_path = tmp_candidate.name
                    created_temp_files.append(candidate_tmp_path)

                    try:
                        dl_success = storage_repo.download_file_stream(cand_key, candidate_tmp_path)
                        if dl_success and os.path.exists(candidate_tmp_path) and os.path.getsize(candidate_tmp_path) > 0:
                            if EpubZipPatcher.is_layout_standardized(candidate_tmp_path):
                                base_key = cand_key
                                tmp_base_path = candidate_tmp_path
                                logger.info("Selected standardized base EPUB '%s' for FAST_PATCH on %s", base_key, novel_id)
                                break
                            else:
                                logger.info("Candidate '%s' for %s is not layout-standardized, trying next candidate", cand_key, novel_id)
                    except Exception as dl_err:
                        logger.warning("Failed testing candidate base '%s' for %s: %s", cand_key, novel_id, dl_err)

                    # Not chosen, clean up candidate tmp file
                    if candidate_tmp_path in created_temp_files:
                        created_temp_files.remove(candidate_tmp_path)
                    if os.path.exists(candidate_tmp_path):
                        try:
                            os.unlink(candidate_tmp_path)
                        except Exception:
                            pass

                if base_key and tmp_base_path and os.path.exists(tmp_base_path):
                    try:
                        # Determine which chapters need patching
                        chapters_to_patch = [
                            ch for ch in novel.chapters
                            if (not target_indexes or ch.chapter_index in target_indexes)
                            and (ch.status == ChapterStatus.COMPLETED or ch.r2_translated_key or (target_indexes and ch.chapter_index in target_indexes))
                        ]

                        total_patch = len(chapters_to_patch)
                        chapter_payloads: Dict[int, Tuple[str, str]] = {}
                        for idx_step, ch in enumerate(chapters_to_patch, start=1):
                            if is_cancelled_callback and is_cancelled_callback():
                                raise EpubBuildCancelledException(f"Job '{job_id or novel_id}' đã bị hủy.")
                            if progress_callback:
                                pct = int((idx_step / max(1, total_patch)) * 75)
                                progress_callback(
                                    f"Đang tải nội dung Chương {ch.chapter_index}: {ch.chapter_title}...",
                                    ch.chapter_index,
                                    idx_step,
                                    total_patch,
                                    pct,
                                )
                            txt = self._legacy.get_chapter_content(
                                novel_id,
                                ch.chapter_index,
                                version="translated",
                                allow_epub_self_heal=False,
                                is_cancelled_callback=is_cancelled_callback,
                                novel_meta=novel,
                            )
                            if not txt:
                                txt = self._legacy.get_chapter_content(
                                    novel_id,
                                    ch.chapter_index,
                                    version="original",
                                    allow_epub_self_heal=False,
                                    is_cancelled_callback=is_cancelled_callback,
                                    novel_meta=novel,
                                )
                            if txt:
                                chapter_payloads[ch.chapter_index] = (ch.chapter_title, txt)

                        if is_cancelled_callback and is_cancelled_callback():
                            raise EpubBuildCancelledException(f"Job '{job_id or novel_id}' đã bị hủy.")

                        if chapter_payloads:
                            if progress_callback:
                                progress_callback(
                                    f"Đang vá {len(chapter_payloads)} chương vào file EPUB...",
                                    None,
                                    len(chapter_payloads),
                                    total_patch,
                                    85,
                                )
                            os.makedirs(os.path.join("storage", "outputs"), exist_ok=True)
                            patched_out_path = os.path.join(
                                "storage",
                                "outputs",
                                f"{novel_id}_patch_r{(novel.built_revision or 0) + 1}_{artifact_token}.epub",
                            )
                            created_temp_files.append(patched_out_path)
                            patched_count = EpubZipPatcher.patch_epub_streaming(
                                base_epub_path=tmp_base_path,
                                output_epub_path=patched_out_path,
                                chapter_payloads=chapter_payloads,
                            )
                            final_output_path = patched_out_path
                            strategy = "fast_patch"
                            logger.info("FAST_PATCH succeeded for novel %s (%d chapters)", novel_id, patched_count)
                    except EpubBuildCancelledException:
                        raise
                    except Exception as exc:
                        logger.warning("FAST_PATCH failed for novel %s, falling back to FULL_REBUILD: %s", novel_id, exc)
                    finally:
                        if os.path.exists(tmp_base_path):
                            try:
                                os.unlink(tmp_base_path)
                            except Exception:
                                pass

            # Fallback to FULL_REBUILD if FAST_PATCH was not applicable or failed
            # CRITICAL: Published EPUB artifact MUST contain all novel chapters (target_chapters=None)
            if not final_output_path or not os.path.exists(final_output_path):
                logger.info("Executing FULL_REBUILD for novel %s (use_fast_patch=%s, base_key=%s)", novel_id, use_fast_patch, base_key)
                strategy = "full_rebuild"
                if job_id:
                    try:
                        from app.db.session import db_session
                        from app.modules.library.persistence.legacy_repository import LibraryRepository
                        with db_session() as j_session:
                            LibraryRepository.update_job_strategy(j_session, job_id, "full_rebuild")
                            j_session.commit()
                    except Exception as e:
                        logger.debug("Failed to update job strategy to full_rebuild: %s", e)
                final_output_path = self._legacy.export_full_epub(
                    novel_id,
                    force_rebuild=True,
                    target_chapters=None,
                    progress_callback=progress_callback,
                    is_cancelled_callback=is_cancelled_callback,
                )
                if final_output_path:
                    created_temp_files.append(final_output_path)
                strategy = "full_rebuild"
                patched_count = len(novel.chapters)

            if not final_output_path or not os.path.exists(final_output_path):
                raise RuntimeError(f"Failed to generate EPUB for novel '{novel_id}'")

            # Verify integrity and OCF compliance
            is_valid, err = EpubZipPatcher.verify_epub_archive(final_output_path)
            if not is_valid:
                raise RuntimeError(f"Generated EPUB archive validation failed: {err}")

            if is_cancelled_callback and is_cancelled_callback():
                raise EpubBuildCancelledException(f"Job '{job_id or novel_id}' đã bị hủy.")

            if progress_callback:
                progress_callback(
                    "Đang tải file EPUB lên Cloud Storage...",
                    None,
                    patched_count,
                    patched_count,
                    92,
                )

            # Compute next immutable revision key
            current_rev = int(novel.built_revision or 0)
            next_rev = current_rev + 1
            versioned_key = f"novels/{novel_id}/exports/r{next_rev}-{artifact_token}.epub"

            # Stream upload versioned immutable key
            uploaded_url = storage_repo.upload_file_stream(
                final_output_path,
                versioned_key,
                content_type="application/epub+zip",
            )
            if storage_repo.active_provider_name in ("supabase", "r2"):
                if not uploaded_url:
                    raise RuntimeError(f"Cloud storage upload stream failed for '{versioned_key}'")
                if storage_repo.active_provider_name == "supabase" and not storage_repo.file_exists_on_supabase(versioned_key):
                    raise RuntimeError(f"Cloud verification failed: '{versioned_key}' not found on Supabase Storage")
                if storage_repo.active_provider_name == "r2" and not storage_repo.file_exists_on_r2(versioned_key):
                    raise RuntimeError(f"Cloud verification failed: '{versioned_key}' not found on Cloudflare R2")
            elif not uploaded_url and not storage_repo.file_exists(versioned_key):
                raise RuntimeError(f"Upload EPUB artifact '{versioned_key}' to storage failed")

            # Mark uploaded_versioned_key ONLY after successful upload and verification
            uploaded_versioned_key = versioned_key

            # Verify cancellation right after uploading the immutable artifact. The
            # mutable legacy alias is promoted by the worker only after complete_job
            # commits successfully, so cancellation/failure cannot overwrite it.
            if is_cancelled_callback and is_cancelled_callback():
                raise EpubBuildCancelledException(f"Job '{job_id or novel_id}' đã bị hủy.")

            if progress_callback:
                progress_callback(
                    "Hoàn tất đóng gói EPUB!",
                    None,
                    patched_count,
                    patched_count,
                    100,
                )

            public_download_url = uploaded_url or storage_repo.get_public_url(versioned_key)
            build_completed_successfully = True

            return {
                "novel_id": novel_id,
                "strategy": strategy,
                "built_revision": next_rev,
                "epub_key": versioned_key,
                "download_url": public_download_url,
                "patched_chapters_count": patched_count,
                "local_file_path": final_output_path,
                "output_path": final_output_path,
                "cleanup_revision": next_rev,
            }
        except Exception:
            # Clean up orphaned cloud artifact if it was uploaded before error/cancellation
            if uploaded_versioned_key:
                try:
                    storage_repo.delete_file(uploaded_versioned_key)
                    logger.info("Cleaned up orphaned cloud artifact '%s' after error/cancellation", uploaded_versioned_key)
                except Exception as del_err:
                    logger.warning("Failed to clean up orphaned cloud artifact '%s': %s", uploaded_versioned_key, del_err)
            raise
        finally:
            # If build did not complete successfully, clean up all created local temp files
            if not build_completed_successfully:
                for tmp_file in created_temp_files:
                    if tmp_file and os.path.exists(tmp_file):
                        try:
                            os.unlink(tmp_file)
                        except Exception:
                            pass

    def cleanup_old_revisions_best_effort(
        self,
        novel_id: str,
        current_built_rev: int,
        current_epub_key: Optional[str] = None,
    ) -> None:
        """Best-effort retention cleanup for older revision artifacts."""
        try:
            self._cleanup_old_revisions(novel_id, current_built_rev, current_epub_key)
        except Exception as exc:
            logger.warning("Best-effort retention cleanup failed for %s: %s", novel_id, exc)

    def _cleanup_old_revisions(
        self,
        novel_id: str,
        current_rev: int,
        current_epub_key: Optional[str] = None,
    ) -> None:
        """Deletes artifacts older than current_rev - retention_copies + 1 (keeping exactly retention_copies)."""
        retention_copies = getattr(settings, "epub_storage_retention_copies", 2)
        oldest_to_keep = current_rev - retention_copies + 1
        if oldest_to_keep <= 0:
            return

        prefix = f"novels/{novel_id}/exports/"
        try:
            all_files = storage_repo.list_files(prefix)
            for fpath in all_files:
                fname = os.path.basename(fpath)
                match = re.fullmatch(r"r(\d+)(?:-[A-Za-z0-9]+)?\.epub", fname)
                if match:
                    revision = int(match.group(1))
                    is_duplicate_current_revision = (
                        current_epub_key is not None
                        and revision == current_rev
                        and fpath != current_epub_key
                    )
                    if revision < oldest_to_keep or is_duplicate_current_revision:
                        logger.info("Cleaning up obsolete EPUB revision artifact: %s", fpath)
                        storage_repo.delete_file(fpath)
        except Exception as exc:
            logger.warning("Failed to cleanup old EPUB revisions for novel %s: %s", novel_id, exc)

