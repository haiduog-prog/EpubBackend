import json
import pytest
from app.schemas.character_profile import (
    BookMetadata,
    BookResolutionRequest,
    CharacterEventCandidate,
    EditionCreateRequest,
)
from app.services.character_profile_service import (
    CharacterProfileService,
    _canonicalize_attribute_info,
)


def test_canonicalize_attribute_info_mapping():
    # 1. Realm synonyms
    cat, key, val, op = _canonicalize_attribute_info("custom", "hon_luc_level", "Cấp 3", "set")
    assert cat == "realm" and key == "realm" and val == "Cấp 3"

    cat, key, val, op = _canonicalize_attribute_info("custom", "Hồn Lực", "Cảm ứng", "set")
    assert cat == "realm" and key == "realm"

    cat, key, val, op = _canonicalize_attribute_info("custom", "Hồn lực", "Nhị hoàn", "set")
    assert cat == "realm" and key == "realm"

    # 2. Power synonyms
    cat, key, val, op = _canonicalize_attribute_info("custom", "vo_hun", "Lam Ngân Thảo", "set")
    assert cat == "power" and key == "martial_soul"

    cat, key, val, op = _canonicalize_attribute_info("custom", "Võ Hồn", ["Lam Ngân Thảo"], "set")
    assert cat == "power" and key == "martial_soul"

    # 3. Profession synonyms
    cat, key, val, op = _canonicalize_attribute_info("custom", "Đoán Tạo Sư", "Cấp 5", "set")
    assert cat == "faction" and key == "profession"

    cat, key, val, op = _canonicalize_attribute_info("custom", "blacksmith_rank", "Tông Tượng", "set")
    assert cat == "faction" and key == "profession"

    # 4. Academy synonyms
    cat, key, val, op = _canonicalize_attribute_info("custom", "hoc_sinh_su_lai_khac", "Sử Lai Khắc", "set")
    assert cat == "faction" and key == "academy"

    cat, key, val, op = _canonicalize_attribute_info("custom", "Học viện", "Sử Lai Khắc học viện", "set")
    assert cat == "faction" and key == "academy"

    # 5. Weapon and Skill detection
    cat, key, val, op = _canonicalize_attribute_info("custom", "weapon", "Linh Đoán Trầm Ngân Chuy", "set")
    assert cat == "item" and key == "weapon"

    cat, key, val, op = _canonicalize_attribute_info("custom", "Loạn Phi Phong Chuy Pháp", "49 chuy", "set")
    assert cat == "skill" and key == "techniques" and op == "add"
    assert "Loạn Phi Phong Chuy Pháp" in val


