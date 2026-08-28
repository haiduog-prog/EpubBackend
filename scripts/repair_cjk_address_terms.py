"""Audit and repair CJK address terms in persisted Book Bibles.

Usage:
    python scripts/repair_cjk_address_terms.py
    python scripts/repair_cjk_address_terms.py --apply

The default mode is a read-only dry run. ``--apply`` replaces only changed
Bible documents, increments their revision, and removes related direct-text
cache entries so old translations cannot be reused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.infrastructure.cache.direct_translation import DirectTranslationCache
from app.infrastructure.storage.facade import storage_repo
from app.modules.book_bible.domain.address_term_policy import (
    contains_cjk,
    is_valid_address_term,
)


def repair_bible(bible) -> tuple[Any, dict[str, int], bool]:
    repaired = bible.model_copy(deep=True)
    removed_terms = 0
    rejected_observations = 0

    for character in repaired.characters:
        valid_terms = [term for term in character.address_terms if is_valid_address_term(term)]
        removed_terms += len(character.address_terms) - len(valid_terms)
        character.address_terms = valid_terms

    for observation in repaired.address_observations:
        if contains_cjk(observation.self_term) or contains_cjk(observation.other_term):
            if observation.resolution != "rejected":
                observation.resolution = "rejected"
                rejected_observations += 1

    changed = removed_terms > 0 or rejected_observations > 0
    if changed:
        repaired.bible_revision += 1
    return repaired, {
        "removed_address_terms": removed_terms,
        "rejected_observations": rejected_observations,
    }, changed


def invalidate_related_cache(novel_ids: set[str], apply: bool) -> int:
    cache = DirectTranslationCache()
    invalidated = 0
    for path in cache.cache_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        cached_bible = payload.get("book_bible") or {}
        if cached_bible.get("novel_id") not in novel_ids:
            continue
        invalidated += 1
        if apply:
            path.unlink(missing_ok=True)
    return invalidated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="ghi thay doi vao Book Bible va xoa cache lien quan",
    )
    args = parser.parse_args()

    summaries: list[dict[str, Any]] = []
    changed_novels: set[str] = set()
    for novel_id, bible in storage_repo.list_bibles().items():
        repaired, counts, changed = repair_bible(bible)
        if not changed:
            continue
        changed_novels.add(novel_id)
        summary = {"novel_id": novel_id, **counts, "new_revision": repaired.bible_revision}
        summaries.append(summary)
        if args.apply:
            storage_repo.save_bible(novel_id, repaired, replace=True)

    cache_count = invalidate_related_cache(changed_novels, apply=args.apply)
    result = {
        "mode": "apply" if args.apply else "dry-run",
        "changed_bibles": summaries,
        "related_cache_entries": cache_count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
