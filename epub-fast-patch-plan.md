# EPUB FAST_PATCH Implementation Plan

## Goal

Rebuild an EPUB after content-only chapter changes without fetching, parsing, or recompressing every chapter, while keeping full rebuild as a safe fallback for structural changes.

## Decisions

- FAST_PATCH handles content-only changes; add/delete/reorder chapter, cover, metadata, spine, nav, or TOC changes force FULL_REBUILD.
- PostgreSQL stores exact dirty chapter indexes and the current published EPUB revision; object storage keeps immutable versioned files such as `novels/{id}/exports/r42.epub`.
- FAST_PATCH uses Info-ZIP `zip -u` so unchanged compressed entries are preserved. `ebooklib.write_epub()` remains only in FULL_REBUILD.
- One durable consumer processes one EPUB build at a time on the free Render instance.

## Tasks

- [ ] Add `EpubBuildJobModel` and export fields on `NovelModel` in `app/modules/library/persistence/legacy_models.py`, plus an Alembic migration: status, exact `dirty_chapters`, structural-dirty flag, desired/built revision, current object key, lease, attempts, timing, and error. Add a unique constraint preventing two active jobs for one novel. Verify by upgrading and downgrading a temporary database.
- [ ] Extend `app/modules/library/persistence/legacy_repository.py` with atomic invalidation, job coalescing, lease/claim, completion, retry, and stale-lease recovery operations. Use a database row/advisory lock instead of the current process-local rebuild lock. Verify two concurrent enqueue calls produce one active job containing the union of chapter indexes.
- [ ] Mark EPUB state dirty from chapter publish/update paths in `app/modules/library/legacy_service.py`: content edits add one exact index; add/delete/reorder/import/cover/TOC changes set structural dirty. Verify sparse changes `{1, 500, 1000}` remain exactly those three indexes.
- [ ] Create `app/modules/library/application/epub_zip_patcher.py`: copy the current snapshot to a temporary workspace, generate only dirty `ch_NNNN.xhtml` files, update them with `zip -u`, and lightly validate `mimetype`, central-directory entries, and changed XHTML. Add `zip` to `Dockerfile`, preserve Alembic startup, and switch `render.yaml` to the Docker runtime. Verify unchanged ZIP entries retain their compressed payload/checksum.
- [ ] Expand `app/modules/library/application/epub_export_service.py` into the build orchestrator: choose FAST_PATCH only when the snapshot and canonical chapter map are valid; otherwise call the existing full compiler. Stream base download/upload, publish to a versioned key, and atomically update `current_epub_key` only after successful upload. Verify an upload failure leaves the previous revision active.
- [ ] Add a lifespan-managed EPUB job consumer in `app/main.py` with global concurrency `1`, chapter-fetch concurrency `2`, debounce/coalescing, bounded exponential retry, lease heartbeat, and restart recovery. Verify a simulated process interruption returns the leased job to the queue and never runs two builds concurrently.
- [ ] Replace rebuild side effects in `app/modules/library/api.py` with `POST /novels/{id}/epub-builds` returning `202`, add a build-status endpoint, and make EPUB download redirect to the current immutable object. Update `app/static/index.html` to send exact `target_chapters`, poll progress, and keep the last good EPUB downloadable during a build. Verify the request returns promptly without transferring the generated EPUB through Render.
- [ ] Add temporary-file cleanup, per-stage metrics, and structured logs for strategy, dirty count, storage GET count, bytes, ZIP time, upload time, attempts, and fallback reason. Gate FAST_PATCH behind `EPUB_FAST_PATCH_ENABLED`; retain FULL_REBUILD for rollback. Verify completed and failed jobs leave no files in `storage/outputs` or the temporary workspace.
- [ ] Final verification: add `tests/test_epub_fast_patch.py` and update library/API tests. Test one/sparse/many chapter patches, Unicode and escaping, structural fallback, corrupt/missing base, upload failure, concurrent requests, restart recovery, and output readability with `ebooklib` plus `zip -T`. Benchmark the 2,934-chapter fixture and compare it with the current full rebuild.

## Done When

- [ ] Patching `k` chapters performs one base EPUB read, exactly `k` chapter reads, no all-chapter content scan, and no recompression of unchanged ZIP entries.
- [ ] Rebuild API returns `202` in under one second and never causes concurrent EPUB builds on the free instance.
- [ ] Failed builds preserve the previous downloadable revision and recover after restart.
- [ ] Content-only FAST_PATCH is materially faster than FULL_REBUILD on the 2,934-chapter fixture, and all produced EPUBs pass integrity/readability tests.
