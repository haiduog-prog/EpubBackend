"""Validation rules for address terms stored in the Book Bible."""

from __future__ import annotations

import re
from typing import Sequence

from app.schemas.book_bible import AddressObservation, AddressObservationCandidate, AddressTerm


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def contains_cjk(value: str | None) -> bool:
    return bool(_CJK_RE.search(value or ""))


def cjk_sequences(value: str | None) -> list[str]:
    return [match.group(0) for match in _CJK_RE.finditer(value or "")]


def is_valid_address_values(self_term: str | None, other_term: str | None) -> bool:
    return bool((self_term or "").strip() and (other_term or "").strip()) and not (
        contains_cjk(self_term) or contains_cjk(other_term)
    )


def is_valid_address_term(term: AddressTerm) -> bool:
    return is_valid_address_values(term.self_term, term.other_term)


def is_valid_address_observation(observation: AddressObservation | AddressObservationCandidate) -> bool:
    return is_valid_address_values(observation.self_term, observation.other_term)


def filter_valid_address_terms(terms: Sequence[AddressTerm]) -> list[AddressTerm]:
    return [term for term in terms if is_valid_address_term(term)]


__all__ = [
    "cjk_sequences",
    "contains_cjk",
    "filter_valid_address_terms",
    "is_valid_address_observation",
    "is_valid_address_term",
    "is_valid_address_values",
]
