"""Backfill and lock canonical entities for Book Bible schema v3 (dải chương 1-154).

Usage:
    python scripts/backfill_book_bible.py --novel-id van-thu-chien-than
    python scripts/backfill_book_bible.py --novel-id van-thu-chien-than --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.infrastructure.storage.facade import storage_repo
from app.modules.book_bible.domain.schema_migration import migrate_book_bible_to_v3
from app.modules.book_bible.legacy_service import LegacyBookBibleService
from app.schemas.book_bible import (
    BookBible,
    CharacterEntry,
    PlaceEntry,
    SourceProfile,
    StyleGuide,
    TermEntry,
)


def seed_canonical_terms() -> list[TermEntry]:
    return [
        # Cultivation Realms (Strict Rank Ordering)
        TermEntry(
            original_name="Khí Võ Cảnh",
            vi_name="Khí Võ Cảnh",
            category="realm",
            locked=True,
            family="cultivation_realm",
            rank_order=1,
            forbidden_variants=["Tụ Võ Cảnh", "Tụ Võ cảnh"],
            notes="Cảnh giới khởi đầu tu luyện võ đạo",
        ),
        TermEntry(
            original_name="Ngưng Võ Cảnh",
            vi_name="Ngưng Võ Cảnh",
            category="realm",
            locked=True,
            family="cultivation_realm",
            rank_order=2,
        ),
        TermEntry(
            original_name="Linh Võ Cảnh",
            vi_name="Linh Võ Cảnh",
            category="realm",
            locked=True,
            family="cultivation_realm",
            rank_order=3,
        ),
        TermEntry(
            original_name="Thiên Võ Cảnh",
            vi_name="Thiên Võ Cảnh",
            category="realm",
            locked=True,
            family="cultivation_realm",
            rank_order=4,
        ),
        TermEntry(
            original_name="Tôn Võ Cảnh",
            vi_name="Tôn Võ Cảnh",
            category="realm",
            locked=True,
            family="cultivation_realm",
            rank_order=5,
        ),
        TermEntry(
            original_name="Vũ Hoàng",
            vi_name="Vũ Hoàng",
            category="realm",
            locked=True,
            family="cultivation_realm",
            rank_order=6,
        ),
        TermEntry(
            original_name="Đại Đế",
            vi_name="Đại Đế",
            category="realm",
            locked=True,
            family="cultivation_realm",
            rank_order=7,
        ),
        # Skills & Spells
        TermEntry(
            original_name="phi hành thuật",
            vi_name="Phi Hành Thuật",
            category="skill",
            locked=True,
            forbidden_variants=["phù thủy", "thuật phù thủy"],
            notes="Kỹ năng ngự không/bay lượn",
        ),
        TermEntry(
            original_name="Cửu Chuyển",
            vi_name="Cửu Chuyển",
            category="skill",
            locked=True,
            forbidden_variants=["Cửu Transfer", "Chuyển 9"],
            notes="Công pháp Cửu Chuyển",
        ),
        # Creatures & Items
        TermEntry(
            original_name="Huyết Lang",
            vi_name="Huyết Lang",
            category="creature",
            locked=True,
            notes="Sói máu hoang thú hung hãn",
        ),
        TermEntry(
            original_name="Thôn Thiên Thú",
            vi_name="Thôn Thiên Thú",
            category="creature",
            locked=True,
            notes="Thần thú thôn phệ trời đất",
        ),
        TermEntry(
            original_name="Bá Vương Cung",
            vi_name="Bá Vương Cung",
            category="item",
            locked=True,
            notes="Cung thần uy lực vô song",
        ),
    ]


def seed_canonical_characters(novel_id: str) -> list[CharacterEntry]:
    return [
        CharacterEntry(
            character_id=LegacyBookBibleService.character_id(novel_id, "Đỗ Phong"),
            original_name="Đỗ Phong",
            vi_name="Đỗ Phong",
            role="main",
            narrative_term="hắn",
            locked=True,
            voice_notes="Điềm đạm, quyết đoán, sát phạt quả cảm",
        ),
        CharacterEntry(
            character_id=LegacyBookBibleService.character_id(novel_id, "Mộc Linh"),
            original_name="Mộc Linh",
            vi_name="Mộc Linh",
            role="ally",
            narrative_term="nàng",
            locked=True,
            voice_notes="Nữ tử Mộc gia, thông minh nhanh nhẹn",
        ),
        CharacterEntry(
            character_id=LegacyBookBibleService.character_id(novel_id, "Mộc Cảnh Nam"),
            original_name="Mộc Cảnh Nam",
            vi_name="Mộc Cảnh Nam",
            role="ally",
            narrative_term="hắn",
            locked=True,
            voice_notes="Thiếu gia Mộc gia, bằng hữu chí cốt",
        ),
    ]


def seed_canonical_places() -> list[PlaceEntry]:
    return [
        PlaceEntry(
            original_name="Thần Mộc Tháp",
            vi_name="Thần Mộc Tháp",
            notes="Thánh địa tu luyện của Mộc gia",
        ),
        PlaceEntry(
            original_name="Đoạn Vân Lĩnh",
            vi_name="Đoạn Vân Lĩnh",
            notes="Dãy núi hiểm trở đầy yêu thú",
        ),
    ]


def backfill_novel_bible(novel_id: str, max_chapter: int = 154, apply: bool = False) -> BookBible:
    if novel_id != "van-thu-chien-than":
        raise ValueError(
            "This backfill contains canonical data for 'van-thu-chien-than' only. "
            "Provide a novel-specific seed before using another novel ID."
        )

    raw_bible = storage_repo.get_bible(novel_id)
    if not raw_bible:
        raw_bible = BookBible(novel_id=novel_id)

    bible = migrate_book_bible_to_v3(raw_bible)

    # 1. Update source profile & style guide
    bible.source_profile = SourceProfile(
        language="vi_machine",
        mode="post_edit",
        encoding="utf-8",
    )
    bible.style_guide = StyleGuide(
        genre="Huyền Huyễn",
        tone="Hào hùng, cổ phong tiên hiệp",
        era_setting="Cổ phong",
        pronoun_policy="ancient",
        source_mode="post_edit",
        forbidden_regex=[
            r"\bphù thủy\b",
            r"\bCửu Transfer\b",
            r"\bTụ Võ Cảnh\b",
        ],
    )

    # 2. Seed locked terms
    term_keys = {LegacyBookBibleService._key(t.original_name): t for t in bible.terms}
    for seed_term in seed_canonical_terms():
        key = LegacyBookBibleService._key(seed_term.original_name)
        if key in term_keys:
            existing = term_keys[key]
            existing.vi_name = seed_term.vi_name
            existing.category = seed_term.category or existing.category
            existing.locked = True
            existing.family = seed_term.family or existing.family
            existing.rank_order = seed_term.rank_order or existing.rank_order
            for fb in seed_term.forbidden_variants:
                if fb not in existing.forbidden_variants:
                    existing.forbidden_variants.append(fb)
        else:
            bible.terms.append(seed_term)

    # 3. Seed locked characters
    char_keys = {LegacyBookBibleService._key(c.original_name): c for c in bible.characters}
    for seed_char in seed_canonical_characters(novel_id):
        key = LegacyBookBibleService._key(seed_char.original_name)
        if key in char_keys:
            existing = char_keys[key]
            existing.vi_name = seed_char.vi_name
            existing.narrative_term = seed_char.narrative_term or existing.narrative_term
            existing.locked = True
            for fb in seed_char.forbidden_variants:
                if fb not in existing.forbidden_variants:
                    existing.forbidden_variants.append(fb)
        else:
            bible.characters.append(seed_char)

    # 4. Seed places
    place_keys = {LegacyBookBibleService._key(p.original_name): p for p in bible.places}
    for seed_place in seed_canonical_places():
        key = LegacyBookBibleService._key(seed_place.original_name)
        if key not in place_keys:
            bible.places.append(seed_place)

    # 5. Verify existing original chapter files up to max_chapter
    storage_root = Path("storage/novels") / novel_id / "original"
    verified_files_count = 0
    if storage_root.exists():
        for ch_idx in range(1, max_chapter + 1):
            ch_file = storage_root / f"ch_{ch_idx:04d}.txt"
            if ch_file.exists():
                verified_files_count += 1

    # 6. Update scan_state (preserve last_scanned_chapter from actual LLM extraction if present)
    existing_last_scan = raw_bible.scan_state.get("last_scanned_chapter", 0) if hasattr(raw_bible, "scan_state") and isinstance(raw_bible.scan_state, dict) else 0
    bible.scan_state = {
        "last_scanned_chapter": existing_last_scan,
        "seeded_canonical_up_to_chapter": max_chapter,
        "seed_mode": "canonical_injection",
        "verified_original_files_count": verified_files_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    bible.bible_revision += 1
    print(f"[*] Backfill stats for '{novel_id}':")
    print(f"    - Characters: {len(bible.characters)} (Main: Đỗ Phong, Mộc Linh, Mộc Cảnh Nam)")
    print(f"    - Terms: {len(bible.terms)} ({sum(1 for t in bible.terms if t.locked)} locked)")
    print(f"    - Places: {len(bible.places)}")
    print(f"    - Scan state: chapters 1 to {max_chapter} ({verified_files_count} files verified)")
    print(f"    - Source Mode: {bible.source_profile.mode} | Pronoun Policy: {bible.style_guide.pronoun_policy}")

    if apply:
        storage_repo.save_bible(novel_id, bible)
        print(f"[✓] Successfully saved Book Bible revision {bible.bible_revision}!")
    else:
        print("[i] Dry-run complete. Run with --apply to persist changes.")

    return bible


def main():
    parser = argparse.ArgumentParser(description="Backfill Book Bible for chapters 1-154")
    parser.add_argument("--novel-id", default="van-thu-chien-than", help="Novel ID to backfill")
    parser.add_argument("--max-chapter", type=int, default=154, help="Max chapter to scan")
    parser.add_argument("--apply", action="store_true", help="Persist backfilled Book Bible")
    args = parser.parse_args()

    backfill_novel_bible(novel_id=args.novel_id, max_chapter=args.max_chapter, apply=args.apply)


if __name__ == "__main__":
    main()
