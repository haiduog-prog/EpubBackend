"""
EPUB Background Build Consumer
===============================
Runs a durable in-process worker with concurrency=1 on the Render instance.
Uses a dedicated raw database connection to hold the PostgreSQL Advisory Lock throughout the build cycle,
runs periodic background heartbeats for active job leases, and executes builds in a separate thread.
"""

import os
import asyncio
import logging
import uuid
from typing import Optional, Dict

from sqlalchemy import text

from app.config import settings
from app.db.session import engine, db_session
from app.modules.library.application.facade import library_service
from app.modules.library.application.epub_export_service import EpubBuildCancelledException
from app.modules.library.persistence.legacy_repository import LibraryRepository

logger = logging.getLogger("EpubBackend.EpubBuildWorker")

_worker_task: Optional[asyncio.Task] = None
_worker_running: bool = False
WORKER_ID: str = f"worker-{uuid.uuid4().hex[:8]}"


async def _run_job_heartbeat(job_id: str, stop_event: asyncio.Event, lease_state: Dict[str, bool]) -> None:
    """Periodically renews the worker lease until build completes, tracking lease validity."""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=25.0)
            break
        except asyncio.TimeoutError:
            try:
                with db_session() as hb_session:
                    success = LibraryRepository.heartbeat_job(
                        session=hb_session,
                        job_id=job_id,
                        lease_token=WORKER_ID,
                        lease_duration_seconds=300,
                    )
                    hb_session.commit()
                    if not success:
                        lease_state["lost"] = True
                        logger.warning("Heartbeat failed for job '%s' (lease lost or expired)", job_id)
            except Exception as hb_err:
                logger.warning("Error during lease heartbeat for job '%s': %s", job_id, hb_err)


