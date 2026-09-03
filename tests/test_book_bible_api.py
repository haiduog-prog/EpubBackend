import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.infrastructure.storage.facade import storage_repo
from app.schemas.book_bible import BookBible, TermEntry, CharacterEntry, StyleGuide, SourceProfile


@pytest.fixture
def client():
    return TestClient(app)


def test_export_and_import_book_bible_json(client, tmp_path, monkeypatch):
    novel_id = "test-export-import"
    bible = BookBible(
        novel_id=novel_id,
        bible_revision=3,
        source_profile=SourceProfile(language="zh", mode="translate"),
        terms=[
            TermEntry(
                original_name="Khí Võ Cảnh",
                vi_name="Khí Võ Cảnh",
                category="realm",
                locked=True,
                family="cultivation_realm",
                rank_order=1,
            )
        ],
        characters=[
            CharacterEntry(
                character_id="c-1",
                original_name="Đỗ Phong",
                vi_name="Đỗ Phong",
                narrative_term="hắn",
                locked=True,
            )
        ],
    )
    storage_repo.save_bible(novel_id, bible)

    # 1. Export JSON
    resp = client.get(f"/api/v1/book-bible/{novel_id}/export/json")
    assert resp.status_code == 200
    assert "attachment;" in resp.headers.get("Content-Disposition", "")
    exported_data = resp.json()
    assert exported_data["novel_id"] == novel_id
    assert exported_data["schema_version"] == 3
    assert len(exported_data["terms"]) == 1
    assert exported_data["terms"][0]["vi_name"] == "Khí Võ Cảnh"

    # 2. Modify and Import JSON
    exported_data["terms"].append(
        {
            "original_name": "Ngưng Võ Cảnh",
            "vi_name": "Ngưng Võ Cảnh",
            "category": "realm",
            "locked": True,
            "family": "cultivation_realm",
            "rank_order": 2,
        }
    )
    import_resp = client.post(f"/api/v1/book-bible/{novel_id}/import/json", json=exported_data)
    assert import_resp.status_code == 200
    imported_bible = import_resp.json()
    assert len(imported_bible["terms"]) == 2
    assert imported_bible["bible_revision"] > 3


def test_upsert_term_endpoint(client):
    novel_id = "test-upsert-term"
    term_payload = {
        "original_name": "Tử Kỳ Lân",
        "vi_name": "Tử Kỳ Lân",
        "category": "creature",
        "locked": True,
        "notes": "Chiến thú thần thoại",
    }
    resp = client.put(f"/api/v1/book-bible/{novel_id}/terms", json=term_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert any(t["original_name"] == "Tử Kỳ Lân" and t["locked"] is True for t in data["terms"])


def test_upsert_character_and_style_guide(client):
    novel_id = "test-char-style"
    char_payload = {
        "original_name": "Mộc Linh",
        "vi_name": "Mộc Linh",
        "narrative_term": "nàng",
        "locked": True,
        "forbidden_variants": ["Mộc Cảnh Nam"],
    }
    resp = client.put(f"/api/v1/book-bible/{novel_id}/characters", json=char_payload)
    assert resp.status_code == 200
    assert any(c["original_name"] == "Mộc Linh" and c["narrative_term"] == "nàng" for c in resp.json()["characters"])

    style_payload = {
        "style_guide": {
            "genre": "Tiên hiệp",
            "tone": "Hào hùng",
            "era_setting": "Cổ phong",
            "pronoun_policy": "huynh_muoi",
            "source_mode": "post_edit",
        },
        "source_profile": {
            "language": "vi_machine",
            "mode": "post_edit",
        },
    }
    resp_style = client.put(f"/api/v1/book-bible/{novel_id}/style-guide", json=style_payload)
    assert resp_style.status_code == 200
    data = resp_style.json()
    assert data["style_guide"]["pronoun_policy"] == "huynh_muoi"
    assert data["style_guide"]["preserve_structure"] is True
    assert data["source_profile"]["mode"] == "post_edit"

    # Verify partial character update preserves narrative_term and forbidden_variants
    partial_char = {
        "original_name": "Mộc Linh",
        "vi_name": "Mộc Linh",
        "locked": False,
    }
    resp_char2 = client.put(f"/api/v1/book-bible/{novel_id}/characters", json=partial_char)
    assert resp_char2.status_code == 200
    ml = next(c for c in resp_char2.json()["characters"] if c["original_name"] == "Mộc Linh")
    assert ml["locked"] is False
    assert ml["narrative_term"] == "nàng"
    assert "Mộc Cảnh Nam" in ml["forbidden_variants"]
