from typing import Dict, List, Optional, Tuple

from app.schemas.book_bible import AddressObservation, AddressTerm, BookBible
from app.modules.book_bible.application.facade import BookBibleService
from app.modules.book_bible.domain.address_term_policy import (
    contains_cjk,
    is_valid_address_observation,
)


class AddressRuleResolver:
    """Chon quy tac xung ho hop le tai mot chapter, khong doc du lieu tuong lai."""

    @staticmethod
    def _chapter_allowed(observation: AddressObservation, chapter_index: Optional[int]) -> bool:
        if chapter_index is None or observation.chapter_index is None:
            return True
        return observation.chapter_index <= chapter_index

    @staticmethod
    def _find_character_id(bible: BookBible, value: str) -> Optional[str]:
        value_key = BookBibleService._key(value)
        if not value_key:
            return None
        for character in bible.characters:
            candidates = [character.original_name, character.vi_name, *character.aliases]
            if any(BookBibleService._key(item) == value_key for item in candidates if item):
                return character.character_id
        return None

    @classmethod
    def _group_key(cls, bible: BookBible, observation: AddressObservation) -> Tuple[str, str]:
        counterpart = (
            observation.counterpart_id
            or cls._find_character_id(bible, observation.counterpart_text)
            or BookBibleService._key(observation.counterpart_text)
        )
        return observation.character_id, counterpart

    @staticmethod
    def _rank(observation: AddressObservation) -> Tuple[int, int, str]:
        resolution_rank = {"confirmed": 3, "inferred": 2, "legacy": 1}
        chapter = observation.chapter_index if observation.chapter_index is not None else -1
        return resolution_rank.get(observation.resolution, 0), chapter, observation.observation_id

    @classmethod
    def resolve(
        cls, bible: BookBible, chapter_index: Optional[int] = None
    ) -> Dict[str, object]:
        BookBibleService.ensure_timeline(bible)
        candidates = [
            observation
            for observation in bible.address_observations
            if observation.resolution not in {"pending", "rejected"}
            and is_valid_address_observation(observation)
            and cls._chapter_allowed(observation, chapter_index)
        ]
        grouped: Dict[Tuple[str, str], AddressObservation] = {}
        for observation in candidates:
            key = cls._group_key(bible, observation)
            current = grouped.get(key)
            if current is None or cls._rank(observation) > cls._rank(current):
                grouped[key] = observation
        active = list(grouped.values())
        active.sort(key=lambda item: item.observation_id)
        return {
            "active_observations": active,
            "applied_observation_ids": [item.observation_id for item in active],
            "pending_change_ids": [
                item.change_id
                for item in bible.pending_changes
                if item.status == "pending"
                and (
                    chapter_index is None
                    or item.chapter_index is None
                    or item.chapter_index <= chapter_index
                )
            ],
            "has_uncertainty": bool(
                bible.pending_changes
                and any(
                    item.status == "pending"
                    and (
                        chapter_index is None
                        or item.chapter_index is None
                        or item.chapter_index <= chapter_index
                    )
                    for item in bible.pending_changes
                )
            ),
        }

    @classmethod
    def apply(cls, bible: BookBible, chapter_index: Optional[int] = None) -> BookBible:
        result = bible.model_copy(deep=True)
        resolution = cls.resolve(result, chapter_index)
        active: List[AddressObservation] = resolution["active_observations"]  # type: ignore[assignment]
        result.address_observations = [item.model_copy(deep=True) for item in active]
        result.pending_changes = [
            item.model_copy(deep=True)
            for item in result.pending_changes
            if chapter_index is None
            or item.chapter_index is None
            or item.chapter_index <= chapter_index
        ]
        characters_by_id = {character.character_id: character for character in result.characters}
        for character in result.characters:
            character.address_terms = []
        terms_by_character: Dict[str, List[AddressTerm]] = {}
        for observation in active:
            counterpart = characters_by_id.get(observation.counterpart_id or "")
            counterpart_candidates = (
                [counterpart.vi_name, counterpart.original_name]
                if counterpart
                else [observation.counterpart_text]
            )
            counterpart_name = next(
                (
                    candidate.strip()
                    for candidate in counterpart_candidates
                    if candidate and candidate.strip() and not contains_cjk(candidate)
                ),
                "đối phương",
            )
            terms_by_character.setdefault(observation.character_id, []).append(
                AddressTerm(
                    **{
                        "with": counterpart_name or observation.counterpart_id or "unknown",
                        "self": observation.self_term,
                        "other": observation.other_term,
                        "context": observation.context,
                    }
                )
            )
        for character in result.characters:
            if character.character_id in terms_by_character:
                character.address_terms = terms_by_character[character.character_id]
        return result

