"""Delete local ranking exports whose YouTube upload is confirmed."""
from pathlib import Path
from src.config import config
from src.database import RankingDatabase

def main():
    db=RankingDatabase(config.db_path); removed=0; missing=0
    with db._connect() as conn:
        rows=conn.execute("SELECT id, local_path, youtube_id FROM builds WHERE status='uploaded' AND local_path IS NOT NULL AND local_path != ''").fetchall()
    for row in rows:
        path=Path(row['local_path'])
        if not path.exists(): missing+=1; continue
        try:
            path.unlink(); removed+=1; print(f"CLEANUP_DONE build={row['id']} -> {path.name}")
        except OSError as exc: print(f"CLEANUP_WARN {path}: {exc}")
    print(f"\nUPLOADED FILE CLEANUP: removed={removed} already_missing={missing} records_kept={len(rows)}")
    return 0
if __name__=='__main__': raise SystemExit(main())
