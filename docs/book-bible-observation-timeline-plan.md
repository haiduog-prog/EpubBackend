# Book Bible Observation Timeline — Implementation Plan

## 1. Mục tiêu

Nâng cấp Book Bible để tên nhân vật và danh xưng nhất quán khi người dùng dịch chapter không theo thứ tự, ví dụ `50 -> 200 -> 100`.

Hệ thống phải:

- Giữ `vi_name` canonical ổn định.
- Ghi nhận danh xưng theo chapter và bằng chứng, không ghi đè lịch sử.
- Không dùng thông tin từ chapter tương lai cho chapter quá khứ.
- Tự áp dụng thay đổi có độ tin cậy cao; đưa xung đột và canonical correction vào hàng chờ duyệt.
- Hỗ trợ retry và concurrent requests mà không tạo duplicate hoặc mất dữ liệu.

## 2. Assumptions và non-goals

### Assumptions

- `novel_id` định danh ổn định một truyện.
- Mỗi request có `chapter_index` đáng tin cậy; `chapter_id` ổn định cho retry.
- Firestore tiếp tục là persistence chính.
- Một novel có thể có hàng nghìn chapter.
- Tính nhất quán quan trọng hơn tiết kiệm một lượng nhỏ token hoặc LLM call.

### Non-goals phase đầu

- Không tự sửa lại các chapter đã dịch.
- Không suy đoán chapter chuyển đổi chính xác trong khoảng chưa được quan sát.
- Không tự đổi `vi_name` canonical.
- Chưa cache kết quả resolver lâu dài; ưu tiên correctness trước.

## 3. Kiến trúc được chọn

Sử dụng **Observation Timeline + Resolver**.

```text
Chapter input
  -> extract entity/address observations
  -> HybridPolicyEngine
  -> Firestore transaction
  -> AddressRuleResolver.resolve(chapter_index)
  -> filtered Book Bible
  -> translate chapter
```

Book Bible giữ canonical data. Lịch sử danh xưng được lưu append-only dưới dạng observations. Resolver là nguồn quyết định duy nhất về rule dùng cho một chapter.

## 4. Data model

### Character identity

Thêm `character_id` ổn định vào `CharacterEntry`. Với dữ liệu cũ, tạo ID deterministic từ `novel_id + normalized original_name`.

`aliases` chỉ chứa tên riêng hoặc biệt danh có khả năng định danh nhân vật. Các từ quan hệ chung như `sư phụ`, `đại nhân`, `chưởng môn` không được lưu làm alias.

### AddressObservation

```python
class AddressObservation(BaseModel):
    observation_id: str
    character_id: str
    counterpart_id: str | None = None
    counterpart_text: str = ""
    self_term: str
    other_term: str
    context: str = ""

    chapter_index: int | None
    chapter_id: str
    chunk_id: str
    evidence: str
    confidence: float

    change_type: Literal["same", "new", "replace", "uncertain"]
    resolution: Literal["confirmed", "inferred", "pending", "rejected"]
    explicit_transition: bool = False
    source: Literal["llm", "user", "legacy"] = "llm"
    created_at: str
```

### PendingBibleChange

```python
class PendingBibleChange(BaseModel):
    change_id: str
    change_type: Literal["canonical_correction", "identity_conflict", "address_conflict"]
    target_id: str
    old_value: dict | None
    proposed_value: dict
    evidence: str
    confidence: float
    chapter_index: int | None
    status: Literal["pending", "approved", "rejected"]
    reviewed_at: str | None = None
    reviewed_by: str | None = None
```

Thêm `schema_version` và `bible_revision` vào document canonical.

## 5. Resolver policy

Khi resolve chapter `N`, chỉ xét observations có `chapter_index <= N` hoặc legacy observation không có chapter.

Thứ tự ưu tiên:

