from copy import deepcopy
from typing import Any, Dict, Union

from app.modules.book_bible.schemas import BookBible


def migrate_book_bible_dict_to_v3(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate a legacy Book Bible payload dictionary (schema v1 or v2) to schema v3.
    Preserves all existing entity mappings, timeline observations, locks, and aliases.
    """
    if not isinstance(data, dict):
        return {}

    migrated = deepcopy(data)
    schema_version = migrated.get("schema_version", 1)

    if schema_version < 3:
        migrated["schema_version"] = 3

        if "source_profile" not in migrated:
            migrated["source_profile"] = {
                "language": "zh",
                "mode": "translate",
            }

        if "scan_state" not in migrated:
            migrated["scan_state"] = {}

        # Migrate characters
        for char in migrated.get("characters", []):
            if isinstance(char, dict):
                char.setdefault("forbidden_variants", [])
                char.setdefault("narrative_term", "")
                char.setdefault("locked", False)

        # Migrate terms
        for term in migrated.get("terms", []):
            if isinstance(term, dict):
                term.setdefault("family", "")
                term.setdefault("rank_order", None)
                term.setdefault("evidence", "")
                term.setdefault("confidence", 1.0)
                term.setdefault("forbidden_variants", [])
                term.setdefault("locked", False)

        # Migrate style_guide
        style = migrated.setdefault("style_guide", {})
        if isinstance(style, dict):
            style.setdefault("genre", "")
            style.setdefault("tone", "")
            style.setdefault("era_setting", "")
            style.setdefault("source_mode", "translate")
            style.setdefault("source_language", "zh")
            style.setdefault("pronoun_policy", "ancient")
            style.setdefault("dialogue_style", "classical")
            style.setdefault("narrative_point_of_view", "third_person")
            style.setdefault("preserve_structure", True)
            style.setdefault("custom_rules", [])

    return migrated


def migrate_book_bible_to_v3(source: Union[Dict[str, Any], BookBible]) -> BookBible:
    """
    Ensure the BookBible object is fully compliant with schema v3.
    """
    if isinstance(source, BookBible):
        if source.schema_version >= 3:
            return source
        payload = source.model_dump(by_alias=True)
    else:
        payload = source

    v3_dict = migrate_book_bible_dict_to_v3(payload)
    return BookBible.model_validate(v3_dict)
