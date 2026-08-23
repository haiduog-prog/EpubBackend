"""Backward-compatible imports for the character profiles bounded context."""

from app.modules.character_profiles.legacy_service import (
    CharacterProfileService,
    _canonicalize_attribute_info,
    _clean_title,
    _hash,
    _merge_address_term_list,
    _norm,
    _slugify,
    _token_similarity,
    candidates_from_legacy_bible,
)

__all__ = [
    "CharacterProfileService",
    "candidates_from_legacy_bible",
    "_canonicalize_attribute_info",
    "_clean_title",
    "_hash",
    "_merge_address_term_list",
    "_norm",
    "_slugify",
    "_token_similarity",
]
