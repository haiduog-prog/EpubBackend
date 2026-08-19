import pytest
from app.core.storage import storage_repo
from app.config import settings

@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    orig_r2_enabled = storage_repo.r2_enabled
    orig_r2_client = storage_repo.r2_client

    storage_repo.r2_enabled = False
    storage_repo.r2_client = None

    yield

    storage_repo.r2_enabled = orig_r2_enabled
    storage_repo.r2_client = orig_r2_client