1. `confirmed` trong chapter hiện tại.
2. `confirmed` gần nhất trước chapter hiện tại.
3. `inferred` gần nhất trước chapter hiện tại.
4. Legacy/default rule.
5. Không có rule.

Observation tương lai tuyệt đối không được dùng ngược về quá khứ.

`inferred` chỉ phục vụ dịch hiện tại, không đóng rule cũ và không thay đổi timeline confirmed.

Ví dụ `50 -> 200 -> 100`:

- Chapter 50 xác nhận rule cũ.
- Chapter 200 xác nhận rule mới; khoảng `51..199` vẫn unknown.
- Chapter 100 không được dùng rule chapter 200.
- Nếu chapter 100 không có bằng chứng, dùng rule confirmed gần nhất trước đó và đánh dấu inferred.

## 6. HybridPolicyEngine

Policy mặc định:

| Điều kiện | Kết quả |
|---|---|
| Evidence chuyển đổi rõ và `confidence >= 0.90` | `confirmed` |
| Danh xưng xuất hiện rõ nhưng thời điểm bắt đầu không rõ | `confirmed` tại chapter quan sát |
| Phù hợp rule gần nhất, không có bằng chứng mới | `inferred` |
| Xung đột hoặc confidence dưới ngưỡng | `pending` |
| Đổi `vi_name` canonical | luôn `pending` |

Rule cũ không bị xóa. Khi thấy rule mới ở chapter 200, chỉ ghi `superseded_observed_at=200`; không tự gán `valid_to=199`.

## 7. API contract

### Direct text

```json
{
  "novel_id": "novel-1",
  "chapter_index": 200,
  "chapter_id": "chapter-200",
  "text": "..."
}
```

### File upload

`POST /translate/file` nhận thêm multipart fields:

- `novel_id`
- `chapter_index` cho file chapter rời
- `chapter_id`

Với EPUB nguyên cuốn, backend xác định index theo spine order.

### Response

```json
{
  "translated_text": "...",
  "book_bible": {},
  "address_resolution": {
    "applied_observation_ids": [],
    "pending_change_ids": [],
    "has_uncertainty": false
  }
}
```

### Review endpoints

```text
GET  /book-bible/{novel_id}/pending
POST /book-bible/{novel_id}/pending/{change_id}/approve
POST /book-bible/{novel_id}/pending/{change_id}/reject
```

## 8. Firestore layout và idempotency

```text
book_bibles/{novel_id}
book_bibles/{novel_id}/address_observations/{observation_id}
book_bibles/{novel_id}/pending_changes/{change_id}
```

`observation_id` được tạo deterministic từ:

```text
novel_id + chapter_id + chunk_id + character_id
+ counterpart_id/text + self_term + other_term
```

Retry cùng request sẽ upsert cùng observation.

Mỗi transaction:

1. Đọc canonical Bible và observations cần thiết.
2. Resolve character/alias.
3. Chạy HybridPolicyEngine.
4. Upsert observations và pending changes.
5. Cập nhật canonical data chỉ với entity/alias an toàn.
6. Tăng `bible_revision`.
7. Commit trước khi dịch.

Nếu transaction thất bại, không dịch bằng state chưa commit.

## 9. Implementation phases

### Phase 1 — Schema và migration compatibility

Files:

- `app/schemas/book_bible.py`
- `app/core/storage.py`
- `tests/test_book_bible_migration.py`

Tasks:

- Thêm models và fields mới với default tương thích ngược.
- Tạo deterministic `character_id`.
- Convert `address_terms` v1 thành legacy observations khi đọc.
- Giữ response fields cũ trong giai đoạn chuyển tiếp.

Exit criteria:

- Bible v1 load được mà không mất dữ liệu.
- Bible v2 round-trip Firestore thành công.

### Phase 2 — Extraction contract và policy engine

Files:

- `app/prompts/templates.py`
- `app/llm/base.py`
- `app/llm/anthropic_provider.py`
- `app/llm/gemini_provider.py`
- `app/services/hybrid_policy_service.py`
- `tests/test_hybrid_policy.py`

