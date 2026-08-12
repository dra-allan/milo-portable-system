#!/usr/bin/env python3
"""Remove missing-file queue rows and empty per-source output folders."""
from __future__ import annotations
import sqlite3
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent; sys.path.insert(0,str(HERE))
from src.config import config

def main():
    conn=sqlite3.connect(str(config.db_path)); cur=conn.cursor(); removed=0
    try:
        rows=cur.execute("SELECT source_video_id, segment_index, local_path FROM generated_shorts WHERE local_path IS NOT NULL AND local_path != ''").fetchall()
        for source,segment,path in rows:
            if not Path(path).exists():
                cur.execute("UPDATE generated_shorts SET status='missing' WHERE source_video_id=? AND segment_index=?",(source,segment)); removed+=1
        conn.commit()
    finally: conn.close()
    for root in (Path(config.shorts_dir), Path(config.temp_dir)):
        if not root.exists(): continue
        for folder in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p:len(p.parts), reverse=True):
            try:
                if not any(folder.iterdir()): folder.rmdir()
            except OSError: pass
    print(f'[cleanup] marked {removed} stale clip rows; removed empty folders')
    return 0
if __name__=='__main__': raise SystemExit(main())
