import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.base import Base
from app.db.models.library import ChapterModel, NovelModel, EpubBuildJobModel
from app.db.session import create_db_engine, db_session, reset_db_engine
from app.main import app


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    test_db = tmp_path / "concurrency_test.sqlite3"
    db_url = f"sqlite:///{test_db.as_posix()}"
    orig_db_url = settings.database_url
    orig_auth = settings.auth_required

    monkeypatch.setattr(settings, "database_url", db_url)
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(settings, "structured_storage_read_source", "postgres")

    engine = reset_db_engine(db_url)
    Base.metadata.create_all(engine)

    yield engine

    try:
        engine.dispose()
    except Exception:
        pass
    reset_db_engine(orig_db_url)
    monkeypatch.setattr(settings, "database_url", orig_db_url)
    monkeypatch.setattr(settings, "auth_required", orig_auth)


def test_new_request_while_processing_creates_queued_followup(isolated_db):
    """
    Xác minh khi đã có một job đang ở trạng thái 'processing':
    Request build mới (với dải chương mới) không bị nuốt chửng (P1.2).
    Hệ thống ghi nhận dirty_chapters vào DB và tạo một follow-up job ở trạng thái 'queued'.
    """
    novel_id = "test-concurrent-novel"

    # 1. Tạo novel và 1 job đang PROCESSING
    with db_session() as session:
        novel = NovelModel(
            novel_id=novel_id,
            title="Đại Phụng Đả Canh Nhân",
            original_title="Da Feng Da Geng Ren",
            author="Mại Báo Tiểu Lang Quân",
            desired_revision=1,
            built_revision=0,
            dirty_chapters={"1": 1, "2": 1},
        )
        session.add(novel)

        active_job = EpubBuildJobModel(
            job_id="job-currently-processing",
            novel_id=novel_id,
            status="processing",
            strategy="fast_patch",
            target_revision=1,
            dirty_chapters={"1": 1, "2": 1},
            current_step="Đang vá chương 1...",
            progress_percentage=40,
            processed_chapters=1,
            total_chapters=2,
        )
        session.add(active_job)
        session.commit()

    # 2. Client gửi request build mới với target_chapters="5-6"
    client = TestClient(app)
    response = client.post(
        f"/api/v1/library/novels/{novel_id}/epub-builds",
        json={"target_chapters": "5-6", "force_rebuild": False},
    )
    assert response.status_code == 202
    res_data = response.json()

    # Phải trả về một queued job mới, không phải trả về processing job cũ!
    assert res_data["status"] == "queued"
    assert res_data["job_id"] != "job-currently-processing"
    assert 5 in res_data["dirty_chapters"]
    assert 6 in res_data["dirty_chapters"]

    # 3. Kiểm tra trong cơ sở dữ liệu
    with db_session() as session:
        # Job cũ vẫn đang processing
        old_job = session.query(EpubBuildJobModel).filter_by(job_id="job-currently-processing").first()
        assert old_job is not None
        assert old_job.status == "processing"

        # Job mới ở trạng thái queued
        new_job = session.query(EpubBuildJobModel).filter_by(job_id=res_data["job_id"]).first()
        assert new_job is not None
        assert new_job.status == "queued"
        assert "5" in new_job.dirty_chapters
        assert "6" in new_job.dirty_chapters

        # Novel desired_revision được tăng và dirty_chapters được gộp đầy đủ
        updated_novel = session.query(NovelModel).filter_by(novel_id=novel_id).first()
        assert updated_novel.desired_revision == 2
        assert "5" in updated_novel.dirty_chapters
        assert "6" in updated_novel.dirty_chapters


def test_excessive_target_chapters_rejected_with_400(isolated_db):
    """
    Xác minh dải chương quá lớn (nguy cơ DoS / cạn kiệt RAM)
    bị từ chối ngay lập tức với HTTP 400 mà không gây crash server.
    """
    novel_id = "test-dos-novel"

    with db_session() as session:
        novel = NovelModel(
            novel_id=novel_id,
            title="Đại Phụng Đả Canh Nhân",
            original_title="Da Feng Da Geng Ren",
            author="Mại Báo Tiểu Lang Quân",
            total_chapters=100,
        )
        session.add(novel)
        session.commit()

    client = TestClient(app)

    # 1. Dải chương quá lớn (1 đến 100,000,000)
    res_large = client.post(
        f"/api/v1/library/novels/{novel_id}/epub-builds",
        json={"target_chapters": "1-100000000"},
    )
    assert res_large.status_code == 400
    assert "vượt quá giới hạn" in res_large.json()["detail"]

    # 2. Số chương đơn lẻ quá lớn
    res_single = client.post(
        f"/api/v1/library/novels/{novel_id}/epub-builds",
        json={"target_chapters": "9999999"},
    )
    assert res_single.status_code == 400
    assert "vượt quá giới hạn" in res_single.json()["detail"]

