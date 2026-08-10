"""Delete local Shorts whose YouTube upload is already confirmed."""
from pathlib import Path
from src.config import config
from src.database import PipelineDatabase

def main():
    db=PipelineDatabase(); removed=0; missing=0
    with db._connect() as conn:
        rows=conn.execute("SELECT source_video_id, segment_index, local_path, youtube_short_id FROM generated_shorts WHERE youtube_short_id IS NOT NULL AND local_path IS NOT NULL AND local_path != ''").fetchall()
    for row in rows:
        path=Path(row['local_path'])
        if not path.exists(): missing+=1; continue
        try:
            path.unlink(); removed+=1; print(f"CLEANUP_DONE {row['source_video_id']}#{row['segment_index']} -> {path.name}")
        except OSError as exc: print(f"CLEANUP_WARN {path}: {exc}")
    print(f"\nUPLOADED FILE CLEANUP: removed={removed} already_missing={missing} records_kept={len(rows)}")
    return 0
if __name__=='__main__': raise SystemExit(main())
