import os
import re
import zipfile
import tempfile
import pytest
from html import escape
from fastapi.testclient import TestClient

from app.main import app, lifespan
from app.config import settings
from app.db.session import db_session
from app.infrastructure.storage.facade import storage_repo
from app.modules.library.application.epub_zip_patcher import EpubZipPatcher
from app.modules.library.application.epub_export_service import EpubBuildCancelledException
from app.modules.library.application.facade import library_service
from app.modules.library.persistence.legacy_models import NovelModel, ChapterModel, EpubBuildJobModel
from app.modules.library.persistence.legacy_repository import LibraryRepository
from app.schemas.library import NovelCreateRequest, ChapterStatus

client = TestClient(app)


def _create_sample_base_epub(file_path: str, chapter_count: int = 3, non_standard: bool = False, include_nav: bool = True) -> None:
    """Helper to create a sample EPUB for testing."""
    with zipfile.ZipFile(file_path, "w") as zf:
        # 1. Uncompressed mimetype at offset 0
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)

        # 2. Container XML
        container_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            '  <rootfiles>\n'
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            '  </rootfiles>\n'
            '</container>'
        )
        zf.writestr("META-INF/container.xml", container_xml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)

        # 3. Chapters & Nav
        manifest_items = []
        spine_items = []

        if include_nav:
            nav_content = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
                '<head><title>Navigation</title></head>\n'
                '<body><nav epub:type="toc"><ol><li><a href="ch_0001.xhtml">Chương 1</a></li></ol></nav></body>\n'
                '</html>'
            )
            zf.writestr("OEBPS/nav.xhtml", nav_content.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
            manifest_items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

        for i in range(1, chapter_count + 1):
            fname = f"part_{i}.html" if non_standard else f"ch_{i:04d}.xhtml"
            ch_content = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="vi">\n'
                f'<head><title>Chương {i}</title></head>\n'
                f'<body><h1>Chương {i}</h1><p>Nội dung gốc chương {i}</p></body>\n'
                '</html>'
            )
            zf.writestr(f"OEBPS/{fname}", ch_content.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
            manifest_items.append(f'<item id="ch_{i:04d}" href="{fname}" media-type="application/xhtml+xml"/>')
            spine_items.append(f'<itemref idref="ch_{i:04d}"/>')

        # 4. OPF
        opf = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            '    <dc:identifier id="pub-id">test-novel</dc:identifier>\n'
            '    <dc:title>Test Novel</dc:title>\n'
            '    <dc:language>vi</dc:language>\n'
            '  </metadata>\n'
            f'  <manifest>\n    {"".join(manifest_items)}\n  </manifest>\n'
            f'  <spine>\n    {"".join(spine_items)}\n  </spine>\n'
            '</package>'
        )
        zf.writestr("OEBPS/content.opf", opf.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)


@pytest.mark.asyncio
async def test_app_lifespan_smoke():
    """Verify lifespan starts and shuts down cleanly without NameError."""
    async with lifespan(app):
        assert app is not None


def test_epub_zip_patcher_streaming_and_ocf():
    """Verify EpubZipPatcher updates dirty chapters and strictly enforces EPUB OCF rules."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as src_f, \
         tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as dst_f:
        src_path = src_f.name
        dst_path = dst_f.name

    try:
        _create_sample_base_epub(src_path, chapter_count=3, include_nav=True)

        # Verify layout standardization ignores nav.xhtml
        assert EpubZipPatcher.is_layout_standardized(src_path) is True

        # Patch only Chapter 2
        payloads = {
            2: ("Chương 2: Đột Phá Mới", "Nội dung bản dịch siêu tốc chương 2.\nĐoạn văn thứ hai.")
        }
        patched_count = EpubZipPatcher.patch_epub_streaming(src_path, dst_path, payloads)
        assert patched_count == 1

        # Validate OCF specifications
        with zipfile.ZipFile(dst_path, "r") as zf:
            infolist = zf.infolist()
            # 1. Entry 0 is mimetype and uncompressed
            assert infolist[0].filename == "mimetype"
            assert infolist[0].compress_type == zipfile.ZIP_STORED
            assert getattr(infolist[0], "header_offset", 0) == 0
            assert zf.read("mimetype") == b"application/epub+zip"

            # 2. Chapter 2 content is updated
            ch2_content = zf.read("OEBPS/ch_0002.xhtml").decode("utf-8")
            assert "Chương 2: Đột Phá Mới" in ch2_content
            assert "Nội dung bản dịch siêu tốc chương 2." in ch2_content
            assert "<p>Đoạn văn thứ hai.</p>" in ch2_content

            # 3. Chapter 1 & 3 remain unchanged
            ch1_content = zf.read("OEBPS/ch_0001.xhtml").decode("utf-8")
            assert "Nội dung gốc chương 1" in ch1_content

            # 4. Integrity check passes
            is_valid, err = EpubZipPatcher.verify_epub_archive(dst_path)
            assert is_valid is True
            assert err is None
    finally:
        for p in (src_path, dst_path):
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


def test_epub_layout_standardization_check():
    """Verify is_layout_standardized correctly detects non-standard layouts."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".epub") as non_std_f:
        non_std_path = non_std_f.name

    try:
        _create_sample_base_epub(non_std_path, chapter_count=2, non_standard=True)
        assert EpubZipPatcher.is_layout_standardized(non_std_path) is False
    finally:
        if os.path.exists(non_std_path):
            try:
                os.unlink(non_std_path)
            except Exception:
                pass