def test_character_snapshot_deduplication_and_overwrite():
    service = CharacterProfileService(min_independent_sources=1, auto_approve=True)

    # Create Book and Edition
    res = service.resolve_book(
        BookResolutionRequest(
            metadata=BookMetadata(title="Đấu La Đại Lục 3", author="Đường Gia Tam Thiếu", language="vi"),
            create_if_missing=True,
        )
    )
    book_id = res.book_id
    edition = service.create_edition(
        book_id,
        EditionCreateRequest(
            metadata=BookMetadata(title="Đấu La Đại Lục 3", author="Đường Gia Tam Thiếu", language="vi"),
            chapter_count=500,
        ),
    )
    edition_id = edition.edition_id

    # Chapter 5: hon_luc_level = "Tiên Thiên Hồn Lực cấp ba"
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-ch5",
        local_chapter_index=5,
        input_type="structured_events",
        content_fingerprint="fp-ch5",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="hon_luc_level",
                operation="set",
                value="Tiên Thiên Hồn Lực cấp ba",
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="vo_hun",
                operation="set",
                value="Lam Ngân Thảo",
                confidence=0.9,
            ),
        ],
    )

    # Chapter 20: Hồn Lực = "Sơ bộ cảm ứng được Hồn Lực và Lam Ngân Thảo", Đoán Tạo Sư = "Đoán Tạo Sư cấp 5"
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-ch20",
        local_chapter_index=20,
        input_type="structured_events",
        content_fingerprint="fp-ch20",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="Hồn Lực",
                operation="set",
                value="Sơ bộ cảm ứng được Hồn Lực và Lam Ngân Thảo",
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="Đoán Tạo Sư",
                operation="set",
                value="Đoán Tạo Sư cấp 5 (sơ cấp)",
                confidence=0.9,
            ),
        ],
    )

    # Chapter 100: weapon = "Linh Đoán Trầm Ngân Chuy", Loạn Phi Phong Chuy Pháp = "49 chuy"
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-ch100",
        local_chapter_index=100,
        input_type="structured_events",
        content_fingerprint="fp-ch100",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="weapon",
                operation="set",
                value="Linh Đoán Trầm Ngân Chuy",
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="Loạn Phi Phong Chuy Pháp",
                operation="set",
                value="49 chuy",
                confidence=0.9,
            ),
        ],
    )

    # Chapter 284: Hồn lực = "Nhị hoàn", blacksmith_rank = "Tông Tượng cấp Đoán Tạo Sư", Võ Hồn = "Lam Ngân Thảo"
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-ch284",
        local_chapter_index=284,
        input_type="structured_events",
        content_fingerprint="fp-ch284",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="Hồn lực",
                operation="set",
                value="Nhị hoàn",
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="blacksmith_rank",
                operation="set",
                value="Tông Tượng cấp Đoán Tạo Sư",
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="custom",
                attribute_key="Võ Hồn",
                operation="set",
                value="Lam Ngân Thảo",
                confidence=0.9,
            ),
        ],
    )

    # Generate Snapshot at Chapter 300
    snap_resp = service.snapshot(edition_id=edition_id, local_chapter_index=300)
    assert len(snap_resp.characters) == 1
    char = snap_resp.characters[0]
    attrs = char.attributes

    # 1. Verify overwrite: realm must be the latest "Nhị hoàn"
    assert attrs.get("realm") == "Nhị hoàn"
    assert "hon_luc_level" not in attrs
    assert "Hồn Lực" not in attrs
    assert "Hồn lực" not in attrs

    # 2. Verify martial soul deduplication
    assert attrs.get("martial_soul") == "Lam Ngân Thảo"
    assert "vo_hun" not in attrs
    assert "Võ Hồn" not in attrs

    # 3. Verify profession overwrite
    assert attrs.get("profession") == "Tông Tượng cấp Đoán Tạo Sư"
    assert "Đoán Tạo Sư" not in attrs
    assert "blacksmith_rank" not in attrs

    # 4. Verify weapon and techniques
    assert attrs.get("weapon") == "Linh Đoán Trầm Ngân Chuy"
    assert any("Loạn Phi Phong Chuy Pháp" in t for t in attrs.get("techniques", []))
    assert "Loạn Phi Phong Chuy Pháp" not in attrs


