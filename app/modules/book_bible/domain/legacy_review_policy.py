import hashlib
from typing import Optional, Tuple

from app.schemas.book_bible import (
    AddressObservation,
    AddressObservationCandidate,
    BookBible,
    BookBibleDelta,
    PendingBibleChange,
)
from app.modules.book_bible.application.facade import BookBibleService
from app.modules.book_bible.domain.address_term_policy import (
    filter_valid_address_terms,
    is_valid_address_observation,
)


class HybridPolicyEngine:
    """Phan loai quan sat LLM thanh confirmed hoac pending, khong xoa rule cu."""

    def _find_character(self, bible: BookBible, original_name: str):
        key = BookBibleService._key(original_name)
        for character in bible.characters:
            candidates = [character.original_name, character.vi_name, *character.aliases]
            if any(BookBibleService._key(item) == key for item in candidates if item):
                return character
        return None

    def _find_counterpart_id(self, bible: BookBible, original_name: Optional[str]) -> Optional[str]:
        if not original_name:
            return None
        character = self._find_character(bible, original_name)
        return character.character_id if character else None

    @staticmethod
    def _observation_id(
        novel_id: str,
        chapter_id: str,
        chunk_id: str,
        character_id: str,
        counterpart: str,
        self_term: str,
        other_term: str,
    ) -> str:
        raw = "|".join(
            [novel_id, chapter_id, chunk_id, character_id, counterpart, self_term, other_term]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _change_id(observation_id: str) -> str:
        return hashlib.sha256(f"pending:{observation_id}".encode("utf-8")).hexdigest()[:24]

    def apply_delta(
        self,
        bible: BookBible,
        delta: BookBibleDelta,
        chapter_index: Optional[int],
        chapter_id: str,
        chunk_id: str,
    ) -> Tuple[BookBible, list[str]]:
        # Normalize legacy extraction fields into dated observations before merge.
        # Explicit candidates retain their confidence/review requirements.
        delta = delta.model_copy(deep=True)

        def identity_key(value: str) -> str:
            key = BookBibleService._key(value)
            for character in [*bible.characters, *delta.new_characters]:
                names = [character.original_name, character.vi_name, *character.aliases]
                if any(BookBibleService._key(name) == key for name in names if name):
                    return character.character_id or BookBibleService._key(character.original_name)
            return key

        address_updates = [(c.original_name, c.address_terms) for c in delta.new_characters]
        address_updates += [(u.character_original_name, u.address_terms)
                            for u in delta.new_address_terms_for_existing]
        for name, terms in address_updates:
            for term in terms:
                if not any(
                    identity_key(c.character_original_name) == identity_key(name)
                    and identity_key(term.with_person) in {
                        identity_key(c.counterpart_original_name or ""),
                        identity_key(c.counterpart_text),
                    }
                    and c.self_term == term.self_term and c.other_term == term.other_term
                    for c in delta.address_observations
                ):
                    delta.address_observations.append(AddressObservationCandidate(
                        character_original_name=name,
                        counterpart_original_name=term.with_person,
                        counterpart_text=term.with_person,
                        self_term=term.self_term,
                        other_term=term.other_term,
                        context=term.context,
                        confidence=1.0,
                    ))
        for character in delta.new_characters:
            character.address_terms = []
        delta.new_address_terms_for_existing = []
        previous_ids = {c.change_id for c in bible.pending_changes}
        bible = BookBibleService.merge_delta(bible, delta, chapter_index=chapter_index)
        pending_ids = [c.change_id for c in bible.pending_changes
                       if c.status == "pending" and c.change_id not in previous_ids]

        for candidate in delta.address_observations:
            character = self._find_character(bible, candidate.character_original_name)
            if not character:
                continue
            if not is_valid_address_observation(candidate):
                continue
            counterpart_id = self._find_counterpart_id(
                bible, candidate.counterpart_original_name or candidate.counterpart_text
            )
            counterpart_key = counterpart_id or BookBibleService._key(
                candidate.counterpart_text
            )
            observation_id = self._observation_id(
                bible.novel_id,
                chapter_id,
                chunk_id,
                character.character_id,
                counterpart_key,
                candidate.self_term,
                candidate.other_term,
            )
            resolution = "confirmed" if candidate.confidence >= 0.9 else "pending"
            observation = AddressObservation(
                observation_id=observation_id,
                character_id=character.character_id,
                counterpart_id=counterpart_id,
                counterpart_text=candidate.counterpart_text
                or candidate.counterpart_original_name
                or "",
                self_term=candidate.self_term,
                other_term=candidate.other_term,
                context=candidate.context,
                chapter_index=chapter_index,
                chapter_id=chapter_id,
                chunk_id=chunk_id,
                evidence=candidate.evidence,
                confidence=max(0.0, min(1.0, candidate.confidence)),
                change_type=candidate.change_type,
                resolution=resolution,
                explicit_transition=candidate.explicit_transition,
            )
            existing = next(
                (item for item in bible.address_observations if item.observation_id == observation_id),
                None,
            )
            if existing is None:
                bible.address_observations.append(observation)
            if resolution == "pending":
                change_id = self._change_id(observation_id)
                if not any(item.change_id == change_id for item in bible.pending_changes):
                    bible.pending_changes.append(
                        PendingBibleChange(
                            change_id=change_id,
                            observation_id=observation_id,
                            change_type="address_conflict",
                            target_id=character.character_id,
                            proposed_value=f"{candidate.self_term}/{candidate.other_term}",
                            evidence=candidate.evidence,
                            confidence=candidate.confidence,
                            chapter_index=chapter_index,
                        )
                    )
                pending_ids.append(change_id)
        # Preserve the compatibility view only after its timeline metadata exists.
        for name, terms in address_updates:
            character = self._find_character(bible, name)
            if character:
                for term in filter_valid_address_terms(terms):
                    if term not in character.address_terms:
                        character.address_terms.append(term)
        bible.bible_revision += 1
        return bible, pending_ids