def test_same_chapter_race_revision_preservation():
    """
    Verify that if Chapter 10 is modified to rev 44 while worker is building Chapter 10 at rev 43,
    completing rev 43 retains Chapter 10 in the dirty set and triggers rev 44.
    """
    test_novel_id = "test-same-ch-race"
    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id.like("test-%")).delete()
        novel = session.get(NovelModel, test_novel_id)

        if not novel:
            novel = NovelModel(
                novel_id=test_novel_id,
                title="Test Same Chapter Race Novel",
                status="ongoing",
            )
            session.add(novel)
        novel.dirty_chapters = {"10": 43}
        novel.desired_revision = 43
        novel.built_revision = 42
        session.commit()

        # 1. Enqueue job for rev 43 (dirty chapter 10)
        job1 = LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            dirty_indexes=[10],
            is_structural=False,
        )
        session.commit()

        # 2. Worker claims job1 (snapshot claimed_dirty_chapters = {"10": 43})
        claimed = LibraryRepository.claim_next_job(session=session, worker_id="test-worker")
        session.commit()
        assert claimed is not None
        assert claimed.novel_id == test_novel_id

        # 3. User translates Chapter 10 AGAIN while worker is building (triggering mark_dirty_and_enqueue_job)
        LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            dirty_indexes=[10],
            is_structural=False,
        )
        session.commit()

        # 4. Worker finishes building revision 43
        success = LibraryRepository.complete_job(
            session=session,
            job_id=claimed.job_id,
            built_revision=43,
            epub_key=f"novels/{test_novel_id}/exports/r43.epub",
            worker_id="test-worker",
        )
        session.commit()
        assert success is True

        # Verify: Chapter 10 is NOT lost because rev > claimed rev!
        updated_novel = session.get(NovelModel, test_novel_id)
        assert updated_novel.built_revision == 43
        assert "10" in updated_novel.dirty_chapters



        # Verify next job for chapter 10 was automatically enqueued
        next_job = session.query(EpubBuildJobModel).filter(
            EpubBuildJobModel.novel_id == test_novel_id,
            EpubBuildJobModel.status == "queued",
        ).first()
        assert next_job is not None
        assert 10 in LibraryRepository._normalize_dirty_chapters(next_job.dirty_chapters)


def test_global_advisory_lock():
    """Verify PostgreSQL advisory lock acquisition and release."""
    with db_session() as session:
        has_lock = LibraryRepository.acquire_global_build_lock(session)
        assert has_lock is True
        released = LibraryRepository.release_global_build_lock(session)
        assert released is True


