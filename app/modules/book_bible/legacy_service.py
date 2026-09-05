import hashlib
import re
from typing import Dict, Optional

from app.schemas.book_bible import (
    AddressObservation,
    BookBible,
    BookBibleDelta,
    CharacterEntry,
    PendingBibleChange,
    PlaceEntry,
    TermEntry,
)
from app.modules.book_bible.domain.address_term_policy import (
    filter_valid_address_terms,
    is_valid_address_term,
)


def _is_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff\uf900-\ufaff]", text or ""))


class LegacyBookBibleService:
    """Deterministic canonical merge plus legacy identity migration."""

    @staticmethod
    def _key(value: str) -> str:
        return " ".join((value or "").casefold().split())

    @staticmethod
    def character_id(novel_id: str, original_name: str) -> str:
        raw = f"{novel_id}:{LegacyBookBibleService._key(original_name)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def ensure_timeline(bible: BookBible, chapter_index: Optional[int] = None) -> BookBible:
        for character in bible.characters:
            if not character.character_id:
                character.character_id = BookBibleService.character_id(
                    bible.novel_id, character.original_name
                )
        existing_ids = {item.observation_id for item in bible.address_observations}
        for character in bible.characters:
            for index, term in enumerate(character.address_terms):
                if not is_valid_address_term(term):
                    continue
                # A materialized address term may already have a dated or pending
                # observation. Never turn it back into an undated legacy rule.
                counterpart_ids = {
                    c.character_id for c in bible.characters
                    if any(BookBibleService._key(name) == BookBibleService._key(term.with_person)
                           for name in [c.original_name, c.vi_name, *c.aliases] if name)
                }
                counterpart_names = {BookBibleService._key(term.with_person)}
                for counterpart in bible.characters:
                    if counterpart.character_id in counterpart_ids:
                        counterpart_names.update(
                            BookBibleService._key(name)
                            for name in [counterpart.original_name, counterpart.vi_name, *counterpart.aliases]
                            if name
                        )
                if any(
                    observation.character_id == character.character_id
                    and observation.self_term == term.self_term
                    and observation.other_term == term.other_term
                    and (BookBibleService._key(observation.counterpart_text) in counterpart_names
                         or observation.counterpart_id in counterpart_ids)
                    for observation in bible.address_observations
                ):
                    continue
                observation_id = hashlib.sha256(
                    f"legacy:{character.character_id}:{index}:{term.with_person}:{term.self_term}:{term.other_term}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:24]
                if observation_id in existing_ids:
                    continue
                bible.address_observations.append(
                    AddressObservation(
                        observation_id=observation_id,
                        character_id=character.character_id,
                        counterpart_text=term.with_person,
                        self_term=term.self_term,
                        other_term=term.other_term,
                        context=term.context,
                        resolution="confirmed",
                        source="legacy" if chapter_index is None else "llm",
                        chapter_index=chapter_index,
                        confidence=1.0,
                    )
                )
                existing_ids.add(observation_id)
        bible.schema_version = max(bible.schema_version, 3)
        return bible

    @staticmethod
    def merge_delta(
        bible: BookBible,
        delta: BookBibleDelta,
        chapter_index: Optional[int] = None,
    ) -> BookBible:
        BookBibleService.ensure_timeline(bible)
        source_profile = getattr(delta, "source_profile", None) or getattr(bible, "source_profile", None)
        is_post_edit = bool(
            source_profile is not None
            and getattr(source_profile, "mode", None) == "post_edit"
        )

        char_map: Dict[str, CharacterEntry] = {
            BookBibleService._key(c.original_name): c for c in bible.characters
        }
        alias_map: Dict[str, CharacterEntry] = {
            BookBibleService._key(alias): c
            for c in bible.characters
            for alias in c.aliases
            if alias
        }
        vi_map: Dict[str, CharacterEntry] = {
            BookBibleService._key(c.vi_name): c
            for c in bible.characters
            if c.vi_name
        }

        for new_char in delta.new_characters:
            if new_char.first_seen_chapter is None:
                new_char.first_seen_chapter = chapter_index
            valid_address_terms = filter_valid_address_terms(new_char.address_terms)
            name_key = BookBibleService._key(new_char.original_name)
            vi_key = BookBibleService._key(new_char.vi_name)
            existing = char_map.get(name_key) or alias_map.get(name_key) or vi_map.get(vi_key)
            if existing:
                # Do not accept invented CJK without evidence in post_edit mode
                if _is_cjk(new_char.original_name) and not _is_cjk(existing.original_name):
                    if not is_post_edit:
                        if existing.original_name not in existing.aliases:
                            existing.aliases.append(existing.original_name)
                        existing.original_name = new_char.original_name

                if new_char.vi_name and not existing.vi_name:
                    existing.vi_name = new_char.vi_name
                elif (
                    new_char.vi_name
                    and existing.vi_name
                    and BookBibleService._key(new_char.vi_name)
                    != BookBibleService._key(existing.vi_name)
                ):
                    if existing.locked:
                        if new_char.vi_name not in existing.forbidden_variants:
                            existing.forbidden_variants.append(new_char.vi_name)
                    else:
                        # Character canonical vi_name is preserved, alternative name added to aliases
                        if new_char.vi_name not in existing.aliases:
                            existing.aliases.append(new_char.vi_name)

                if (new_char.vi_name and existing.vi_name
                        and BookBibleService._key(new_char.vi_name) != BookBibleService._key(existing.vi_name)):
                    change_id = hashlib.sha256(
                        f"canonical:{existing.character_id}:{new_char.vi_name}".encode("utf-8")
                    ).hexdigest()[:24]
                    if not any(c.change_id == change_id for c in bible.pending_changes):
                        bible.pending_changes.append(PendingBibleChange(
                            change_id=change_id,
                            change_type="canonical_correction",
                            target_id=existing.character_id,
                            old_value=existing.vi_name,
                            proposed_value=new_char.vi_name,
                            evidence=f"Chương {chapter_index or 0}",
                            confidence=0.8 if existing.locked else 0.0,
                            chapter_index=chapter_index,
                        ))

                if new_char.role and not existing.role:
                    existing.role = new_char.role
                if getattr(new_char, "narrative_term", "") and not getattr(existing, "narrative_term", ""):
                    existing.narrative_term = new_char.narrative_term
                if new_char.voice_notes:
                    if existing.voice_notes and new_char.voice_notes not in existing.voice_notes:
                        existing.voice_notes = f"{existing.voice_notes}; {new_char.voice_notes}"
                    elif not existing.voice_notes:
                        existing.voice_notes = new_char.voice_notes
                if name_key != BookBibleService._key(existing.original_name):
                    if new_char.original_name not in existing.aliases:
                        existing.aliases.append(new_char.original_name)
                    alias_map[name_key] = existing
                for alias in new_char.aliases:
                    if alias and alias not in existing.aliases:
                        existing.aliases.append(alias)
                    if alias:
                        alias_map[BookBibleService._key(alias)] = existing
                for variant in getattr(new_char, "forbidden_variants", []) or []:
                    if variant and variant not in existing.forbidden_variants:
                        existing.forbidden_variants.append(variant)
                if new_char.vi_name:
                    vi_map[BookBibleService._key(new_char.vi_name)] = existing
                for address_term in valid_address_terms:
                    if address_term not in existing.address_terms:
                        existing.address_terms.append(address_term)
            else:
                if is_post_edit and _is_cjk(new_char.original_name):
                    if not (getattr(new_char, "evidence", "") and _is_cjk(new_char.evidence)):
                        new_char.original_name = new_char.vi_name or new_char.original_name
                name_key = BookBibleService._key(new_char.original_name)
                new_char_to_add = new_char.model_copy(deep=True)
                new_char_to_add.original_name = new_char.original_name
                new_char_to_add.address_terms = valid_address_terms
                new_char_to_add.character_id = new_char_to_add.character_id or BookBibleService.character_id(
                    bible.novel_id, new_char.original_name
                )
                char_map[name_key] = new_char_to_add
                bible.characters.append(new_char_to_add)
                for alias in new_char_to_add.aliases:
                    if alias:
                        alias_map[BookBibleService._key(alias)] = new_char_to_add
                if new_char_to_add.vi_name:
                    vi_map[BookBibleService._key(new_char_to_add.vi_name)] = new_char_to_add

        for update in delta.new_address_terms_for_existing:
            name_key = BookBibleService._key(update.character_original_name)
            target_char = char_map.get(name_key) or alias_map.get(name_key) or vi_map.get(name_key)
            if target_char:
                for address_term in update.address_terms:
                    if not is_valid_address_term(address_term):
                        continue
                    if address_term not in target_char.address_terms:
                        target_char.address_terms.append(address_term)

        place_map: Dict[str, PlaceEntry] = {
            BookBibleService._key(place.original_name): place for place in bible.places
        }
        place_vi_map: Dict[str, PlaceEntry] = {
            BookBibleService._key(place.vi_name): place
            for place in bible.places
            if place.vi_name
        }
        for new_place in delta.new_places:
            if new_place.first_seen_chapter is None:
                new_place.first_seen_chapter = chapter_index
            name_key = BookBibleService._key(new_place.original_name)
            vi_key = BookBibleService._key(new_place.vi_name)
            existing = place_map.get(name_key) or place_vi_map.get(vi_key)
            if existing:
                if _is_cjk(new_place.original_name) and not _is_cjk(existing.original_name):
                    if not is_post_edit:
                        existing.original_name = new_place.original_name
                if new_place.vi_name and not existing.vi_name:
                    existing.vi_name = new_place.vi_name
                if new_place.notes:
                    if existing.notes and new_place.notes not in existing.notes:
                        existing.notes = f"{existing.notes}; {new_place.notes}"
                    elif not existing.notes:
                        existing.notes = new_place.notes
                if new_place.vi_name:
                    place_vi_map[BookBibleService._key(new_place.vi_name)] = existing
            else:
                place_map[name_key] = new_place
                bible.places.append(new_place)
                if new_place.vi_name:
                    place_vi_map[BookBibleService._key(new_place.vi_name)] = new_place

        term_map: Dict[str, TermEntry] = {
            BookBibleService._key(term.original_name): term for term in bible.terms
        }
        term_alias_map: Dict[str, TermEntry] = {
            BookBibleService._key(alias): term
            for term in bible.terms
            for alias in getattr(term, "aliases", [])
            if alias
        }
        term_vi_map: Dict[str, TermEntry] = {
            BookBibleService._key(term.vi_name): term
            for term in bible.terms
            if term.vi_name
        }
        for new_term in delta.new_terms:
            if new_term.first_seen_chapter is None:
                new_term.first_seen_chapter = chapter_index
            name_key = BookBibleService._key(new_term.original_name)
            vi_key = BookBibleService._key(new_term.vi_name)
            existing = term_map.get(name_key) or term_alias_map.get(name_key) or term_vi_map.get(vi_key)
            if existing:
                # Do not accept invented CJK without evidence in post_edit mode
                if _is_cjk(new_term.original_name) and not _is_cjk(existing.original_name):
                    if is_post_edit:
                        if getattr(new_term, "evidence", "") and _is_cjk(new_term.evidence):
                            existing.original_name = new_term.original_name
                    else:
                        existing.original_name = new_term.original_name

                if new_term.vi_name and not existing.vi_name:
                    existing.vi_name = new_term.vi_name
                elif (
                    new_term.vi_name
                    and existing.vi_name
                    and BookBibleService._key(new_term.vi_name)
                    != BookBibleService._key(existing.vi_name)
                ):
                    if existing.locked:
                        if new_term.vi_name not in existing.forbidden_variants:
                            existing.forbidden_variants.append(new_term.vi_name)
                        change_id = f"change-term-{BookBibleService._key(existing.original_name)}-{chapter_index or 0}"
                        if not any(c.change_id == change_id for c in bible.pending_changes):
                            bible.pending_changes.append(
                                PendingBibleChange(
                                    change_id=change_id,
                                    change_type="canonical_correction",
                                    target_id=existing.original_name,
                                    old_value=existing.vi_name,
                                    proposed_value=new_term.vi_name,
                                    evidence=getattr(new_term, "evidence", "") or f"Chương {chapter_index or 0}",
                                    confidence=getattr(new_term, "confidence", 0.8),
                                    chapter_index=chapter_index,
                                    status="pending",
                                )
                            )
                    else:
                        old_name = existing.vi_name
                        if old_name and old_name not in existing.aliases:
                            existing.aliases.append(old_name)
                        existing.vi_name = new_term.vi_name

                for alias in new_term.aliases:
                    if alias and alias not in existing.aliases:
                        existing.aliases.append(alias)
                    if alias:
                        term_alias_map[BookBibleService._key(alias)] = existing
                for variant in new_term.forbidden_variants:
                    if variant and variant not in existing.forbidden_variants:
                        existing.forbidden_variants.append(variant)
                if new_term.category and not existing.category:
                    existing.category = new_term.category
                if getattr(new_term, "family", "") and not getattr(existing, "family", ""):
                    existing.family = new_term.family
                if getattr(new_term, "rank_order", None) is not None and getattr(existing, "rank_order", None) is None:
                    existing.rank_order = new_term.rank_order
                if getattr(new_term, "evidence", ""):
                    existing.evidence = (
                        f"{existing.evidence}; {new_term.evidence}"
                        if getattr(existing, "evidence", "")
                        else new_term.evidence
                    )
                if new_term.notes:
                    if existing.notes and new_term.notes not in existing.notes:
                        existing.notes = f"{existing.notes}; {new_term.notes}"
                    elif not existing.notes:
                        existing.notes = new_term.notes
                if new_term.vi_name:
                    term_vi_map[BookBibleService._key(new_term.vi_name)] = existing
            else:
                if is_post_edit and _is_cjk(new_term.original_name):
                    if not (getattr(new_term, "evidence", "") and _is_cjk(new_term.evidence)):
                        new_term.original_name = new_term.vi_name or new_term.original_name
                name_key = BookBibleService._key(new_term.original_name)
                term_map[name_key] = new_term
                bible.terms.append(new_term)
                for alias in new_term.aliases:
                    if alias:
                        term_alias_map[BookBibleService._key(alias)] = new_term
                if new_term.vi_name:
                    term_vi_map[BookBibleService._key(new_term.vi_name)] = new_term

        if delta.style_guide:
            if delta.style_guide.genre and not bible.style_guide.genre:
                bible.style_guide.genre = delta.style_guide.genre
            if delta.style_guide.tone and not bible.style_guide.tone:
                bible.style_guide.tone = delta.style_guide.tone
            if delta.style_guide.era_setting and not bible.style_guide.era_setting:
                bible.style_guide.era_setting = delta.style_guide.era_setting
            if delta.style_guide.pronoun_policy and not bible.style_guide.pronoun_policy:
                bible.style_guide.pronoun_policy = delta.style_guide.pronoun_policy
        if delta.source_profile and getattr(bible, "source_profile", None):
            if delta.source_profile.mode:
                bible.source_profile.mode = delta.source_profile.mode
            if delta.source_profile.language:
                bible.source_profile.language = delta.source_profile.language
        return BookBibleService.ensure_timeline(bible, chapter_index)

    @staticmethod
    def get_known_names_index(bible: BookBible) -> str:
        BookBibleService.ensure_timeline(bible)
        lines = []
        for character in bible.characters:
            lines.append(f"{character.original_name} -> {character.vi_name}")
            for alias in character.aliases:
                if alias:
                    lines.append(f"{alias} -> {character.vi_name} (alias of {character.original_name})")
        for place in bible.places:
            lines.append(f"{place.original_name} -> {place.vi_name}")
        for term in bible.terms:
            lines.append(f"{term.original_name} -> {term.vi_name}")
            for alias in getattr(term, "aliases", []) or []:
                if alias:
                    lines.append(f"{alias} -> {term.vi_name} (alias of {term.original_name})")
            for forbidden in getattr(term, "forbidden_variants", []) or []:
                if forbidden:
                    lines.append(f"DO NOT USE: {forbidden}; use {term.vi_name}")
        return "\n".join(lines) if lines else "(empty)"

    @staticmethod
    def _character_matches_text(character: CharacterEntry, text_key: str) -> bool:
        candidates = [character.original_name, character.vi_name, *character.aliases]
        return any(candidate and BookBibleService._key(candidate) in text_key for candidate in candidates)

    @staticmethod
    def filter_bible_for_text(bible: BookBible, chunk_text: str) -> BookBible:
        BookBibleService.ensure_timeline(bible)
        text_key = BookBibleService._key(chunk_text)
        filtered_chars = [
            character for character in bible.characters
            if BookBibleService._character_matches_text(character, text_key)
        ]
        filtered_places = [
            place for place in bible.places
            if BookBibleService._key(place.original_name) in text_key
            or (place.vi_name and BookBibleService._key(place.vi_name) in text_key)
        ]
        filtered_terms = [
            term for term in bible.terms
            if any(
                BookBibleService._key(name) and BookBibleService._key(name) in text_key
                for name in [term.original_name, term.vi_name, *term.aliases]
            )
        ]
        visible_ids = {item.character_id for item in filtered_chars}
        observations = [
            item for item in bible.address_observations if item.character_id in visible_ids
        ]
        return BookBible(
            novel_id=bible.novel_id,
            schema_version=bible.schema_version,
            bible_revision=bible.bible_revision,
            source_profile=getattr(bible, "source_profile", None) or getattr(bible, "source_profile", None),
            scan_state=getattr(bible, "scan_state", {}) or {},
            characters=filtered_chars,
            places=filtered_places,
            terms=filtered_terms,
            style_guide=bible.style_guide,
            address_observations=observations,
        )

    @staticmethod
    def detect_novel_id(text: str, bibles: Dict[str, BookBible]) -> Optional[str]:
        text_key = BookBibleService._key(text)
        best_novel_id = None
        max_score = 0

        for novel_id, bible in bibles.items():
            score = 0
            for character in bible.characters:
                if BookBibleService._character_matches_text(character, text_key):
                    score += 2
            for place in bible.places:
                if BookBibleService._key(place.original_name) in text_key or (place.vi_name and BookBibleService._key(place.vi_name) in text_key):
                    score += 1
            for term in bible.terms:
                if BookBibleService._key(term.original_name) in text_key or (term.vi_name and BookBibleService._key(term.vi_name) in text_key):
                    score += 1
            
            if score > max_score:
                max_score = score
                best_novel_id = novel_id

        if max_score >= 1:
            return best_novel_id
        return None


# Internal compatibility alias while callers migrate to the bounded context.
BookBibleService = LegacyBookBibleService
