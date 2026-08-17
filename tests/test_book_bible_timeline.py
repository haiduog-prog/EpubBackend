from app.core.storage import StorageRepository
from app.schemas.book_bible import (
    AddressObservationCandidate,
    BookBible,
    BookBibleDelta,
    CharacterEntry,
)
from app.services.address_rule_resolver import AddressRuleResolver
from app.services.book_bible_service import BookBibleService
from app.services.hybrid_policy_service import HybridPolicyEngine


def _delta(self_term: str, other_term: str, confidence: float = 0.99, explicit=False):
    return BookBibleDelta(
        address_observations=[
            AddressObservationCandidate(
                character_original_name="A",
                counterpart_text="B",
                self_term=self_term,
                other_term=other_term,
                confidence=confidence,
                evidence=f"{self_term}/{other_term}",
                explicit_transition=explicit,
                change_type="replace" if explicit else "new",
            )
        ]
    )


def _term(bible: BookBible):
    return bible.characters[0].address_terms[0]


def test_resolver_never_reads_future_chapter():
    bible = BookBible(
        novel_id="novel-1",
        characters=[CharacterEntry(original_name="A", vi_name="A")],
    )
    policy = HybridPolicyEngine()
    bible, _ = policy.apply_delta(bible, _delta("ta", "ngươi"), 50, "ch-50", "chunk-50")
    bible, _ = policy.apply_delta(
        bible, _delta("bổn tọa", "ngươi", explicit=True), 200, "ch-200", "chunk-200"
    )

    at_100 = AddressRuleResolver.apply(bible, 100)
    at_200 = AddressRuleResolver.apply(bible, 200)

    assert _term(at_100).self_term == "ta"
    assert _term(at_200).self_term == "bổn tọa"


def test_low_confidence_observation_is_pending_and_not_applied():
    bible = BookBible(
        novel_id="novel-1",
        characters=[CharacterEntry(original_name="A", vi_name="A")],
    )
    bible, pending_ids = HybridPolicyEngine().apply_delta(
        bible, _delta("đệ", "huynh", confidence=0.5), 20, "ch-20", "chunk-20"
    )

    assert pending_ids
    assert len(bible.pending_changes) == 1
    assert not AddressRuleResolver.apply(bible, 20).characters[0].address_terms


def test_legacy_address_terms_are_migrated_to_timeline():
    bible = BookBible(
        novel_id="novel-1",
        characters=[
            CharacterEntry(
                original_name="A",
                vi_name="A",
                address_terms=[
                    {"with": "B", "self": "ta", "other": "ngươi", "context": "old"}
                ],
            )
        ],
    )

    BookBibleService.ensure_timeline(bible)

    assert bible.characters[0].character_id
    assert len(bible.address_observations) == 1
    assert bible.address_observations[0].source == "legacy"


def test_review_approval_changes_observation_resolution():
    bible = BookBible(
        novel_id="novel-1",
        characters=[CharacterEntry(original_name="A", vi_name="A")],
    )
    bible, pending_ids = HybridPolicyEngine().apply_delta(
        bible, _delta("đệ", "huynh", confidence=0.5), 20, "ch-20", "chunk-20"
    )

    repo = StorageRepository.__new__(StorageRepository)
    repo._jobs = {}
    repo._bibles = {}
    repo.firebase_enabled = False
    repo.firestore_db = None
    repo.save_bible("novel-1", bible)

    updated = repo.review_pending_change("novel-1", pending_ids[0], "approved", "tester")

    assert updated is not None
    assert updated.pending_changes[0].status == "approved"
    assert updated.address_observations[0].resolution == "confirmed"