def test_api_epub_build_endpoints_and_job_id_lookup():
    """Verify POST /epub-builds (202) and GET /epub-builds/{job_id}."""
    test_novel_id = "test-api-build-novel"
    existing = library_service.get_novel(test_novel_id)
    if not existing:
        library_service.create_novel(
            NovelCreateRequest(
                title="Test API Build Novel",
                novel_id=test_novel_id,
            )
        )

    # 1. Trigger build via POST (202 Accepted)
    resp = client.post(
        f"/api/v1/library/novels/{test_novel_id}/epub-builds",
        json={"target_chapters": "1-3", "force_rebuild": False},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["novel_id"] == test_novel_id
    assert "job_id" in data
    job_id = data["job_id"]

    # 2. Check status via GET /novels/{novel_id}/epub-builds/{job_id}
    job_resp = client.get(f"/api/v1/library/novels/{test_novel_id}/epub-builds/{job_id}")
    assert job_resp.status_code == 200
    job_data = job_resp.json()
    assert job_data["job_id"] == job_id
    assert job_data["novel_id"] == test_novel_id

    # 3. Check status via GET /novels/{novel_id}/epub-builds/status
    st_resp = client.get(f"/api/v1/library/novels/{test_novel_id}/epub-builds/status")
    assert st_resp.status_code == 200
    st_data = st_resp.json()
    assert st_data["novel_id"] == test_novel_id


def test_ghost_novel_auto_sync_to_postgres(monkeypatch):
    """Verify that a novel existing only in legacy storage JSON is automatically upserted into DB on get_novel."""
    import json
    monkeypatch.setattr(settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(settings, "structured_storage_read_source", "postgres")

    ghost_id = "test-ghost-novel-sync"
    library_service._cache.pop(ghost_id, None)

    # Create in storage JSON
    raw_meta = {
        "novel_id": ghost_id,
        "title": "Ghost Novel In JSON",
        "status": "ongoing",
        "total_chapters": 1,
        "translated_chapters": 0,
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:00:00Z",
        "chapters": [],
    }
    storage_repo.put_bytes(
        f"novels/{ghost_id}/metadata.json",
        json.dumps(raw_meta, ensure_ascii=False, indent=2).encode("utf-8"),
    )

    # Ensure removed from DB
    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id == ghost_id).delete()
        novel_row = session.get(NovelModel, ghost_id)
        if novel_row:
            session.delete(novel_row)
            session.commit()

    # Call get_novel
    meta = library_service.get_novel(ghost_id)
    assert meta is not None
    assert meta.novel_id == ghost_id
    assert meta.title == "Ghost Novel In JSON"

    # Verify auto-synced into DB
    with db_session() as session:
        db_novel = session.get(NovelModel, ghost_id)
        assert db_novel is not None
        assert db_novel.title == "Ghost Novel In JSON"




def test_lease_loss_guards_worker_completion():
    """Verify that a worker whose lease token was revoked cannot complete a job."""
    test_novel_id = "test-lease-guard-novel"
    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id.like("test-%")).delete()
        novel = session.get(NovelModel, test_novel_id)

        if not novel:
            novel = NovelModel(novel_id=test_novel_id, title="Test Lease Guard Novel", status="ongoing")
            session.add(novel)
        session.commit()

        # Enqueue job
        job = LibraryRepository.mark_dirty_and_enqueue_job(session=session, novel_id=test_novel_id, dirty_indexes=[1])
        session.commit()

        # Claim by worker-1
        claimed = LibraryRepository.claim_next_job(session=session, worker_id="worker-1")
        session.commit()
        assert claimed is not None

        # Simulate lease reassigned to worker-2
        claimed.lease_token = "worker-2"
        session.commit()

        # Worker-1 attempts to complete job -> must be rejected
        comp_result = LibraryRepository.complete_job(
            session=session,
            job_id=claimed.job_id,
            built_revision=1,
            epub_key=f"novels/{test_novel_id}/exports/r1.epub",
            worker_id="worker-1",
        )
        assert comp_result is False


def test_legacy_full_epub_enables_fast_patch_without_current_key():
    """Verify that a novel with null current_epub_key uses fast_patch if full.epub exists on storage."""
    test_novel_id = "test-legacy-epub-strategy"
    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id.like("test-%")).delete()
        novel = session.get(NovelModel, test_novel_id)
        if not novel:
            novel = NovelModel(
                novel_id=test_novel_id,
                title="Test Legacy Strategy Novel",
                status="ongoing",
            )
            session.add(novel)
        novel.current_epub_key = None
        novel.is_structural_dirty = False
        session.commit()

        # Mock existence of novels/{id}/full.epub
        legacy_key = f"novels/{test_novel_id}/full.epub"
        storage_repo.put_bytes(legacy_key, b"dummy epub content")

        try:
            # Enqueue a content-only update for Chapter 151
            job_resp = LibraryRepository.mark_dirty_and_enqueue_job(
                session=session,
                novel_id=test_novel_id,
                dirty_indexes=[151],
                is_structural=False,
                force_rebuild=False,
            )
            session.commit()

            # Strategy MUST be fast_patch, NOT full_rebuild!
            assert job_resp.strategy == "fast_patch"
            assert job_resp.is_structural is False
            assert 151 in LibraryRepository._normalize_dirty_chapters(job_resp.dirty_chapters)

            # Novel should automatically have its current_epub_key initialized
            updated_novel = session.get(NovelModel, test_novel_id)
            assert updated_novel.current_epub_key == legacy_key
        finally:
            storage_repo.delete_file(legacy_key)


