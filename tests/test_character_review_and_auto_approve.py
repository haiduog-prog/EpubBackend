import pytest
from app.schemas.book_bible import BookBibleDelta, CharacterEntry
from app.schemas.character_profile import (
    BookMetadata,
    BookResolutionRequest,
    CharacterEventCandidate,
    EditionCreateRequest,
)
from app.services.character_profile_service import CharacterProfileService


def _setup_service(auto_approve=False, min_sources=2):
    service = CharacterProfileService(min_independent_sources=min_sources, auto_approve=auto_approve)
    res = service.resolve_book(
        BookResolutionRequest(metadata=BookMetadata(title="Test Novel", author="Author", language="vi"))
    )
    edition = service.create_edition(
        res.book_id,
        EditionCreateRequest(metadata=BookMetadata(title="Test Novel", author="Author", language="vi")),
    )
    return service, res.book_id, edition.edition_id


def test_manual_review_flow():
    # 1. auto_approve = False -> event is pending
    service, book_id, edition_id = _setup_service(auto_approve=False, min_sources=2)
    candidate = CharacterEventCandidate(
        character_original_name="Tiêu Viêm",
        category="realm",
        attribute_key="cultivation_realm",
        operation="set",
        value="Đấu Giả Nhất Tinh",
        evidence="Tiêu Viêm đột phá Đấu Giả",
        confidence=0.9,
    )
    submission = service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-1",
        local_chapter_index=1,
        input_type="structured_events",
        content_fingerprint="fp-1",
        candidates=[candidate],
    )
    assert submission.status == "completed"

    # Event should be pending
    events = service.list_events(book_id=book_id, status="pending")
    assert len(events) == 1
    event_id = events[0].event_id
    assert events[0].status == "pending"

    # Snapshot at chapter 1 should be empty because event is pending
    snapshot = service.snapshot(edition_id, 1)
    assert len(snapshot.characters) == 0

    # 2. Approve event manually
    approved = service.approve_event(event_id)
    assert approved.status == "approved"

    # Now snapshot should have Tiêu Viêm
    snapshot_after = service.snapshot(edition_id, 1)
    assert len(snapshot_after.characters) == 1
    assert snapshot_after.characters[0].original_name == "Tiêu Viêm"
    assert snapshot_after.characters[0].attributes["cultivation_realm"] == "Đấu Giả Nhất Tinh"


def test_reject_event():
    service, book_id, edition_id = _setup_service(auto_approve=False, min_sources=2)
    candidate = CharacterEventCandidate(
        character_original_name="Nạp Lan Yên Nhiên",
        category="status",
        attribute_key="state",
        operation="set",
        value="tử vong",
        evidence="tin đồn nhảm",
        confidence=0.6,
    )
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-reject",
        local_chapter_index=5,
        input_type="structured_events",
        content_fingerprint="fp-rej",
        candidates=[candidate],
    )
    events = service.list_events(book_id=book_id, status="pending")
    assert len(events) == 1

    rejected = service.reject_event(events[0].event_id)
    assert rejected.status == "rejected"
    assert len(service.list_events(book_id=book_id, status="pending")) == 0
    assert len(service.snapshot(edition_id, 5).characters) == 0


def test_auto_approve_flow():
    # When auto_approve = True, single submission is approved immediately
    service, book_id, edition_id = _setup_service(auto_approve=True, min_sources=2)
    candidate = CharacterEventCandidate(
        character_original_name="Dược Lão",
        category="realm",
        attribute_key="cultivation_realm",
        operation="set",
        value="Đấu Tôn Đỉnh Phong",
        evidence="Dược Trần năm xưa là Bát phẩm luyện dược sư, Đấu Tôn đỉnh phong",
        confidence=0.95,
    )
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-auto",
        local_chapter_index=2,
        input_type="structured_events",
        content_fingerprint="fp-auto",
        candidates=[candidate],
    )
    events = service.list_events(book_id=book_id, status="approved")
    assert len(events) == 1
    assert events[0].status == "approved"

    snapshot = service.snapshot(edition_id, 2)
    assert len(snapshot.characters) == 1
    assert snapshot.characters[0].original_name == "Dược Lão"


def test_approve_all_pending_and_settings_update():
    service, book_id, edition_id = _setup_service(auto_approve=False, min_sources=2)
    c1 = CharacterEventCandidate(
        character_original_name="Tiêu Viêm",
        category="skill",
        attribute_key="skills",
        operation="add",
        value="Hấp Chưởng",
        confidence=0.9,
    )
    c2 = CharacterEventCandidate(
        character_original_name="Tiêu Viêm",
        category="skill",
        attribute_key="skills",
        operation="add",
        value="Bát Cực Băng",
        confidence=0.9,
    )
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-multi",
        local_chapter_index=3,
        input_type="structured_events",
        content_fingerprint="fp-multi",
        candidates=[c1, c2],
    )
    assert len(service.list_events(book_id=book_id, status="pending")) == 2

    # Bulk approve
    approved = service.approve_all_pending(book_id=book_id, canonical_chapter=3)
    assert len(approved) == 2
    assert len(service.list_events(book_id=book_id, status="pending")) == 0

    # Settings toggle
    settings = service.get_settings()
    assert settings["auto_approve"] is False
    updated = service.update_settings(auto_approve=True, min_sources=1)
    assert updated["auto_approve"] is True
    assert updated["min_independent_sources"] == 1


def test_approve_with_supplemental_evidence():
    service, book_id, edition_id = _setup_service(auto_approve=False, min_sources=2)
    candidate = CharacterEventCandidate(
        character_original_name="Tiêu Viêm",
        category="realm",
        attribute_key="cultivation_realm",
        operation="set",
        value="Đấu Giả",
        evidence="AI trích xuất ngắn",
        confidence=0.8,
    )
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-evidence-1",
        local_chapter_index=10,
        input_type="structured_events",
        content_fingerprint="fp-ev1",
        candidates=[candidate],
    )
    events = service.list_events(book_id=book_id, status="pending")
    assert len(events) == 1
    event_id = events[0].event_id

    # User bổ sung dẫn chứng đầy đủ và sửa giá trị chuẩn xác
    updated_evidence = "Tiêu Viêm ngồi xếp bằng trên tảng đá, đấu khí trong cơ thể ngưng tụ thành đấu khí lốc xoáy, chính thức bước vào Đấu Giả Nhất Tinh!"
    approved = service.approve_event(
        event_id=event_id,
        evidence=updated_evidence,
        value="Đấu Giả Nhất Tinh",
    )
    assert approved.status == "approved"
    assert approved.evidence == updated_evidence
    assert approved.value == "Đấu Giả Nhất Tinh"

    # Kiểm tra bằng chứng được lưu vào collection evidence
    assert any(ev.excerpt == updated_evidence for ev in service.evidence.values())

    # Snapshot cập nhật đúng giá trị
    snapshot = service.snapshot(edition_id, 10)
    assert snapshot.characters[0].attributes["cultivation_realm"] == "Đấu Giả Nhất Tinh"
