# Ke hoach tach module EpubBackend

## Muc tieu

Chuyen codebase tu cac thu muc layer dung chung sang modular monolith theo bounded context, giam `god object` va dependency vong, nhung khong doi REST contract, schema database hay hanh vi da duoc test.

## Gia dinh va pham vi

- Giu mot FastAPI deployment va mot database; khong tach microservice.
- Giu nguyen cac route `/api/v1/*`, response schema va Alembic history.
- Refactor theo tung PR nho; moi PR phai deploy/rollback doc lap.
- Cac import cu nhu `app.services.library_service` duoc giu bang compatibility facade cho den phase cuoi.
- Worker/queue ben ngoai la cong viec sau refactor; phase nay chi tao boundary de co the thay `asyncio.create_task()` sau nay.

## Quyet dinh kien truc

Chon vertical slice theo domain thay vi tiep tuc tach file trong bon thu muc layer toan cuc:

```text
app/
  modules/
    library/
      api.py
      schemas.py
      application/
        novel_service.py
        chapter_service.py
        epub_import_service.py
        epub_export_service.py
      persistence/
        models.py
        repository.py
    translation/
      api.py
      schemas.py
      application/
        direct_translation_service.py
        file_translation_service.py
        job_service.py
      pipelines/
        text.py
        txt.py
        epub.py
      persistence/
        models.py
        job_repository.py
    book_bible/
      api.py
      schemas.py
      domain/
        merge_service.py
        address_resolver.py
        review_policy.py
      persistence/
        models.py
        repository.py
    character_profiles/
      api.py
      schemas.py
      domain/
        identity.py
        event_policy.py
        snapshot_reducer.py
      application/
        book_service.py
        edition_service.py
        submission_service.py
        event_review_service.py
        snapshot_service.py
      persistence/
        models.py
        repositories.py
  infrastructure/
    storage/
      base.py
      local.py
      r2.py
      supabase.py
      job_store.py
      bible_store.py
      facade.py
```

Dependency hop le: `api -> application -> domain/ports`, con `persistence` va `infrastructure` implement port. Domain khong import FastAPI, SQLAlchemy, storage provider hay LLM SDK.

## Ke hoach thuc hien

- [ ] 1. Dong bang contract hien tai: them test snapshot cho OpenAPI path/method, facade public va cac luong persistence quan trong. Verify: route count/path khong doi, baseline `77 passed` van xanh.
- [ ] 2. Tao composition root va port: inject blob store, job store, Bible store, repository va LLM factory vao service; `app.main` chi khoi tao dependency va router. Verify: test service dung fake dependency, khong can patch global `storage_repo`.
- [ ] 3. Tach `app/core/storage.py`: chuyen ba provider, job persistence va Book Bible persistence sang `app/infrastructure/storage`; file cu chi re-export `StorageRepository` va `storage_repo`. Verify: storage, Supabase, R2, PostgreSQL integration tests pass.
- [ ] 4. Tach Library: `NovelService` quan ly metadata/CRUD, `ChapterService` quan ly noi dung va dich mot chapter, `EpubImportService` quan ly import job, `EpubExportService` dong goi file. `LibraryService` cu delegate sang bon service. Verify: import, overwrite chapter, translate, export va recovery tests pass.
- [ ] 5. Tach Translation va Book Bible: dua direct/TXT/EPUB vao ba pipeline rieng; `JobService` so huu lifecycle/recovery; merge, resolver va review policy nam trong Book Bible domain. Verify: direct text, TXT, EPUB, timeline `50 -> 200 -> 100` va job recovery pass.
- [ ] 6. Tach Character Profiles: dua normalize/identity/event key/snapshot reducer thanh pure domain; tach book, edition/mapping, submission, review va snapshot use case; chia repository theo aggregate. Verify: idempotency, merge book, auto-approve, timeline va snapshot khong lo future data.
- [ ] 7. Chuyen router/schema/model/repository vao tung module: router cu re-export router moi; `app/db/models/__init__.py` re-export model moi de Alembic van discover du metadata. Verify: `alembic upgrade head` tren SQLite tam va startup OpenAPI smoke pass.
- [ ] 8. Go compatibility facade sau khi API, CLI va test da doi import; them architecture test dung Python AST de chan import sai chieu va import cheo truc tiep giua module. Verify: khong con business logic trong `app/api/v1`, `app/services`, `app/repositories`, `app/db/models` cu.
- [ ] 9. Verification cuoi: chay `compileall`, full `pytest`, migration smoke, FastAPI startup va mot luong upload -> poll -> download. Done khi ket qua khong thap hon baseline va khong co thay doi OpenAPI/DB ngoai du kien.

## Thu tu va rollback

Critical path: `contract tests -> ports/composition -> storage -> library -> translation/Book Bible -> character profiles -> route/model move -> cleanup -> verification`.

Moi phase chi di chuyen mot ownership boundary, giu facade cu va commit rieng. Neu regression, rollback phase hien tai ma khong can rollback schema hay du lieu. Khong gom model move, service split va API rewrite vao cung mot PR.

## Done when

- Khong file service nao vuot qua khoang 400-500 dong neu khong co ly do ro rang.
- Moi use case co mot owner, khong truy cap `db_session()` hoac global storage tu domain logic.
- API public, Alembic metadata va du lieu cu tuong thich nguoc.
- Full test suite, migration smoke va startup smoke deu pass.

## Nhat ky quyet dinh

- Chon modular monolith: phu hop quy mo hien tai va van cho phep tach worker/service sau nay.
- Chon compatibility facade: giam blast radius do API, CLI va test dang import truc tiep cac class cu.
- Chon tach storage truoc: day la dependency dung chung va hien dang tron blob provider, job store, Bible store va DB access.
- Hoan microservice/CQRS/full Clean Architecture: chua co nhu cau scale va ownership doc lap de bu cho chi phi van hanh.