Tasks:

- LLM trả observations với evidence/confidence/change type.
- Không đưa chức danh quan hệ chung vào aliases.
- Canonical correction luôn tạo pending change.

Exit criteria:

- Policy unit tests bao phủ mọi branch.
- Malformed/low-confidence output không tự thay đổi canonical Bible.

### Phase 3 — Resolver và pipeline integration

Files:

- `app/services/address_rule_resolver.py`
- `app/services/pipeline_service.py`
- `app/services/book_bible_service.py`
- `tests/test_address_rule_resolver.py`
- `tests/test_book_bible_timeline_pipeline.py`

Tasks:

- Resolve theo chapter trước khi tạo prompt dịch.
- Commit observation trước khi dịch.
- Trả resolution metadata.

Exit criteria:

- Scenario `50 -> 200 -> 100` pass.
- Không có future-information leakage.

### Phase 4 — API và pending-review workflow

Files:

- `app/api/v1/translate.py`
- `app/api/v1/book_bible.py`
- response/request schemas liên quan
- API integration tests

Tasks:

- Thêm chapter identity cho text/file.
- Thêm pending list/approve/reject.
- Ghi audit metadata.

Exit criteria:

- Android có thể dịch chapter rời với cùng `novel_id`.
- Approve/reject là idempotent.

### Phase 5 — Shadow rollout

Tasks:

- Thêm `ENABLE_TIMELINE_BOOK_BIBLE`.
- Shadow mode tính resolver mới nhưng chưa dùng để dịch.
- Log structured diff giữa legacy và timeline resolver.
- Bật theo allowlist novel, sau đó bật mặc định.

Rollback bằng feature flag; observations append-only được giữ nguyên.

## 10. Test matrix bắt buộc

- Chapter `50 -> 200 -> 100`.
- Chapter hiện tại có transition explicit.
- Gap chapter không sinh `valid_to` giả.
- Observation tương lai không ảnh hưởng chapter quá khứ.
- Chapter không có evidence dùng nearest prior confirmed dưới dạng inferred.
- Hai character cùng được gọi `sư phụ` không merge identity.
- Alias thật map đúng canonical character.
- Canonical correction luôn vào pending.
- Retry không tạo duplicate observation.
- Concurrent requests cùng novel không mất observation.
- Firestore transaction fail thì translation không chạy.
- Migration v1 giữ canonical names và address terms.
- Approve/reject retry vẫn idempotent.

## 11. Observability và vận hành

Structured metrics/logs:

- `book_bible.observation.confirmed`
- `book_bible.observation.inferred`
- `book_bible.change.pending`
- `book_bible.identity.conflict`
- `book_bible.future_rule_blocked`
- `book_bible.transaction.failed`
- resolver result kèm `novel_id`, `chapter_index`, `bible_revision`

Không log toàn bộ chapter text; evidence được giới hạn độ dài để tránh lộ nội dung và phình log.

## 12. Decision log

1. Chọn Observation Timeline thay vì snapshot theo chapter hoặc `valid_from/valid_to` trực tiếp.
   - Lý do: biểu diễn được khoảng chưa biết và hỗ trợ dịch không theo thứ tự.
2. Chọn Hybrid policy.
   - Lý do: tự động hóa thay đổi rõ ràng nhưng bảo vệ canonical data và xung đột.
3. Không dùng future observation cho past chapter.
   - Lý do: tránh knowledge leakage và bản dịch sai timeline.
4. `inferred` không thay đổi confirmed timeline.
   - Lý do: thiếu evidence không được biến thành lịch sử chính thức.
5. Tách relationship titles khỏi aliases.
   - Lý do: chức danh chung không định danh duy nhất một character.
6. Commit observations trước translation.
   - Lý do: prompt dịch phải dùng đúng state đã persist và retry-safe.

