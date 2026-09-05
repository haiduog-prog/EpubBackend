from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.book_bible import router
from app.config import settings
from app.infrastructure.storage.legacy_storage import LocalStorageProvider, StorageRepository
from app.modules.book_bible import api
from app.modules.book_bible.domain.legacy_address_resolver import AddressRuleResolver
from app.modules.book_bible.domain.legacy_review_policy import HybridPolicyEngine
from app.modules.book_bible.legacy_service import BookBibleService
from app.modules.book_bible.schemas import (
    AddressObservationCandidate, AddressTerm, AddressTermUpdate,
    BookBible, BookBibleDelta, CharacterEntry, SourceProfile, StyleGuide, TermEntry,
)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "structured_storage_backend", "legacy")
    monkeypatch.setattr(settings, "structured_storage_read_source", "legacy")
    repo = StorageRepository.__new__(StorageRepository)
    repo._bibles = {}
    repo.firebase_enabled = False
    repo.firestore_db = None
    repo.local_provider = LocalStorageProvider(str(tmp_path / "storage"))
    monkeypatch.setattr(api, "storage_repo", repo)
    return repo


@pytest.mark.parametrize("new_character", [False, True])
def test_dated_legacy_delta_does_not_leak_after_save_reload(repository, new_character):
    bible = BookBible(novel_id=str(uuid4()))
    character = CharacterEntry(original_name="A", vi_name="A")
    term = AddressTerm(with_person="B", self_term="đệ", other_term="huynh")
    if new_character:
        character.address_terms = [term]
        delta = BookBibleDelta(new_characters=[character])
    else:
        bible.characters = [character]
        delta = BookBibleDelta(new_address_terms_for_existing=[
            AddressTermUpdate(character_original_name="A", address_terms=[term])
        ])
    repository.save_bible(bible.novel_id, bible)
    bible, _ = HybridPolicyEngine().apply_delta(bible, delta, 200, "ch200", "chunk200")
    assert bible.characters[0].address_terms == [term]
    # Saving a resolved/materialized Bible must not recreate undated observations.
    repository.save_bible(bible.novel_id, AddressRuleResolver.apply(bible, 200))
    repository._bibles.clear()
    saved = repository.get_bible(bible.novel_id)
    assert not AddressRuleResolver.apply(saved, 10).characters[0].address_terms
    assert AddressRuleResolver.apply(saved, 200).characters[0].address_terms[0].self_term == "đệ"
    assert all(o.chapter_index == 200 for o in saved.address_observations)


@pytest.mark.parametrize("speaker,counterpart", [("A", "B"), ("Speaker alias", "Counterpart alias")])
def test_explicit_pending_observation_is_not_confirmed_via_legacy_field(speaker, counterpart):
    bible = BookBible(characters=[
        CharacterEntry(original_name="A", vi_name="A", aliases=["Speaker alias"]),
        CharacterEntry(original_name="B", vi_name="B", aliases=["Counterpart alias"]),
    ])
    delta = BookBibleDelta(
        new_address_terms_for_existing=[AddressTermUpdate(
            character_original_name="A",
            address_terms=[AddressTerm(with_person="B", self_term="đệ", other_term="huynh")],
        )],
        address_observations=[AddressObservationCandidate(
            character_original_name=speaker, counterpart_text=counterpart,
            self_term="đệ", other_term="huynh", confidence=0.5,
        )],
    )
    bible, _ = HybridPolicyEngine().apply_delta(bible, delta, 200, "ch200", "chunk200")
    assert len(bible.address_observations) == 1
    assert not AddressRuleResolver.apply(bible, 200).characters[0].address_terms


def test_import_replaces_existing_bible_and_response_matches_reload(repository):
    novel_id = str(uuid4())
    repository.save_bible(novel_id, BookBible(
        novel_id=novel_id, bible_revision=20,
        characters=[CharacterEntry(original_name="A", vi_name="Old", locked=True)],
        terms=[TermEntry(original_name="Removed", vi_name="Removed")],
        style_guide=StyleGuide(tone="old tone"),
    ))
    incoming = BookBible(
        characters=[CharacterEntry(original_name="A", vi_name="New")],
        source_profile=SourceProfile(mode="post_edit"),
        style_guide=StyleGuide(tone="new tone"),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[api.require_write_access] = lambda: None
    with TestClient(app) as client:
        response = client.post(f"/book-bible/{novel_id}/import/json", json=incoming.model_dump())
        assert response.status_code == 200
        repository._bibles.clear()
        loaded = client.get(f"/book-bible/{novel_id}").json()
    assert loaded == response.json()
    assert loaded["bible_revision"] == 21
    assert loaded["characters"][0]["vi_name"] == "New"
    assert loaded["characters"][0]["locked"] is False
    assert loaded["terms"] == []
    assert loaded["source_profile"]["mode"] == "post_edit"
    assert loaded["style_guide"]["tone"] == "new tone"


@pytest.mark.parametrize("kind,entry_type", [("characters", CharacterEntry), ("terms", TermEntry)])
def test_approved_name_is_not_forbidden_after_reload(repository, kind, entry_type):
    bible = BookBible(novel_id=str(uuid4()), **{
        kind: [entry_type(original_name="A", vi_name="Old", locked=True,
                          forbidden_variants=[" NEW ", "Unrelated"])]
    })
    delta = BookBibleDelta(**{"new_" + kind: [entry_type(original_name="A", vi_name="New")]})
    bible, _ = HybridPolicyEngine().apply_delta(bible, delta, 1, "ch1", "chunk1")
    assert len(bible.pending_changes) == 1
    repository.save_bible(bible.novel_id, bible)
    repository.review_pending_change(bible.novel_id, bible.pending_changes[0].change_id, "approved")
    repository._bibles.clear()
    saved = repository.get_bible(bible.novel_id)
    entry = getattr(saved, kind)[0]
    assert entry.vi_name == "New"
    assert entry.forbidden_variants == ["Unrelated"]
    assert "Old" in entry.aliases
    assert all(c.status == "approved" for c in saved.pending_changes)


@pytest.mark.parametrize("locked", [False, True])
def test_character_correction_has_one_pending_change_on_retry(locked):
    bible = BookBible(characters=[CharacterEntry(original_name="A", vi_name="Old", locked=locked)])
    delta = BookBibleDelta(new_characters=[CharacterEntry(original_name="A", vi_name="New")])
    policy = HybridPolicyEngine()
    bible, pending = policy.apply_delta(bible, delta, 1, "ch1", "chunk1")
    assert pending == [bible.pending_changes[0].change_id]
    bible, _ = policy.apply_delta(bible, delta, 1, "ch1", "chunk1")
    assert len(bible.pending_changes) == 1
    assert bible.characters[0].vi_name == "Old"


@pytest.mark.parametrize("text,expected", [("An ALIAS appears", 1), ("unrelated", 0), ("", 0)])
def test_term_alias_filter_keeps_canonical_mapping(text, expected):
    bible = BookBible(terms=[TermEntry(original_name="Source", vi_name="Canonical", aliases=["Alias", " "])])
    filtered = BookBibleService.filter_bible_for_text(bible, text)
    assert len(filtered.terms) == expected
    if expected:
        assert "Alias -> Canonical" in BookBibleService.get_known_names_index(filtered)