def test_epub_build_job_progress_reporting():
    """Verify that job progress is updated with step description and chapter index."""
    test_novel_id = "test-progress-novel"
    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id == test_novel_id).delete()
        novel = session.get(NovelModel, test_novel_id)
        if not novel:
            novel = NovelModel(
                novel_id=test_novel_id,
                title="Test Progress Novel",
                status="ongoing",
            )
            session.add(novel)
        session.commit()

        job_resp = LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            dirty_indexes=[151],
            is_structural=False,
            force_rebuild=False,
        )
        session.commit()

        # Update progress
        update_ok = LibraryRepository.update_job_progress(
            session=session,
            job_id=job_resp.job_id,
            current_step="Đang tải nội dung Chương 151: Sơn thôn thiếu niên...",
            current_chapter=151,
            processed_chapters=1,
            total_chapters=1,
            progress_percentage=75,
        )
        session.commit()
        assert update_ok is True

        # Fetch and verify
        fetched = LibraryRepository.get_epub_build_job_by_id(session, novel_id=test_novel_id, job_id=job_resp.job_id)
        assert fetched is not None
        assert fetched.current_step == "Đang tải nội dung Chương 151: Sơn thôn thiếu niên..."
        assert fetched.current_chapter == 151
        assert fetched.processed_chapters == 1
        assert fetched.total_chapters == 1
        assert fetched.progress_percentage == 75