async def run_epub_build_consumer() -> None:
    """Main loop for the EPUB background build consumer."""
    global _worker_running
    _worker_running = True
    logger.info("EPUB Build Consumer started (worker_id=%s)", WORKER_ID)

    # Recover any stale jobs on startup
    if settings.structured_storage_backend in ("dual", "postgres"):
        try:
            with db_session() as session:
                recovered = LibraryRepository.recover_stale_jobs(session)
                session.commit()
                if recovered > 0:
                    logger.info("Recovered %d stale EPUB build jobs on startup", recovered)
        except Exception as exc:
            logger.warning("Failed to recover stale EPUB build jobs on startup: %s", exc)

    while _worker_running:
        try:
            if settings.structured_storage_backend not in ("dual", "postgres"):
                await asyncio.sleep(5.0)
                continue

            # Dedicated raw database connection checkout that holds the advisory lock continuously.
            # AUTOCOMMIT ensures advisory lock statements are committed immediately and the lock
            # is not accidentally released by a pool-level rollback when the connection is returned.
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_conn:
                has_lock = False
                try:
                    res = lock_conn.execute(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": LibraryRepository.EPUB_GLOBAL_BUILD_LOCK_ID},
                    ).scalar()
                    has_lock = bool(res)
                except Exception as lock_err:
                    logger.warning("Advisory lock check skipped/failed: %s", lock_err)
                    has_lock = True

                if not has_lock:
                    await asyncio.sleep(settings.epub_build_debounce_seconds or 3.0)
                    continue

                try:
                    claimed_job = None
                    with db_session() as claim_session:
                        claimed_job = LibraryRepository.claim_next_job(
                            session=claim_session,
                            worker_id=WORKER_ID,
                            lease_duration_seconds=300,
                        )
                        if claimed_job:
                            # Snapshot claimed job details
                            job_id = claimed_job.job_id
                            novel_id = claimed_job.novel_id
                            strategy = claimed_job.strategy
                            raw_dirty = claimed_job.dirty_chapters
                            dirty_chapters = LibraryRepository._normalize_dirty_chapters(raw_dirty)
                            is_structural = bool(claimed_job.is_structural)
                            claim_session.commit()

                    if not claimed_job:
                        # Nothing to build right now
                        await asyncio.sleep(settings.epub_build_debounce_seconds or 3.0)
                        continue

                    logger.info(
                        "Processing EPUB build job '%s' for novel '%s' (strategy=%s, dirty_count=%d)",
                        job_id,
                        novel_id,
                        strategy,
                        len(dirty_chapters),
                    )

                    # Start background lease heartbeat with state tracking
                    stop_heartbeat = asyncio.Event()
                    lease_state = {"lost": False}
                    hb_task = asyncio.create_task(_run_job_heartbeat(job_id, stop_heartbeat, lease_state))

                    local_cleanup_path = None
                    try:
                        # Brief debounce
                        await asyncio.sleep(1.0)

                        def _make_progress_reporter(target_job_id: str):
                            def _cb(step: str, ch_idx: Optional[int], processed: int, total: int, pct: int):
                                try:
                                    with db_session() as p_session:
                                        LibraryRepository.update_job_progress(
                                            session=p_session,
                                            job_id=target_job_id,
                                            current_step=step,
                                            current_chapter=ch_idx,
                                            processed_chapters=processed,
                                            total_chapters=total,
                                            progress_percentage=pct,
                                        )
                                        p_session.commit()
                                except Exception as p_err:
                                    logger.debug("Failed to record progress for job '%s': %s", target_job_id, p_err)
                            return _cb

                        def _make_cancel_checker(target_job_id: str):
                            def _chk() -> bool:
                                try:
                                    with db_session() as c_session:
                                        return LibraryRepository.is_job_cancelled(c_session, target_job_id)
                                except Exception:
                                    return False
                            return _chk

                        # Execute build in thread executor
                        loop = asyncio.get_running_loop()
                        result = await loop.run_in_executor(
                            None,
                            lambda: library_service.export_service.build_and_publish_epub(
                                novel_id=novel_id,
                                force_rebuild=bool(is_structural or strategy == "full_rebuild"),
                                dirty_chapters=dirty_chapters,
                                job_id=job_id,
                                progress_callback=_make_progress_reporter(job_id),
                                is_cancelled_callback=_make_cancel_checker(job_id),
                            ),
                        )

                        local_cleanup_path = result.get("local_file_path") or result.get("output_path")
                        built_rev = result["built_revision"]

                        # If lease was lost during a very long build, do not overwrite state
                        if lease_state["lost"]:
                            logger.warning("Job '%s' finished build but lease was lost during execution; skipping completion", job_id)
                        else:
                            # Complete job in database
                            with db_session() as comp_session:
                                comp_success = LibraryRepository.complete_job(
                                    session=comp_session,
                                    job_id=job_id,
                                    built_revision=built_rev,
                                    epub_key=result["epub_key"],
                                    worker_id=WORKER_ID,
                                )
                                comp_session.commit()

                            if comp_success:
                                # Best-effort retention cleanup outside transaction
                                library_service.export_service.cleanup_old_revisions_best_effort(novel_id, built_rev)
                                logger.info(
                                    "EPUB build job '%s' completed successfully -> revision %d (%s)",
                                    job_id,
                                    built_rev,
                                    result["epub_key"],
                                )

                    except EpubBuildCancelledException as cancel_exc:
                        logger.info("EPUB build job '%s' cancelled by user: %s", job_id, cancel_exc)
                        with db_session() as cancel_session:
                            LibraryRepository.cancel_job(
                                session=cancel_session,
                                novel_id=novel_id,
                                job_id=job_id,
                            )
                            cancel_session.commit()
                    except Exception as build_exc:
                        logger.error("EPUB build job '%s' failed: %s", job_id, build_exc, exc_info=True)
                        with db_session() as fail_session:
                            LibraryRepository.fail_or_retry_job(
                                session=fail_session,
                                job_id=job_id,
                                error_message=str(build_exc),
                            )
                            fail_session.commit()
                    finally:
                        stop_heartbeat.set()
                        await asyncio.gather(hb_task, return_exceptions=True)

                        # Clean up temporary local output artifact
                        if local_cleanup_path and os.path.exists(local_cleanup_path):
                            norm_path = local_cleanup_path.replace("\\", "/")
                            if "storage/outputs" in norm_path or "patch_r" in norm_path:
                                try:
                                    os.unlink(local_cleanup_path)
                                except Exception:
                                    pass

                finally:
                    try:
                        lock_conn.execute(
                            text("SELECT pg_advisory_unlock(:lock_id)"),
                            {"lock_id": LibraryRepository.EPUB_GLOBAL_BUILD_LOCK_ID},
                        )
                    except Exception:
                        pass

        except asyncio.CancelledError:
            logger.info("EPUB Build Consumer task cancelled")
            break
        except Exception as loop_exc:
            logger.error("Error in EPUB build consumer loop: %s", loop_exc, exc_info=True)
            await asyncio.sleep(3.0)

    logger.info("EPUB Build Consumer stopped")


def start_epub_build_worker() -> None:
    """Starts the EPUB build consumer background task if not already running."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(run_epub_build_consumer())


def stop_epub_build_worker() -> None:
    """Stops the EPUB build consumer."""
    global _worker_running, _worker_task
    _worker_running = False
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        _worker_task = None
