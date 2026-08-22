import hashlib
import re
from typing import Dict, Optional

from app.schemas.book_bible import (
    AddressObservation,
    BookBible,
    BookBibleDelta,
    CharacterEntry,
    PlaceEntry,
    TermEntry,
)


def _is_cjk(text: str) -> bool:
    """Kiểm tra chuỗi có chứa ký tự chữ Hán (Chinese/CJK) hay không."""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


class BookBibleService:
    """Deterministic canonical merge plus legacy identity migration."""

    @staticmethod
    def _key(value: str) -> str:
        return " ".join((value or "").casefold().split())

    @staticmethod
    def character_id(novel_id: str, original_name: str) -> str:
        raw = f"{novel_id}:{BookBibleService._key(original_name)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def ensure_timeline(bible: BookBible) -> BookBible:
        for character in bible.characters:
            if not character.character_id:
                character.character_id = BookBibleService.character_id(
                    bible.novel_id, character.original_name
                )
        existing_ids = {item.observation_id for item in bible.address_observations}
        for character in bible.characters:
            for index, term in enumerate(character.address_terms):
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
                        source="legacy",
                        confidence=1.0,
                    )
                )
                existing_ids.add(observation_id)
        bible.schema_version = max(bible.schema_version, 2)
        return bible

    @staticmethod
    def merge_delta(bible: BookBible, delta: BookBibleDelta) -> BookBible:
        BookBibleService.ensure_timeline(bible)
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
            name_key = BookBibleService._key(new_char.original_name)
            vi_key = BookBibleService._key(new_char.vi_name)

            # Match via original_name, alias, or vi_name
            existing = (
                char_map.get(name_key)
                or alias_map.get(name_key)
                or (vi_map.get(name_key) if name_key else None)
                or (vi_map.get(vi_key) if vi_key else None)
                or (char_map.get(vi_key) if vi_key else None)
            )

            if existing:
                # If new_char has Chinese CJK characters and existing does not,
                # upgrade existing.original_name to the CJK version and preserve old name as alias.
                if _is_cjk(new_char.original_name) and not _is_cjk(existing.original_name):
                    if (
                        existing.original_name
                        and existing.original_name not in existing.aliases
                        and existing.original_name != new_char.original_name
                    ):
                        existing.aliases.append(existing.original_name)
                        alias_map[BookBibleService._key(existing.original_name)] = existing
                    existing.original_name = new_char.original_name
                    char_map[name_key] = existing
                elif not _is_cjk(new_char.original_name) and _is_cjk(existing.original_name):
                    if (
                        new_char.original_name
                        and new_char.original_name not in existing.aliases
                        and new_char.original_name != existing.original_name
                    ):
                        existing.aliases.append(new_char.original_name)
                        alias_map[name_key] = existing

                if new_char.vi_name and not existing.vi_name:
                    existing.vi_name = new_char.vi_name
                    if vi_key:
                        vi_map[vi_key] = existing
                if new_char.role and not existing.role:
                    existing.role = new_char.role
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
                for address_term in new_char.address_terms:
                    if address_term not in existing.address_terms:
                        existing.address_terms.append(address_term)
            else:
                new_char.character_id = new_char.character_id or BookBibleService.character_id(
                    bible.novel_id, new_char.original_name
                )
                char_map[name_key] = new_char
                if vi_key:
                    vi_map[vi_key] = new_char
                bible.characters.append(new_char)
                for alias in new_char.aliases:
                    if alias:
                        alias_map[BookBibleService._key(alias)] = new_char

        for update in delta.new_address_terms_for_existing:
            name_key = BookBibleService._key(update.character_original_name)
            target_char = char_map.get(name_key) or alias_map.get(name_key) or vi_map.get(name_key)
            if target_char:
                for address_term in update.address_terms:
                    if address_term not in target_char.address_terms:
                        target_char.address_terms.append(address_term)

        place_map: Dict[str, PlaceEntry] = {
            BookBibleService._key(place.original_name): place for place in bible.places
        }
        place_vi_map: Dict[str, PlaceEntry] = {
            BookBibleService._key(place.vi_name): place for place in bible.places if place.vi_name
        }
        for new_place in delta.new_places:
            name_key = BookBibleService._key(new_place.original_name)
            vi_key = BookBibleService._key(new_place.vi_name)
            existing_place = place_map.get(name_key) or (place_vi_map.get(vi_key) if vi_key else None)
            if existing_place:
                if _is_cjk(new_place.original_name) and not _is_cjk(existing_place.original_name):
                    existing_place.original_name = new_place.original_name
                    place_map[name_key] = existing_place
                if new_place.vi_name and not existing_place.vi_name:
                    existing_place.vi_name = new_place.vi_name
                    if vi_key:
                        place_vi_map[vi_key] = existing_place
                if new_place.notes:
                    if existing_place.notes and new_place.notes not in existing_place.notes:
                        existing_place.notes = f"{existing_place.notes}; {new_place.notes}"
                    elif not existing_place.notes:
                        existing_place.notes = new_place.notes
            else:
                place_map[name_key] = new_place
                if vi_key:
                    place_vi_map[vi_key] = new_place
                bible.places.append(new_place)

        term_map: Dict[str, TermEntry] = {
            BookBibleService._key(term.original_name): term for term in bible.terms
        }
        term_vi_map: Dict[str, TermEntry] = {
            BookBibleService._key(term.vi_name): term for term in bible.terms if term.vi_name
        }
        for new_term in delta.new_terms:
            name_key = BookBibleService._key(new_term.original_name)
            vi_key = BookBibleService._key(new_term.vi_name)
            existing_term = term_map.get(name_key) or (term_vi_map.get(vi_key) if vi_key else None)
            if existing_term:
                if _is_cjk(new_term.original_name) and not _is_cjk(existing_term.original_name):
                    existing_term.original_name = new_term.original_name
                    term_map[name_key] = existing_term
                if new_term.vi_name and not existing_term.vi_name:
                    existing_term.vi_name = new_term.vi_name
                    if vi_key:
                        term_vi_map[vi_key] = existing_term
                if new_term.category and not existing_term.category:
                    existing_term.category = new_term.category
                if new_term.notes:
                    if existing_term.notes and new_term.notes not in existing_term.notes:
                        existing_term.notes = f"{existing_term.notes}; {new_term.notes}"
                    elif not existing_term.notes:
                        existing_term.notes = new_term.notes
            else:
                term_map[name_key] = new_term
                if vi_key:
                    term_vi_map[vi_key] = new_term
                bible.terms.append(new_term)

        if delta.style_guide:
            if delta.style_guide.genre and not bible.style_guide.genre:
                bible.style_guide.genre = delta.style_guide.genre
            if delta.style_guide.tone and not bible.style_guide.tone:
                bible.style_guide.tone = delta.style_guide.tone
            if delta.style_guide.era_setting and not bible.style_guide.era_setting:
                bible.style_guide.era_setting = delta.style_guide.era_setting
        return BookBibleService.ensure_timeline(bible)

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
        return "\\n".join(lines) if lines else "(empty)"

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
            if BookBibleService._key(term.original_name) in text_key
            or (term.vi_name and BookBibleService._key(term.vi_name) in text_key)
        ]
        visible_ids = {item.character_id for item in filtered_chars}
        observations = [
            item for item in bible.address_observations if item.character_id in visible_ids
        ]
        return BookBible(
            novel_id=bible.novel_id,
            schema_version=bible.schema_version,
            bible_revision=bible.bible_revision,
            characters=filtered_chars,
            places=filtered_places,
            terms=filtered_terms,
            style_guide=bible.style_guide,
            address_observations=observations,
        )

    @staticmethod
    def detect_novel_id(text: str, bibles: Dict[str, BookBible]) -> Optional[str]:
        """
        Quét văn bản để tự động khớp với các bộ truyện (Book Bible) đã có trong Database.
        Trả về novel_id của bộ truyện có số lượng nhân vật/địa danh xuất hiện nhiều nhất.
        """
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



