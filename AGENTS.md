# Project Instructions

## Test storage isolation (mandatory)

- Never overwrite, modify, or delete pre-existing user story data under `storage/novels`, `storage/uploads`, or `data/local_db.sqlite3` while running tests.
- Never overwrite or silently reset existing test data either; start each test with an isolated copy or a unique, session-owned path.
- Use `tmp_path`/temporary directories and unique test IDs for test writes; do not reuse fixed paths or IDs that may point to real story data.
- Treat existing files as read-only test fixtures. If a test needs to mutate a story, copy it to an isolated temporary location first.
- Keep the session cleanup fixture in `tests/conftest.py` enabled. It removes only entries created during the current pytest session after a fully successful run; artifacts from failed runs are retained for debugging.
- Cleanup must never recursively remove broad storage roots or `storage/uploads`; inspect exact targets before any manual cleanup.
- After tests, verify that pre-existing story directories and files are unchanged. Only session-created test artifacts may be removed.
