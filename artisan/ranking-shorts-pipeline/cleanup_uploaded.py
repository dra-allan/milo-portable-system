"""Delete local ranking exports whose YouTube upload is confirmed.

Uploads clean up after themselves now (RANKING_DELETE_AFTER_UPLOAD), so this
is the catch-up pass for anything published before that landed.
"""
from pathlib import Path
from src.cleanup import delete_local_video, disk_report
from src.config import config
from src.database import RankingDatabase


def main() -> int:
    db = RankingDatabase(config.db_path)
    removed = missing = 0
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT id, local_path, youtube_id FROM builds WHERE "
            "status='uploaded' AND local_path IS NOT NULL AND local_path != ''"
        ).fetchall()
    for row in rows:
        path = Path(row['local_path'])
        if not path.exists():
            missing += 1
            continue
        if delete_local_video(path, force=True):
            removed += 1
            print(f"CLEANUP_DONE build={row['id']} -> {path.name}")
    print(f'\nUPLOADED FILE CLEANUP: removed={removed} '
          f'already_missing={missing} records_kept={len(rows)}')
    print(f'disk: {disk_report()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
