# Personal Web Reader Implementation

## Goal

Deliver a standalone `/reader` experience that exposes only readable Vietnamese
translations, supports comfortable desktop/mobile reading, and preserves local
preferences and per-book progress without changing administration workflows.

## Tasks

- [x] 1. Add `app/modules/reader/schemas.py` and failing contract tests in `tests/test_reader.py` for book summaries, translated-only chapter lists, chapter content, and previous/next references. Verify: `pytest -q tests/test_reader.py` passes the Reader service contract.
- [x] 2. Implement `app/modules/reader/service.py` on top of `library_service`, including readable-chapter filtering, index-gap navigation, missing-content fail-closed behavior, and no original-text fallback. Verify: Reader service tests pass with a fake Library facade.
- [x] 3. Implement `app/modules/reader/api.py`, add the compatibility import under `app/api/v1/reader.py`, and register the router in `app/api/v1/router.py`. Verify: TestClient returns `200/404/422` as designed and OpenAPI exposes three public Reader `GET` routes.
- [x] 4. Add the `/reader` HTML route in `app/main.py` and create the accessible shell in `app/static/reader.html` for library, reading view, table-of-contents drawer, settings panel, loading/empty/error states, and desktop/mobile navigation controls. Verify: `/reader` returns `200`, the root dashboard remains available, and static Reader assertions pass.
- [x] 5. Implement the Reader client data flow in `reader.html`: book list, book detail, chapter loading, safe paragraph rendering with DOM text nodes, request cancellation, bounded in-memory cache, retry handling, and next-chapter prefetch. Verify: static DOM-safety scan, JavaScript syntax validation, and API smoke flow pass.
- [x] 6. Implement reader ergonomics: light/dark/sepia themes, font size, line height, reading width, scroll progress, keyboard navigation, responsive TOC drawer, and mobile bottom controls. Persist `reader.preferences` and `reader.progress.{novel_id}` with throttled updates. Verify: controls and persistence code are covered by the Reader page regression contract and runtime page smoke.
- [x] 7. Add security and regression coverage to `tests/test_security_boundaries.py` and `tests/test_module_boundaries.py` for public read-only routing, no Reader write methods, escaped/text-only rendering, and dependency boundaries (`reader -> library application`, never persistence/storage). Verify: targeted Reader, security, and boundary suites pass.
- [ ] 8. Run final verification: full `pytest`, `compileall`, Alembic upgrade, app startup/OpenAPI smoke test, JavaScript syntax validation, and browser checks at desktop and mobile viewports across all themes. Automated checks pass; Playwright/browser visual checks remain pending because Playwright is not installed in the workspace environment.

## Dependencies and Order

Tasks 1-3 are the backend critical path. Task 4 depends on the Reader API contract.
Tasks 5 and 6 build on the static shell and can be iterated together. Task 7
captures the final boundaries before Task 8 performs repository-wide verification.

No new runtime dependency or database migration is expected.

## Done When

- [ ] `/reader` lists only books with readable Vietnamese chapters.
- [ ] Opening a book restores the last chapter and reading position.
- [ ] Chapter navigation remains responsive and keeps current content on errors.
- [ ] Preferences work and persist on both desktop and mobile layouts.
- [ ] Reader APIs are public `GET` operations only and never expose source text.
- [ ] Full automated and visual verification passes without regressing `/` or existing APIs.
