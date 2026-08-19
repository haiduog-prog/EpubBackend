# Kế hoạch chuyển structured storage sang Render PostgreSQL

## 1. Mục tiêu

Chuyển toàn bộ dữ liệu có cấu trúc của backend từ Firestore, JSON trên Cloudflare R2 và local disk sang Render PostgreSQL. Sau cutover, R2 chỉ lưu object lớn hoặc nội dung không phù hợp với database.

Kết quả cần đạt:

- Không tạo thêm `profile_*.json`, `metadata.json`, `bible.json` hoặc job JSON trên R2.
- Không phụ thuộc Firestore/Firebase trong runtime, dependency và cấu hình deploy.
- API Android hiện tại giữ nguyên request/response và các ID đang dùng.
- Submission/event vẫn idempotent, snapshot không đọc thông tin từ chương tương lai.
- Xóa truyện dùng transaction và foreign-key cascade, không để orphan.
- Có migration kiểm đếm, rollback và cleanup dry-run trước khi xóa dữ liệu cũ.

## 2. Tóm tắt hiểu biết đã xác nhận

- Backend FastAPI đang chạy trên Render.
- Cloudflare R2 đang dùng tốt cho EPUB, TXT, cover và bản dịch.
- File JSON tăng nhanh do mỗi book, edition, submission, event và evidence là một object riêng.
- `CharacterProfileService` còn mirror cùng dữ liệu sang Firestore, R2 và local disk.
- Firestore sẽ bị loại bỏ, không phải persistence chính hoặc fallback.
- PostgreSQL trên Render sẽ là source of truth duy nhất cho structured state.
- R2 vẫn được giữ cho binary/text payload lớn; không đưa raw chapter dài vào PostgreSQL.

## 3. Giả định và non-goals

### Giả định

- Quy mô ban đầu nhỏ: vài đến vài chục truyện, tối đa vài nghìn chương mỗi truyện và hàng chục nghìn character events.
- Có thể chấp nhận một giai đoạn dual-write ngắn trong lúc cutover.
- API hiện chỉ có một backend owner; chưa cần multi-tenant hoặc phân vùng database.
- ID hiện tại như `book-*`, `edition-*`, `event-*` và `submission-*` phải được giữ nguyên.
- Render web service và Render Postgres nằm cùng region.
- Raw chapter chỉ lưu tạm khi AI xử lý; database chỉ giữ fingerprint, trạng thái và R2 key nếu thật sự cần.

### Không nằm trong phạm vi

- Không đổi API contract Android nếu không bắt buộc.
- Không viết lại engine snapshot/event policy.
- Không chuyển file EPUB/TXT/HTML từ R2 sang PostgreSQL.
- Không thêm Redis trong phase đầu.
- Không materialize snapshot theo mọi chương ngay từ đầu; chỉ bổ sung nếu số liệu hiệu năng chứng minh cần thiết.

## 4. Kiến trúc đích

```text
Android / Web UI
       |
       v
FastAPI trên Render
       |
       +---- PostgreSQL
       |       - novels / chapters
       |       - jobs / import_jobs
       |       - book_bibles
       |       - books / editions / mappings
       |       - submissions / events / evidence
       |       - profile_settings
       |
       +---- Cloudflare R2
               - source EPUB
               - original/translated chapter text
               - cover
               - exported EPUB
```

Ranh giới ownership:

- PostgreSQL quyết định metadata, trạng thái, quan hệ, revision, review và idempotency.
- R2 chỉ quyết định object bytes; PostgreSQL giữ key trỏ tới object khi cần.
- Service layer không gọi trực tiếp Firestore hoặc các hàm `_r2_*` cho structured data.
- Pydantic tiếp tục là API schema; SQLAlchemy models là persistence schema.

## 5. Phương án đã cân nhắc

### Phương án chọn: Render PostgreSQL + R2 object-only

Ưu điểm: transaction, unique constraint, cascade delete, query/filter tốt và nằm cùng hạ tầng Render với API. Đây là lựa chọn cân bằng nhất cho code hiện tại.

### Phương án không chọn: gom profile thành một JSON lớn trên R2

