from pathlib import Path

import pytest

from app.config import settings
from app.core.storage import storage_repo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_ROOT = PROJECT_ROOT / "storage"


def _snapshot_tree(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


def _remove_new_tree_entries(root: Path, initial_paths: set[str], existed_before: bool) -> None:
    """Remove only entries created after the current pytest session started."""
    if not root.exists():
        return
    current_paths = sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True)
    for path in current_paths:
        relative = path.relative_to(root).as_posix()
        if relative in initial_paths:
            continue
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except OSError:
            # A directory containing a pre-existing entry is intentionally kept.
            pass
    # Disposable roots should not linger as empty folders after a green run.
    if root.is_dir() and (not existed_before or root.name in {"test", "outputs", "data"}):
        try:
            root.rmdir()
        except OSError:
            pass


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_storage_after_success(request):
    """Clean only storage entries created by a successful test session."""
    tracked_roots = (
        STORAGE_ROOT / "novels",
        STORAGE_ROOT / "test",
        STORAGE_ROOT / "outputs",
        STORAGE_ROOT / "data",
    )
    snapshots = {
        root: (root.is_dir(), _snapshot_tree(root))
        for root in tracked_roots
    }

    yield

    if getattr(request.session, "testsfailed", 0) == 0:
        for root, (existed_before, initial_paths) in snapshots.items():
            _remove_new_tree_entries(root, initial_paths, existed_before)


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    orig_r2_enabled = storage_repo.r2_enabled
    orig_r2_client = storage_repo.r2_client

    storage_repo.r2_enabled = False
    storage_repo.r2_client = None

    yield

    storage_repo.r2_enabled = orig_r2_enabled
    storage_repo.r2_client = orig_r2_client