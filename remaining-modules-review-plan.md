# Ke hoach review va tach cac module con lai

## Muc tieu

Hoan tat modular monolith cho phan ha tang va entrypoint con lai ma khong doi REST contract, du lieu, prompt behavior hay luong CLI/UI hien co.

## Pham vi con lai

- LLM/provider va prompt: `app/llm`, `app/prompts`, cac prompt/QA shim trong `app/services`.
- Ingestion: `app/parsers` va cac luong EPUB/TXT/HTML dung chung.
- Platform: `app/config.py`, `app/db`, `app/bootstrap`, `app/main.py`, admin API va CLI.
- Shared support: QA, direct translation cache, job models/recovery.
- Web UI: `app/static/index.html`.

## Van de uu tien da xac nhan

- P0: `app/infrastructure/storage/` dang bi rule `storage/` trong `.gitignore` loai khoi Git; clone sach se thieu implementation ma `app/core/storage.py` va container dang import.
- `legacy_storage.py` van la god object hon 1.200 dong; cac provider file hien chi re-export.
- `ApplicationContainer` da co nhung `app.main`, router, admin va CLI van dung singleton/import cu.
- Prompt dang trung lap tai `app/prompts/templates.py` va `app/services/prompts.py`.
- UI 2.500+ dong luu API key trong `localStorage` va chen du lieu API qua `innerHTML`, tao rui ro stored XSS/lo key.
- QA, cache va job ownership van nam ngoai bounded context Translation/Library.

## Tasks

- [ ] 1. Khac phuc source completeness: scope `.gitignore` chi bo qua `/storage/` runtime, track toan bo `app/infrastructure/storage`, va them fresh-checkout import smoke. Verify: `git ls-files app/infrastructure/storage` co du source; `python -c "from app.main import app"` chay tren checkout sach.
- [ ] 2. Dong bang contract cho phan con lai: them test provider mapping/error, prompt placeholders, EPUB/TXT/HTML malformed input, QA/cache invalidation, admin auth, CLI args va UI API routes. Verify: test moi do truoc khi di chuyen file va OpenAPI van 41 paths.
- [ ] 3. Tach LLM gateway: dua interface vao shared port, provider Anthropic/Gemini vao `app/infrastructure/llm`, factory vao composition root; chuan hoa timeout, retry, structured-output validation va provider error. Verify: contract test dung fake SDK cho ca bon operation va khong can API key/network.
- [ ] 4. Chia prompt theo owner: extraction/event prompt vao Book Bible/Character Profiles; translate HTML/prose/QA prompt vao Translation; xoa ban sao trong `app/services/prompts.py` bang compatibility export tam thoi. Verify: snapshot/hash prompt va tat ca placeholder bat buoc khong doi.
- [ ] 5. Tach ingestion: dat resilient EPUB reader/cover/archive guard vao `app/infrastructure/documents`; dat TXT chunker va HTML merge/rebuild vao Translation pipeline, expose port cho Library import. Them gioi han archive size/entry count va test EPUB loi. Verify: fixture cover/path/case/nav/multi-chapter va HTML round-trip deu pass.
- [ ] 6. Dua QA, cache va jobs ve dung owner: QA rules thanh pure Translation domain; AI QA thanh application use case; cache qua port + local adapter; job lifecycle/recovery thanh supporting module dung chung cho Translation va Library. Verify: cache atomic/concurrent/corrupt-file, QA dedupe va cold-start recovery pass.
- [ ] 7. Hoan tat platform composition: tao app factory nhan `ApplicationContainer`; `main`, router, admin va CLI chi goi use case duoc inject. Tao `MaintenanceService` cho clean/purge, khong truy cap `_cache`, `_bibles` hay singleton truc tiep. Verify: hai app instance voi fake container khong chia state; admin destructive endpoint van fail-closed.
- [ ] 8. Tach static UI: chia HTML/CSS/API client va JS theo Translation, Library, Book Bible, Character Profiles/Admin; dung safe DOM rendering, khong luu provider key lau dai trong `localStorage`. Verify: Playwright smoke cho paste/upload, poll/download, library import/export va event review; khong con noi suy du lieu API vao `innerHTML`.
- [ ] 9. Cleanup va verification cuoi: doi import API/CLI/test sang owner moi, go facade cu khi khong con consumer, them AST architecture gate va fresh-checkout CI. Verify: `compileall`, full `pytest`, migration smoke, startup/OpenAPI, CLI TXT smoke va UI E2E deu xanh.

## Thu tu

Critical path: `source completeness -> contract tests -> LLM/prompt + ingestion (song song) -> QA/cache/jobs -> composition/admin/CLI -> UI -> cleanup/verification`.

## Done when

- Checkout sach co the import/start app ma khong phu thuoc file ignored.
- Domain/application khong import SDK, FastAPI, SQLAlchemy hay singleton storage.
- Khong con business logic trong compatibility shim; moi provider/parser/use case co contract test.
- API, migration, CLI va bon workflow UI chinh giu nguyen hanh vi va full suite khong regression.