def test_epub_build_job_cancel_api():
    """Verify cancel endpoint cancels a queued/processing build job."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    test_novel_id = "test-cancel-novel"

    existing = library_service.get_novel(test_novel_id)
    if not existing:
        library_service.create_novel(
            NovelCreateRequest(
                title="Test Cancel Novel",
                novel_id=test_novel_id,
            )
        )

    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id == test_novel_id).delete()
        session.commit()

        job_resp = LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            dirty_indexes=[151],
            is_structural=False,
            force_rebuild=False,
        )
        session.commit()

    # Call Cancel API
    res = client.post(f"/api/v1/library/novels/{test_novel_id}/epub-builds/{job_resp.job_id}/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cancelled"
    assert "hủy" in data["error_message"].lower()

    # Verify repository status
    with db_session() as session:
        assert LibraryRepository.is_job_cancelled(session, job_resp.job_id) is True


def test_export_full_epub_cancellation_and_regex(tmp_path):
    """Verify that export_full_epub respects is_cancelled_callback and EpubZipPatcher supports flexible chapter naming."""
    import re
    import pytest
    from app.modules.library.application.epub_zip_patcher import EpubZipPatcher
    from app.modules.library.application.epub_export_service import EpubBuildCancelledException

    # 1. Verify flexible regex
    pattern = r"^(?:ch|chapter)_?0*(\d+)\.(?:xhtml|html)$"
    assert re.match(pattern, "ch_0151.xhtml", re.IGNORECASE)
    assert re.match(pattern, "ch_151.xhtml", re.IGNORECASE)
    assert re.match(pattern, "chapter_151.xhtml", re.IGNORECASE)
    assert re.match(pattern, "chapter_0001.html", re.IGNORECASE)

    # 2. Verify export_full_epub aborts immediately on cancellation
    test_novel_id = "test-cancel-abort-novel"
    existing = library_service.get_novel(test_novel_id)
    if not existing:
        library_service.create_novel(
            NovelCreateRequest(
                title="Test Cancel Abort Novel",
                novel_id=test_novel_id,
            )
        )

    with pytest.raises(EpubBuildCancelledException):
        library_service.export_full_epub(
            novel_id=test_novel_id,
            is_cancelled_callback=lambda: True,
        )


def test_fallback_full_rebuild_publishes_all_chapters():
    """Verify that when fast patch is not applicable, fallback FULL_REBUILD generates ALL novel chapters."""
    test_novel_id = "test-fallback-all-chapters-novel"
    existing = library_service.get_novel(test_novel_id)
    if not existing:
        library_service.create_novel(
            NovelCreateRequest(
                title="Test Fallback All Chapters",
                novel_id=test_novel_id,
            )
        )
    for i in range(1, 4):
        library_service.add_or_update_chapter(
            novel_id=test_novel_id,
            chapter_index=i,
            chapter_title=f"Chương {i}",
            content=f"Nội dung chương {i}",
        )

    # Make sure no base EPUB exists
    with db_session() as session:
        nov = LibraryRepository.get_novel(session, test_novel_id)
        if nov:
            nov.current_epub_key = None
            session.commit()

    # Request scoped range build for chapter 2 only
    result = library_service.export_service.build_and_publish_epub(
        novel_id=test_novel_id,
        force_rebuild=False,
        dirty_chapters=[2],
        target_chapters="2",
    )
    assert result["strategy"] == "full_rebuild"

    # Verify that the generated EPUB contains ALL 3 chapters
    out_path = result["output_path"]
    assert os.path.exists(out_path)
    with zipfile.ZipFile(out_path, "r") as zf:
        names = zf.namelist()
        chapter_entries = [n for n in names if re.search(r"ch_\d+\.xhtml", n)]
        assert len(chapter_entries) == 3


def test_job_coalesce_no_downgrade_structural():
    """Verify that coalescing a scoped range request does NOT downgrade an existing structural/full_rebuild job."""
    test_novel_id = "test-coalesce-no-downgrade"
    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id == test_novel_id).delete()
        novel = session.get(NovelModel, test_novel_id)
        if not novel:
            novel = NovelModel(
                novel_id=test_novel_id,
                title="Test Coalesce No Downgrade",
                status="ongoing",
            )
            session.add(novel)
        session.commit()

        # 1. Enqueue structural full rebuild
        job1 = LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            is_structural=True,
            force_rebuild=True,
        )
        session.commit()
        assert job1.is_structural is True
        assert job1.strategy == "full_rebuild"

        # 2. Coalesce scoped range request
        job2 = LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            dirty_indexes=[151],
            is_structural=False,
            force_rebuild=False,
        )
        session.commit()
        # Must retain full_rebuild and is_structural=True!
        assert job2.is_structural is True
        assert job2.strategy == "full_rebuild"


def test_new_scoped_job_is_not_structural_even_if_novel_structural_dirty(tmp_path):
    """Verify that when novel.is_structural_dirty is True, a new scoped range job has is_structural=False and strategy=fast_patch."""
    test_novel_id = "test-scoped-dirty-struct"

    # Create dummy base EPUB on storage
    base_file = str(tmp_path / "base.epub")
    _create_sample_base_epub(base_file, chapter_count=2)
    storage_repo.upload_file_stream(base_file, f"novels/{test_novel_id}/full.epub")

    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id == test_novel_id).delete()
        nov = session.get(NovelModel, test_novel_id)
        if not nov:
            nov = NovelModel(
                novel_id=test_novel_id,
                title="Test Scoped Dirty Struct",
                status="ongoing",
            )
            session.add(nov)
        nov.is_structural_dirty = True
        nov.current_epub_key = f"novels/{test_novel_id}/full.epub"
        session.commit()

        job = LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            dirty_indexes=[1],
            is_structural=False,
            force_rebuild=False,
        )
        session.commit()

        assert job.is_structural is False
        assert job.strategy == "fast_patch"


def test_scoped_job_without_base_keeps_full_rebuild_structural_flag():
    """A scoped request still needs a structural full rebuild when no base EPUB exists."""
    test_novel_id = "test-scoped-dirty-no-base"

    with db_session() as session:
        session.query(EpubBuildJobModel).filter(EpubBuildJobModel.novel_id == test_novel_id).delete()
        novel = session.get(NovelModel, test_novel_id)
        if not novel:
            novel = NovelModel(
                novel_id=test_novel_id,
                title="Test Scoped Dirty Without Base",
                status="ongoing",
            )
            session.add(novel)
        novel.is_structural_dirty = True
        novel.current_epub_key = None
        session.commit()

        job = LibraryRepository.mark_dirty_and_enqueue_job(
            session=session,
            novel_id=test_novel_id,
            dirty_indexes=[1],
            is_structural=False,
            force_rebuild=False,
        )
        session.commit()

        assert job.is_structural is True
        assert job.strategy == "full_rebuild"


def test_missing_target_chapter_in_base_triggers_error(tmp_path):
    """Verify that EpubZipPatcher raises ValueError when target chapter is missing in base EPUB."""
    base_file = str(tmp_path / "base_missing.epub")
    out_file = str(tmp_path / "out.epub")
    _create_sample_base_epub(base_file, chapter_count=2)

    # Try to patch chapter 5 (which does not exist in base EPUB with only chapters 1 and 2)
    with pytest.raises(ValueError, match=r"Target chapters \[5\] not found in base EPUB"):
        EpubZipPatcher.patch_epub_streaming(
            base_epub_path=base_file,
            output_epub_path=out_file,
            chapter_payloads={5: ("Chương 5", "Nội dung chương 5")},
        )


def test_fast_patch_build_success_with_cancelled_callback(tmp_path):
    """Verify that fast patch succeeds without fallback when is_cancelled_callback is passed."""
    test_novel_id = "test-fast-patch-success"
    existing = library_service.get_novel(test_novel_id)
    if not existing:
        library_service.create_novel(
            NovelCreateRequest(
                title="Test Fast Patch Success",
                novel_id=test_novel_id,
            )
        )
    for i in range(1, 3):
        library_service.add_or_update_chapter(
            novel_id=test_novel_id,
            chapter_index=i,
            chapter_title=f"Chương {i}",
            content=f"Nội dung chương {i}",
        )
    library_service.apply_chapter_translation(
        novel_id=test_novel_id,
        chapter_index=2,
        content="Bản dịch chương 2 đã được cập nhật",
    )

    # Create valid standardized base EPUB on storage
    base_file = str(tmp_path / "base_standard.epub")
    _create_sample_base_epub(base_file, chapter_count=2, non_standard=False)
    storage_repo.upload_file_stream(base_file, f"novels/{test_novel_id}/full.epub")

    with db_session() as session:
        nov = session.get(NovelModel, test_novel_id)
        if not nov:
            nov = NovelModel(
                novel_id=test_novel_id,
                title="Test Fast Patch Success",
                status="ongoing",
            )
            session.add(nov)
        nov.current_epub_key = f"novels/{test_novel_id}/full.epub"
        nov.is_structural_dirty = False
        nov.built_revision = 1
        session.commit()

    # Build only dirty chapter 2
    result = library_service.export_service.build_and_publish_epub(
        novel_id=test_novel_id,
        force_rebuild=False,
        dirty_chapters=[2],
        target_chapters="2",
        is_cancelled_callback=lambda: False,
    )

    assert result["strategy"] == "fast_patch"
    assert result["patched_chapters_count"] == 1
    assert result["built_revision"] >= 1
    assert storage_repo.file_exists(result["epub_key"]) is True


def test_cancellation_cleans_up_local_and_cloud_artifacts():
    """Verify that if build is cancelled after upload, the cloud artifact is deleted and not left orphaned."""
    test_novel_id = "test-cancel-cleanup-novel"
    existing = library_service.get_novel(test_novel_id)
    if not existing:
        library_service.create_novel(
            NovelCreateRequest(
                title="Test Cancel Cleanup Novel",
                novel_id=test_novel_id,
            )
        )
    library_service.add_or_update_chapter(
        novel_id=test_novel_id,
        chapter_index=1,
        chapter_title="Chương 1",
        content="Nội dung chương 1",
    )

    with db_session() as session:
        nov = session.get(NovelModel, test_novel_id)
        if not nov:
            nov = NovelModel(
                novel_id=test_novel_id,
                title="Test Cancel Cleanup Novel",
                status="ongoing",
            )
            session.add(nov)
        nov.built_revision = 0
        session.commit()

    prefix = f"novels/{test_novel_id}/exports/"
    artifacts_before = set(storage_repo.list_files(prefix))

    # Cancel triggered on callback after progress hits 90% (after upload step)
    cancel_flag = {"cancelled": False}

    def _chk():
        return cancel_flag["cancelled"]

    def _prog(step, ch_idx, processed, total, pct):
        if pct >= 90:
            cancel_flag["cancelled"] = True

    with pytest.raises(EpubBuildCancelledException):
        library_service.export_service.build_and_publish_epub(
            novel_id=test_novel_id,
            force_rebuild=True,
            progress_callback=_prog,
            is_cancelled_callback=_chk,
        )

    # Any artifact created by this cancelled build must be removed; older test data is allowed.
    artifacts_after = set(storage_repo.list_files(prefix))
    assert artifacts_after - artifacts_before == set()