Giảm số object nhưng mỗi lần duyệt phải read-modify-write cả file, khó chống race condition, khó query pending events và rollback từng record.

### Phương án không chọn: Cloudflare D1

D1 phù hợp hơn khi application chạy trên Workers. API hiện ở Render nên sẽ thêm HTTP hop, thêm cơ chế credential và hai mặt vận hành cho database.

## 6. Dependency và cấu hình

Thêm dependency:

```text
SQLAlchemy>=2.0
psycopg[binary]>=3.2
alembic>=1.13
```

Sau cutover ổn định, gỡ:

```text
firebase-admin
```

Biến môi trường mới:

```text
DATABASE_URL=<Render internal PostgreSQL URL>
STRUCTURED_STORAGE_BACKEND=postgres
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
DB_POOL_TIMEOUT_SECONDS=10
```

Feature flags trong migration:

```text
STRUCTURED_STORAGE_BACKEND=legacy|dual|postgres
STRUCTURED_STORAGE_READ_SOURCE=legacy|postgres
```

Không log `DATABASE_URL`. Engine bật `pool_pre_ping`; pool phải nhỏ vì Uvicorn hiện chạy một process. Khi tăng worker/replica, tổng connection pool phải được tính lại.

## 7. Schema PostgreSQL

### 7.1 Library

#### `novels`

- `novel_id text primary key`
- `title text not null`
- `original_title text not null default ''`
- `author text not null default ''`
- `genre jsonb not null default '[]'`
- `description text not null default ''`
- `cover_r2_key text null`
- `status text not null`
- `total_chapters integer not null default 0`
- `translated_chapters integer not null default 0`
- `revision integer not null default 0`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Index: `lower(title)`, `updated_at desc`.

#### `chapters`

- `novel_id text references novels on delete cascade`
- `chapter_index integer`
- `chapter_id text not null`
- `chapter_title text not null`
- `status text not null`
- `word_count integer not null default 0`
- `original_text_preview text not null default ''`
- `translated_text_preview text not null default ''`
- `original_r2_key text null`
- `translated_r2_key text null`
- `translated_r2_url text null`
- `updated_at timestamptz not null`

Primary key: `(novel_id, chapter_index)`. Unique: `(novel_id, chapter_id)`.

### 7.2 Jobs và legacy Book Bible

#### `translation_jobs`

Giữ các field của `TranslationJob`. `job_id` là primary key; index `(status, created_at desc)`. Dùng row update thay cho `data/jobs/{job_id}.json`.

#### `import_jobs`

Giữ các field của `ImportJobStatus` để job không mất khi Render restart. `job_id` là primary key; index `(status, created_at desc)`.

#### `book_bibles`

