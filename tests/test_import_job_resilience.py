from contextlib import nullcontext

from app.modules.library import legacy_service as legacy_module
from app.modules.library.legacy_service import LegacyLibraryService
from app.schemas.library import ImportJobStatus


def test_terminal_memory_job_wins_over_stale_processing_db_job(monkeypatch):
    service = LegacyLibraryService()
    memory_job = ImportJobStatus(
        job_id="import-terminal",
        status="completed",
        progress_percentage=100,
    )
    stale_db_job = ImportJobStatus(
        job_id="import-terminal",
        status="processing",
        progress_percentage=87,
    )
    service._import_jobs[memory_job.job_id] = memory_job

    monkeypatch.setattr(legacy_module.settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(legacy_module.settings, "structured_storage_read_source", "postgres")
    monkeypatch.setattr(legacy_module, "db_session", lambda: nullcontext(object()))
    monkeypatch.setattr(
        legacy_module.LibraryRepository,
        "get_import_job",
        staticmethod(lambda session, job_id: stale_db_job),
    )

    result = service.get_import_job(memory_job.job_id)

    assert result is memory_job
    assert result.status == "completed"
    assert result.progress_percentage == 100


def test_running_job_uses_higher_progress_between_memory_and_db(monkeypatch):
    service = LegacyLibraryService()
    memory_job = ImportJobStatus(
        job_id="import-progress",
        status="processing",
        progress_percentage=30,
    )
    db_job = ImportJobStatus(
        job_id="import-progress",
        status="processing",
        progress_percentage=60,
    )
    service._import_jobs[memory_job.job_id] = memory_job

    monkeypatch.setattr(legacy_module.settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(legacy_module.settings, "structured_storage_read_source", "postgres")
    monkeypatch.setattr(legacy_module, "db_session", lambda: nullcontext(object()))
    monkeypatch.setattr(
        legacy_module.LibraryRepository,
        "get_import_job",
        staticmethod(lambda session, job_id: db_job),
    )

    result = service.get_import_job(memory_job.job_id)

    assert result is db_job
    assert service._import_jobs[memory_job.job_id] is db_job


def test_terminal_import_job_persistence_retries_transient_db_failures(monkeypatch):
    service = LegacyLibraryService()
    job = ImportJobStatus(
        job_id="import-retry",
        status="completed",
        progress_percentage=100,
    )
    attempts = []
    sleeps = []

    class FakeSession:
        def commit(self):
            return None

    def save_import_job(session, value):
        attempts.append(value.status)
        if len(attempts) < 3:
            raise RuntimeError("temporary database outage")

    monkeypatch.setattr(legacy_module.settings, "structured_storage_backend", "postgres")
    monkeypatch.setattr(legacy_module, "db_session", lambda: nullcontext(FakeSession()))
    monkeypatch.setattr(
        legacy_module.LibraryRepository,
        "save_import_job",
        staticmethod(save_import_job),
    )
    monkeypatch.setattr(legacy_module.time, "sleep", sleeps.append)

    service._persist_import_job(job)

    assert attempts == ["completed", "completed", "completed"]
    assert len(sleeps) == 2
    assert service._import_jobs[job.job_id] is job