def test_address_terms_kinship_resolution_and_deduplication():
    service = CharacterProfileService(min_independent_sources=1, auto_approve=True)

    meta = BookMetadata(title="Vạn Cổ Thần Đế Kinship Edition", author="Phi Thiên Ngư", language="vi")
    res = service.resolve_book(
        BookResolutionRequest(
            metadata=meta,
            create_if_missing=True,
        )
    )
    book_id = res.book_id
    assert book_id is not None
    edition = service.create_edition(
        book_id,
        EditionCreateRequest(
            metadata=meta,
            chapter_count=500,
        ),
    )
    edition_id = edition.edition_id

    # Chapter 1: Create characters with profile roles
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-ch1-profiles",
        local_chapter_index=1,
        input_type="structured_events",
        content_fingerprint="fp-ch1-profiles",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="identity",
                attribute_key="profile",
                operation="set",
                value={"vi_name": "Đường Vũ Lân", "role": "Nam chính", "aliases": ["Vũ Lân"]},
                confidence=1.0,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Tư Nhiên",
                category="identity",
                attribute_key="profile",
                operation="set",
                value={"vi_name": "Đường Tư Nhiên", "role": "Phụ thân của Đường Vũ Lân", "aliases": ["Đường thúc"]},
                confidence=1.0,
            ),
            CharacterEventCandidate(
                character_original_name="Lang Nguyệt",
                category="identity",
                attribute_key="profile",
                operation="set",
                value={"vi_name": "Lang Nguyệt", "role": "Mẫu thân của Đường Vũ Lân", "aliases": []},
                confidence=1.0,
            ),
            CharacterEventCandidate(
                character_original_name="Trọc Thế",
                category="identity",
                attribute_key="profile",
                operation="set",
                value={"vi_name": "Trọc Thế", "role": "Sư tổ của Đường Vũ Lân, Xích Hỏa Đấu La", "aliases": ["Sư tổ", "Xích Long Đấu La"]},
                confidence=1.0,
            ),
        ],
    )

    # Chapter 5: Add address terms with kinship/honorific "phụ thân", "mẫu thân", "Mang Thiên"
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-ch5-addr",
        local_chapter_index=5,
        input_type="structured_events",
        content_fingerprint="fp-ch5-addr",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="relationship",
                attribute_key="address_terms",
                operation="add",
                value={"with": "Mang Thiên", "self": "con", "other": "thúc thúc", "context": "Giao tiếp với người lớn"},
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="relationship",
                attribute_key="address_terms",
                operation="add",
                value={"with": "phụ thân", "self": "con", "other": "phụ thân", "context": "Trò chuyện giữa cha và con"},
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="relationship",
                attribute_key="address_terms",
                operation="add",
                value={"with": "mẫu thân", "self": "con", "other": "mẫu thân", "context": "Con trò chuyện với mẹ"},
                confidence=0.9,
            ),
        ],
    )

    # Chapter 200: Add address terms for Trọc Thế (one with with="Trọc Thế", one with with="Sư tổ")
    service.submit(
        book_id=book_id,
        edition_id=edition_id,
        idempotency_key="sub-ch200-addr",
        local_chapter_index=200,
        input_type="structured_events",
        content_fingerprint="fp-ch200-addr",
        candidates=[
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="relationship",
                attribute_key="address_terms",
                operation="add",
                value={"with": "Trọc Thế", "self": "ta", "other": "Sư tổ", "context": "Đường Vũ Lân chào hỏi, tôn kính Trọc Thế"},
                confidence=0.9,
            ),
            CharacterEventCandidate(
                character_original_name="Đường Vũ Lân",
                category="relationship",
                attribute_key="address_terms",
                operation="add",
                value={"with": "Sư tổ", "self": "", "other": "sư tổ", "context": "Đường Vũ Lân hành lễ với Trọc Thế sau khi tỉnh lại"},
                confidence=0.9,
            ),
        ],
    )

    # Snapshot at Chapter 250
    snap = service.snapshot(edition_id=edition_id, local_chapter_index=250)
    vu_lan = next(c for c in snap.characters if c.original_name == "Đường Vũ Lân")
    terms = vu_lan.attributes.get("address_terms", [])

    with_names = [t.get("with") for t in terms]

    # 1. Verify that kinship names are resolved to real names
    assert "Đường Tư Nhiên" in with_names
    assert "Lang Nguyệt" in with_names
    assert "Mang Thiên" in with_names
    assert "Trọc Thế" in with_names

    # 2. Verify that there are NO unresolved generic kinship cards
    assert "phụ thân" not in with_names
    assert "mẫu thân" not in with_names
    assert "Sư tổ" not in with_names

    # 3. Verify Trọc Thế was merged into ONE card with merged context
    troc_the_terms = [t for t in terms if t.get("with") == "Trọc Thế"]
    assert len(troc_the_terms) == 1
    troc_the = troc_the_terms[0]
    assert "Sư tổ" in troc_the.get("other", "")
    assert "chào hỏi" in troc_the.get("context", "")
    assert "hành lễ" in troc_the.get("context", "")