- `novel_id text primary key`
- `schema_version integer not null`
- `bible_revision integer not null`
- `payload jsonb not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Phase đầu giữ payload JSONB để bảo toàn schema legacy phức tạp. Mỗi update khóa row bằng `SELECT ... FOR UPDATE`, merge bằng service hiện tại rồi update revision trong cùng transaction.

### 7.3 Character profile

#### `profile_books`

- `book_id text primary key`
- `novel_id text null references novels on delete set null`
- metadata columns: `title`, `author`, `language`, `publisher`, `identifier`
- normalized columns: `title_key`, `author_key`
- `sampled_chapters jsonb not null default '[]'`
- `revision integer not null default 0`
- timestamps

Unique có điều kiện cho `novel_id` khi khác null. Không deduplicate bằng UI; database/service phải resolve một canonical `book_id`.

#### `profile_editions`

- `edition_id text primary key`
- `book_id text references profile_books on delete cascade`
- `metadata jsonb not null`
- `fingerprints jsonb not null`
- `chapter_count integer null`
- `mapping_revision integer not null default 1`
- `created_at timestamptz not null`

Index: `book_id`.

#### `profile_chapter_mappings`

- `edition_id text references profile_editions on delete cascade`
- `local_chapter_index integer`
- `canonical_chapter_start integer`
- `canonical_chapter_end integer`
- `confidence double precision`
- `source text`
- `mapping_revision integer`

Primary key: `(edition_id, local_chapter_index)`. Check: end >= start.

#### `profile_submissions`

- `submission_id text primary key`
- `idempotency_key text unique not null`
- `book_id text references profile_books on delete cascade`
- `edition_id text references profile_editions on delete cascade`
- chapter range, input type, fingerprints, source fields
- status/error fields
- `created_at`, `completed_at`

Không lưu raw chapter content. `event_ids` được dựng từ query events theo `source_submission_id`, hoặc giữ JSONB tạm trong compatibility phase.

#### `profile_events`

- `event_id text primary key`
- `event_key text unique not null`
- `book_id text references profile_books on delete cascade`
- `character_id`, `character_original_name`
- `canonical_chapter integer not null`
- `category`, `attribute_key`, `operation`
- `value jsonb null`
- `certainty`, `status`, `evidence`, `confidence`
- `source_group_id`
- `source_submission_id references profile_submissions on delete cascade`
- `supersedes_event_id references profile_events on delete set null`
- timestamps và `schema_version`

Indexes:

- `(book_id, status, canonical_chapter desc)`
- `(book_id, character_id, canonical_chapter)`
- `(source_submission_id)`

#### `profile_evidence`

- `evidence_id text primary key`
- `event_id text references profile_events on delete cascade`
- `event_key text not null`
- `source_group_id text not null`
- `submission_id text references profile_submissions on delete cascade`
- `excerpt text not null`
- `confidence double precision`
- `created_at timestamptz not null`

Unique: `(event_id, source_group_id, submission_id)`. Evidence manual dùng source group `manual-review` nhưng vẫn có ID riêng.

#### `profile_settings`

Một row singleton chứa `auto_approve`, `min_independent_sources` và `updated_at`. Việc update settings hiện chỉ ở RAM sẽ trở thành persistent.

## 8. Transaction và concurrency

### Submit chương

1. Resolve edition và mapping.
2. `INSERT profile_submissions` với unique `idempotency_key`.
3. Nếu conflict, trả submission hiện có.
4. Commit submission trước khi chạy AI background.

### Process candidates

1. Begin transaction, lock submission `FOR UPDATE`.
2. Với mỗi candidate, upsert theo `event_key`.
3. Insert evidence theo unique constraint.
4. Chạy approval policy trên các source group trong database.
5. Chỉ tăng `profile_books.revision` khi canonical approved state thay đổi.
6. Mark submission completed/failed và commit một lần.

### Manual approve/reject

Lock event, insert manual evidence nếu có, update status/value, tăng revision và commit cùng transaction. Hai request đồng thời không được tăng revision sai hoặc ghi mất evidence.

### Delete

Xóa `profile_books` hoặc `novels` trong transaction; foreign keys cascade structured rows. R2 object deletion chạy sau commit bằng danh sách key đã xác định. Nếu R2 delete lỗi, ghi cleanup task/log để retry; không rollback database đã commit.

## 9. Cấu trúc R2 sau migration

Được giữ:

```text
novels/{novel_id}/uploads/{upload_id}.epub
novels/{novel_id}/original/ch_0001.txt
novels/{novel_id}/translated/ch_0001.txt
novels/{novel_id}/cover.jpg
novels/{novel_id}/full.epub
```

Ngừng tạo:

```text
data/jobs/*.json
data/bibles/*.json
data/profile_*/*.json
novels/{novel_id}/metadata.json
novels/{novel_id}/bible.json
novels/{book_id}/profile/**/*.json
storage/novels/**
```

URL public không nên là nguồn dữ liệu authoritative; database giữ R2 key, URL được dựng khi trả response để tránh stale URL khi đổi domain.

## 10. Repository và cấu trúc code

Thêm:

```text
app/db/base.py
app/db/session.py
app/db/models/
app/repositories/job_repository.py
app/repositories/library_repository.py
app/repositories/book_bible_repository.py
app/repositories/character_profile_repository.py
alembic.ini
alembic/env.py
alembic/versions/
scripts/migrate_structured_r2_to_postgres.py
scripts/audit_structured_storage.py
scripts/cleanup_structured_r2.py
```

Refactor:

- `app/core/storage.py`: chỉ còn R2 object operations; bỏ Firebase và structured persistence.
- `app/services/character_profile_service.py`: dùng repository, bỏ `_persist`, `_hydrate_all_from_storage` và state RAM làm source of truth.
- `app/services/library_service.py`: metadata/chapter index/import jobs qua PostgreSQL; content qua R2.
- `app/api/v1/character_profiles.py`: inject repository/database session.
- `app/api/v1/admin.py`: bỏ public destructive cleanup endpoint; migration dùng CLI có dry-run.
- `app/config.py`, `requirements.txt`, `render.yaml`: thêm PostgreSQL và loại Firebase sau cutover.

Service vẫn có thể cache read-only ngắn hạn, nhưng cache phải invalidated theo `revision` và không được dùng làm persistence.

## 11. Kế hoạch triển khai theo phase

### Phase 0 — Baseline và backup

- Ghi nhận số object và tổng bytes theo từng prefix R2.
- Export danh sách book/edition/submission/event/evidence hiện tại.
- Chụp API fixtures cho list books, pending events và snapshots đại diện.
- Tạo một archive nén của structured JSON trên R2, giữ tối thiểu 30 ngày.

Exit criteria: có manifest chứa object key, size, checksum và timestamp; chưa xóa gì.

### Phase 1 — Database foundation

- Provision Render Postgres cùng region.
- Thêm dependency, settings, engine, session và Alembic.
- Tạo toàn bộ tables, constraints và indexes.
- Thêm health check DB nhưng chưa đổi behavior runtime.

Exit criteria: migration chạy được trên DB rỗng và rollback Alembic về base thành công.

### Phase 2 — Repository implementation

- Implement repositories và mapping Pydantic <-> SQLAlchemy.
- Test CRUD, transactions, idempotency, cascade delete và concurrent event ingest.
- Giữ API/service contract hiện tại.

Exit criteria: repository integration tests chạy trên PostgreSQL thật; chưa đọc production từ DB.

### Phase 3 — Idempotent data import

- Import `metadata.json` thành novels/chapters.
- Import jobs/import jobs nếu còn giá trị vận hành.
- Import `bible.json` và legacy `data/bibles` vào `book_bibles`.
- Import profile theo thứ tự books -> editions -> mappings -> submissions -> events -> evidence.
- Deduplicate object legacy/new bằng primary ID và checksum; không cộng count từ bản mirror.

Exit criteria: chạy importer hai lần không đổi row count; mọi FK hợp lệ; báo cáo orphan riêng.

### Phase 4 — Dual-write shadow

- `STRUCTURED_STORAGE_BACKEND=dual`.
- Read từ legacy, write cả legacy và PostgreSQL.
- So sánh async row/document sau mỗi mutation; log ID và hash, không log payload nhạy cảm.
- Chạy đủ create/update/delete, submit/process/approve/reject và import EPUB.

Exit criteria: không có mismatch chưa giải thích trong ít nhất một chu kỳ sử dụng thực tế.

### Phase 5 — PostgreSQL read cutover

- Read từ PostgreSQL, vẫn dual-write tạm thời.
- So sánh API response với fixture baseline.
- Theo dõi lỗi DB, latency, pool saturation và snapshot mismatch.
- Giữ legacy objects nguyên trạng để rollback.

Exit criteria: API contract pass; snapshot temporal tests pass; error/latency trong ngưỡng.

### Phase 6 — PostgreSQL-only

- `STRUCTURED_STORAGE_BACKEND=postgres`.
- Dừng mọi structured write lên R2/local/Firestore.
- Gỡ hydration scan toàn bucket khi startup.
- Persist import jobs và settings vào DB.

Exit criteria: tạo và duyệt một chương mới không sinh object JSON nào ngoài content object cần thiết.

### Phase 7 — Cleanup và bỏ Firebase

- Chạy `cleanup_structured_r2.py --dry-run`.
- Review manifest key/bytes sẽ xóa.
- Xóa structured JSON theo explicit prefixes sau phê duyệt.
- Giữ archive 30 ngày.
- Gỡ Firebase code, env vars và dependency.

Exit criteria: R2 chỉ còn object lớn; deploy không cần Firebase credentials; restore drill từ DB backup thành công.

## 12. Migration mapping

| Nguồn hiện tại | Đích PostgreSQL |
|---|---|
| `data/jobs/{id}.json` | `translation_jobs` |
| `novels/{id}/metadata.json` | `novels`, `chapters` |
| `novels/{id}/bible.json`, `data/bibles/*` | `book_bibles` |
| `profile_books/*.json` | `profile_books` |
| `profile_editions/*.json` | `profile_editions` |
| `profile_chapter_mappings/*.json` | `profile_chapter_mappings` |
| `profile_submissions/*.json` | `profile_submissions` |
| `profile_events/*.json` | `profile_events` |
| `profile_evidence/*.json` | `profile_evidence` |

Importer phải đọc cả layout legacy `data/profile_*` và layout mới `novels/{book_id}/profile/*`. Khi cùng ID xuất hiện nhiều nơi, chọn record có `updated/reviewed/completed_at` mới hơn; nếu không có timestamp thì ưu tiên layout mới và ghi conflict report.

## 13. Audit và tiêu chí đối chiếu

Trước cutover, tạo báo cáo cho từng book/novel:

- metadata hash
- edition count
- mapping count
- submission count theo status
- event count theo status và chapter
- evidence count
- approved snapshot hash tại các chapter đại diện
- Book Bible revision và payload hash
- chapter count và số R2 key tồn tại

Hard fail migration khi:

- Thiếu book/edition cha.
- Event trỏ tới submission không tồn tại.
- Mapping có canonical end nhỏ hơn start.
- Hai payload cùng ID nhưng khác dữ liệu mà không có quy tắc chọn rõ ràng.
- Snapshot approved trước/sau migration khác nhau.

## 14. Rollback

Trước Phase 6:

- Chuyển read source về `legacy`.
- Dual-write đảm bảo mutation mới vẫn có ở legacy.
- Không cần restore database để rollback API.

Sau Phase 6 nhưng trước cleanup:

- Tạm bật lại `dual`.
- Export các row thay đổi sau cutover về legacy nếu thật sự cần rollback.
- Chuyển read về legacy sau khi delta export hoàn tất.

Sau cleanup:

- Restore PostgreSQL bằng Render backup/PITR.
- Structured R2 archive chỉ là phương án phục hồi cuối cùng, không phải runtime fallback.

Cleanup không được chạy cùng deploy cutover; phải là một release/operation riêng.

## 15. Security và vận hành

- Dùng Render internal `DATABASE_URL`; không public allowlist rộng nếu không cần.
- Admin cleanup/migration chạy CLI, không expose endpoint công khai.
- Mọi write endpoint profile phải dùng trusted-client credential hiện có hoặc cơ chế auth thay thế.
- Không lưu API key, full prompt hoặc raw chapter trong DB/log.
- Evidence tiếp tục giới hạn 1000 ký tự.
- Dùng parameterized SQL qua SQLAlchemy.
- Bật backup/PITR phù hợp gói Render; định kỳ kiểm tra restore.
- Thêm statement timeout cho request path và log slow query theo ngưỡng.

## 16. Test matrix

### Unit

- Mapping Pydantic/ORM round-trip.
- Event key và idempotency key deterministic.
- Approval/revision policy.
- R2 key builder không tạo structured JSON key.

### PostgreSQL integration

- Unique submission retry 10 lần chỉ tạo một row.
- Hai transaction ingest cùng event không tạo duplicate.
- Approve/reject đồng thời không mất revision/evidence.
- Delete book cascade đủ mappings/submissions/events/evidence.
- Delete novel không xóa nhầm profile book chưa liên kết.
- Book Bible row lock không mất merge.

### Migration

- Import layout legacy.
- Import layout per-novel mới.
- Hai bản mirror cùng ID được deduplicate.
- Orphan được báo cáo, không silently drop.
- Chạy importer hai lần vẫn idempotent.

### Regression API

- Các test character profile hiện có giữ nguyên.
- Snapshot chapter `50 -> 200 -> 100` không leak tương lai.
- Manual review và approve-all.
- Library create/update/delete, import EPUB và translate chapter.
- Translation job survive process restart.
- Android response JSON không đổi ngoài field mới optional.

### Storage acceptance

- Sau một submission có N events, R2 object count chỉ tăng nếu có raw/content artifact được chủ động lưu.
- Không tồn tại write call tới `data/profile_*`, `metadata.json`, `bible.json` hoặc local `storage/novels` trong production mode.

## 17. Observability và SLO đề xuất

Metrics/logs:

- DB query/transaction duration.
- Connection pool checked-out/wait timeout.
- Submission idempotency conflict rate.
- Event deduplication rate.
- Migration imported/skipped/conflicted/orphan counts.
- Cleanup object count và bytes.
- Snapshot p50/p95 và event replay count.

Mục tiêu ban đầu:

- CRUD metadata p95 dưới 300 ms.
- Snapshot p95 dưới 500 ms với hàng chục nghìn event/book.
- Không có orphan sau cascade delete.
- Không có structured JSON object mới trên R2 sau Phase 6.

Nếu snapshot vượt ngưỡng, phase sau mới thêm checkpoint/materialized projection theo book revision; không đưa vào migration đầu tiên.

## 18. Danh sách file dự kiến thay đổi

- `requirements.txt`
- `render.yaml`
- `app/config.py`
- `app/core/storage.py`
- `app/services/character_profile_service.py`
- `app/services/library_service.py`
- `app/api/v1/character_profiles.py`
- `app/api/v1/admin.py`
- các file DB/repository/Alembic/scripts mới
- test storage, library, Book Bible và character profile liên quan

`app/schemas/*.py` chỉ thay đổi khi cần field persistence nội bộ; API field hiện có được giữ tương thích.

## 19. Thứ tự PR/commit khuyến nghị

1. DB foundation + Alembic + CI PostgreSQL.
2. Repositories + integration tests.
3. Character profile dual-write.
4. Library/jobs/Book Bible dual-write.
5. Idempotent importer + audit report.
6. PostgreSQL read cutover.
7. PostgreSQL-only + remove local structured mirror.
8. Cleanup CLI + remove Firebase.

Mỗi bước phải deploy độc lập và rollback được; không gom schema, import, cutover và delete vào một release.

## 20. Nhật ký quyết định

| Quyết định | Phương án khác | Lý do |
|---|---|---|
| Render PostgreSQL là source of truth | Firestore, D1, R2 JSON | Cùng hạ tầng API, transaction/query/constraint phù hợp |
| R2 chỉ giữ object lớn | Giữ mirror structured JSON | Giảm object count, tránh nhiều nguồn dữ liệu |
| Giữ API IDs dạng text hiện tại | Chuyển toàn bộ sang UUID DB | Không phá Android contract và migration đơn giản |
| Book Bible legacy lưu JSONB phase đầu | Chuẩn hóa toàn bộ nested schema | Giảm phạm vi và rủi ro migration |
| Character events/evidence chuẩn hóa thành rows | Một JSON profile/book | Cần query review, idempotency và audit |
| Dual-write ngắn hạn trước cutover | Big-bang migration | Có rollback an toàn và đối chiếu thực tế |
| Cleanup bằng CLI có dry-run | Public admin endpoint | Giảm rủi ro xóa nhầm và yêu cầu auth phức tạp |
| Chưa thêm Redis/materialized snapshot | Tối ưu ngay từ đầu | Quy mô hiện tại chưa chứng minh cần thiết |

## 21. Definition of Done

- Alembic schema áp dụng thành công trên Render Postgres mới.
- Tất cả structured records hiện có được import và audit pass.
- API/profile/library/job regression tests pass.
- PostgreSQL-only mode chạy ổn định qua ít nhất một chu kỳ deploy/restart.
- R2 không tăng structured JSON object khi tạo/duyệt tiến trình mới.
- Firebase dependency/config/runtime code được gỡ.
- Cleanup dry-run được review và archive backup tồn tại trước khi xóa.
- Runbook rollback và restore được kiểm tra thực tế.
