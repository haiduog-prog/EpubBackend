"""Reconcile local storage chapter files with SQLite database chapters.

Usage:
    python scripts/reconcile_storage_chapters.py --novel-id van-thu-chien-than
    python scripts/reconcile_storage_chapters.py --novel-id van-thu-chien-than --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.session import db_session
from app.modules.library.persistence.legacy_models import ChapterModel, NovelModel
from app.schemas.library import ChapterStatus


def extract_chapter_index(filename: str) -> int | None:
    match = re.search(r"ch_(\d+)", filename, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def reconcile_novel_storage(novel_id: str, apply: bool = False) -> dict:
    storage_root = Path("storage/novels") / novel_id
    orig_dir = storage_root / "original"
    trans_dir = storage_root / "translated"

    if not storage_root.exists():
        print(f"[!] Storage directory does not exist: {storage_root}")
        return {"error": "Storage directory not found"}

    orig_files = {
        extract_chapter_index(f.name): f
        for f in orig_dir.glob("ch_*.txt")
        if extract_chapter_index(f.name) is not None
    }
    trans_files = {
        extract_chapter_index(f.name): f
        for f in trans_dir.glob("ch_*.txt")
        if extract_chapter_index(f.name) is not None
    }

    draft_dir = storage_root / "drafts"
    draft_files = {
        extract_chapter_index(f.name): f
        for f in draft_dir.glob("ch_*.txt")
        if extract_chapter_index(f.name) is not None
    } if draft_dir.exists() else {}

    all_indexes = sorted(set(orig_files.keys()) | set(trans_files.keys()) | set(draft_files.keys()))
    print(f"[*] Storage scanned: {len(orig_files)} original files, {len(trans_files)} translated files, {len(draft_files)} draft files.")

    # Load Book Bible for deterministic QA gate during reconciliation
    from app.infrastructure.storage.facade import storage_repo
    from app.modules.translation.application.qa_service import QAService
    bible = storage_repo.get_bible(novel_id)
    qa_service = QAService(None)

    stats = {
        "novel_id": novel_id,
        "total_files_found": len(all_indexes),
        "db_chapters_found": 0,
        "inserted_to_db": 0,
        "status_updated_to_completed": 0,
        "status_flagged_needs_review": 0,
    }

    with db_session() as session:
        novel = session.query(NovelModel).filter(NovelModel.novel_id == novel_id).first()
        if not novel:
            print(f"[!] Novel not found in database: {novel_id}")
            return {"error": "Novel not found in DB"}

        existing_db_chapters = {c.chapter_index: c for c in novel.chapters}
        stats["db_chapters_found"] = len(existing_db_chapters)
        print(f"[*] DB state: {len(existing_db_chapters)} chapters registered.")

        for idx in all_indexes:
            orig_file = orig_files.get(idx)
            trans_file = trans_files.get(idx)
            draft_file = draft_files.get(idx)

            orig_text = orig_file.read_text(encoding="utf-8", errors="ignore") if orig_file else ""
            trans_text = trans_file.read_text(encoding="utf-8", errors="ignore") if trans_file else ""
            draft_text = draft_file.read_text(encoding="utf-8", errors="ignore") if draft_file else ""

            # Extract title candidate from first non-empty line
            title = f"Chương {idx}"
            if orig_text:
                for line in orig_text.splitlines():
                    stripped = line.strip()
                    if stripped and len(stripped) < 120:
                        title = stripped
                        break

            ch_model = existing_db_chapters.get(idx)
            has_trans = bool(trans_file and trans_file.stat().st_size > 50)
            has_draft = bool(draft_file and draft_file.stat().st_size > 50)
            original_key = f"novels/{novel_id}/original/{orig_file.name}" if orig_file else None
            translated_key = f"novels/{novel_id}/translated/{trans_file.name}" if has_trans else None

            # Determine quality gate status
            target_status = ChapterStatus.NOT_TRANSLATED.value
            active_text = ""

            if has_draft and not has_trans:
                target_status = ChapterStatus.NEEDS_REVIEW.value
                active_text = draft_text
            elif has_trans:
                active_text = trans_text
                # Do not downgrade or silently auto-publish if currently flagged as needs review
                if ch_model and ch_model.status == ChapterStatus.NEEDS_REVIEW.value:
                    target_status = ChapterStatus.NEEDS_REVIEW.value
                elif not orig_text or bible is None:
                    # A translated file without its source (or Book Bible) cannot be
                    # certified by reconciliation alone.
                    target_status = ChapterStatus.NEEDS_REVIEW.value
                else:
                    # Run deterministic QA check before certifying as COMPLETED
                    qa_issues = []
                    qa_issues = qa_service.fast_rule_check(orig_text, trans_text, bible)
                    if qa_issues or has_draft:
                        target_status = ChapterStatus.NEEDS_REVIEW.value
                    else:
                        target_status = ChapterStatus.COMPLETED.value
            elif orig_text:
                active_text = orig_text

            word_count = len(active_text.split()) if active_text else 0

            if not ch_model:
                # Chapter missing in DB: insert
                stats["inserted_to_db"] += 1
                if target_status == ChapterStatus.NEEDS_REVIEW.value:
                    stats["status_flagged_needs_review"] += 1
                if apply:
                    ch_model = ChapterModel(
                        novel_id=novel_id,
                        chapter_index=idx,
                        chapter_id=f"{novel_id}-{idx:04d}",
                        chapter_title=title,
                        status=target_status,
                        word_count=word_count,
                        original_text_preview=orig_text[:200] if orig_text else None,
                        translated_text_preview=active_text[:200] if active_text else None,
                        original_r2_key=original_key,
                        translated_r2_key=translated_key,
                    )
                    ch_model.novel = novel
                    session.add(ch_model)
            else:
                # Keep storage keys in sync even when the current DB status is
                # already acceptable. The UI and build pipeline use these keys
                # as a durable marker for the corresponding chapter version.
                if original_key and not ch_model.original_r2_key:
                    ch_model.original_r2_key = original_key
                if translated_key:
                    ch_model.translated_r2_key = translated_key

                # Chapter exists in DB: check if status needs updating.
                if target_status == ChapterStatus.COMPLETED.value and ch_model.status != ChapterStatus.COMPLETED.value:
                    stats["status_updated_to_completed"] += 1
                    if apply:
                        ch_model.status = ChapterStatus.COMPLETED.value
                        ch_model.word_count = word_count
                        ch_model.translated_text_preview = trans_text[:200] if trans_text else None
                elif (
                    target_status == ChapterStatus.NEEDS_REVIEW.value
                    and ch_model.status not in {
                        ChapterStatus.NEEDS_REVIEW.value,
                        ChapterStatus.COMPLETED.value,
                    }
                ):
                    stats["status_flagged_needs_review"] += 1
                    if apply:
                        ch_model.status = ChapterStatus.NEEDS_REVIEW.value
                        ch_model.word_count = word_count
                        ch_model.translated_text_preview = active_text[:200] if active_text else None

        if apply:
            session.flush()
            novel.total_chapters = len(novel.chapters)
            novel.translated_chapters = sum(
                1 for c in novel.chapters if c.status == ChapterStatus.COMPLETED.value
            )
            session.commit()
            print(f"[ok] Successfully applied reconcile changes! DB now has {novel.total_chapters} total chapters ({novel.translated_chapters} completed).")
        else:
            print(
                f"[i] Dry-run complete. Would insert {stats['inserted_to_db']} chapters, "
                f"mark {stats['status_updated_to_completed']} completed and "
                f"flag {stats['status_flagged_needs_review']} for review."
            )
            print("[i] Run with --apply to commit changes to database.")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Reconcile local storage chapter files with SQLite DB")
    parser.add_argument("--novel-id", default="van-thu-chien-than", help="Novel ID to reconcile")
    parser.add_argument("--apply", action="store_true", help="Apply changes to DB")
    args = parser.parse_args()

    reconcile_novel_storage(novel_id=args.novel_id, apply=args.apply)


if __name__ == "__main__":
    main()
