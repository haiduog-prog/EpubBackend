import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.storage import storage_repo

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('CleanupScript')


def get_structured_json_keys() -> list[str]:
    prefixes_to_clean = [
        'data/jobs/',
        'data/bibles/',
        'data/profile_books/',
        'data/profile_editions/',
        'data/profile_chapter_mappings/',
        'data/profile_submissions/',
        'data/profile_events/',
        'data/profile_evidence/',
        'profile_books/',
        'profile_editions/',
        'profile_chapter_mappings/',
        'profile_submissions/',
        'profile_events/',
        'profile_evidence/',
    ]
    all_keys = []
    for p in prefixes_to_clean:
        all_keys.extend(storage_repo.list_files(prefix=p))

    # Also per-novel structured JSON files
    novel_keys = storage_repo.list_files(prefix='novels/')
    for k in novel_keys:
        if k.endswith('/metadata.json') or k.endswith('/bible.json') or '/profile/' in k:
            all_keys.append(k)

    return sorted(list(set(all_keys)))


def main():
    parser = argparse.ArgumentParser(description='Cleanup structured JSON objects from Cloudflare R2 / Local Storage')
    parser.add_argument('--dry-run', action='store_true', default=False, help='Simulate cleanup without deleting')
    parser.add_argument('--execute', action='store_true', default=False, help='Execute deletion of structured JSON files')
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        logger.error('You must specify either --dry-run or --execute')
        sys.exit(1)

    keys = get_structured_json_keys()
    logger.info(f'Identified {len(keys)} structured JSON objects for cleanup.')

    if args.dry_run:
        logger.info('=== DRY RUN MODE: No files will be deleted ===')
        for k in keys[:20]:
            logger.info(f' [PLAN TO DELETE] {k}')
        if len(keys) > 20:
            logger.info(f' ... and {len(keys) - 20} more files.')
        logger.info('Dry run completed.')
        return

    if args.execute:
        logger.warning('=== EXECUTING CLEANUP ===')
        deleted_count = 0
        failed_count = 0
        for k in keys:
            try:
                ok = storage_repo.delete_file(k)
                if ok:
                    deleted_count += 1
                else:
                    failed_count += 1
                    logger.warning(f'Could not delete {k}')
                if (deleted_count + failed_count) % 100 == 0:
                    logger.info(f'Processed {deleted_count + failed_count}/{len(keys)} files (deleted: {deleted_count}, failed: {failed_count})...')
            except Exception as e:
                failed_count += 1
                logger.error(f'Failed to delete {k}: {e}')

        logger.info(f'=== Cleanup Completed. Deleted: {deleted_count}/{len(keys)}, Failed: {failed_count} ===')



if __name__ == '__main__':
    main()
