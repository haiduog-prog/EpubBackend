"""Session-wide isolation for tests.

Settings, the database engine and the storage facade are initialized at
module-import time, so the temporary roots must be selected first.
"""

import hashlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.mkdtemp(prefix="epub-backend-pytest-"))
TEST_STORAGE_ROOT = TEST_ROOT / "storage"
TEST_DATABASE = TEST_ROOT / "data" / "local_db.sqlite3"

# These assignments intentionally override accidental .env values in tests.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["STORAGE_PROVIDER"] = "local"
os.environ["LOCAL_STORAGE_ROOT"] = str(TEST_STORAGE_ROOT)
os.environ["STRUCTURED_STORAGE_BACKEND"] = "dual"
os.environ["STRUCTURED_STORAGE_READ_SOURCE"] = "legacy"
os.environ["GOOGLE_DRIVE_SYNC_ENABLED"] = "false"

import pytest

from app.db.base import Base
import app.db.session as db_session_module
from app.db.session import engine
from app.config import settings

# Register every ORM model before collection imports app.main (which hydrates
# the character-profile service at import time).
from app.db.models import jobs, reader  # noqa: F401,E402
from app.modules.book_bible.persistence import legacy_models as book_bible_models  # noqa: F401,E402
from app.modules.character_profiles.persistence import legacy_models as profile_models  # noqa: F401,E402
from app.modules.library.persistence import legacy_models as library_models  # noqa: F401,E402
from app.core.storage import storage_repo

TEST_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)


STORAGE_ROOT = TEST_STORAGE_ROOT


def _snapshot_files(*roots: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for root in roots:
        if root.is_file():
            paths = [root]
        elif root.is_dir():
            paths = [p for p in root.rglob("*") if p.is_file()]
        else:
            paths = []
        for path in paths:
            result[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


ORIGINAL_DATA_SNAPSHOT = _snapshot_files(
    PROJECT_ROOT / "storage" / "novels",
    PROJECT_ROOT / "storage" / "uploads",
    PROJECT_ROOT / "data" / "local_db.sqlite3",
)


@pytest.fixture(scope="session", autouse=True)
def isolated_database_and_storage(request):
    """Create schema in the unique session DB and clean only that root."""

    TEST_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    yield

    assert _snapshot_files(
        PROJECT_ROOT / "storage" / "novels",
        PROJECT_ROOT / "storage" / "uploads",
        PROJECT_ROOT / "data" / "local_db.sqlite3",
    ) == ORIGINAL_DATA_SNAPSHOT, "Tests modified pre-existing production data"

    keep_on_failure = os.getenv("KEEP_TEST_ARTIFACTS_ON_FAILURE", "false").lower() in {"1", "true", "yes"}
    if not keep_on_failure or getattr(request.session, "testsfailed", 0) == 0:
        engine.dispose()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    # Some legacy tests deliberately call reset_db_engine() to exercise
    # reconfiguration.  Give every test its own file and re-anchor the
    # process-global session so one test cannot leak a dropped/invalid
    # database into the next test.
    test_token = uuid.uuid4().hex
    test_database = TEST_ROOT / "databases" / f"{test_token}.sqlite3"
    test_storage_root = TEST_ROOT / "storage" / test_token
    test_database.parent.mkdir(parents=True, exist_ok=True)
    test_storage_root.mkdir(parents=True, exist_ok=True)
    test_db_url = f"sqlite:///{test_database.as_posix()}"

    current_engine = db_session_module.engine
    if current_engine is not engine:
        current_engine.dispose()
    db_session_module.reset_db_engine(test_db_url)
    monkeypatch.setattr(settings, "database_url", test_db_url)
    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "local_storage_root", str(test_storage_root))
    monkeypatch.setattr(settings, "structured_storage_backend", "dual")
    monkeypatch.setattr(settings, "structured_storage_read_source", "legacy")

    Base.metadata.create_all(bind=db_session_module.engine)
    original_provider = storage_repo.local_provider
    original_r2_enabled = storage_repo.r2_enabled
    original_r2_client = storage_repo.r2_client

    from app.infrastructure.storage.legacy_storage import LocalStorageProvider

    storage_repo.local_provider = LocalStorageProvider(str(test_storage_root))
    storage_repo.r2_enabled = False
    storage_repo.r2_client = None
    yield

    current_engine = db_session_module.engine
    if current_engine is not engine:
        current_engine.dispose()
    storage_repo.local_provider = original_provider
    storage_repo.r2_enabled = original_r2_enabled
    storage_repo.r2_client = original_r2_client
