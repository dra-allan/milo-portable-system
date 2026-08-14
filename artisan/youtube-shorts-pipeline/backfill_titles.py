"""Backfill clip hooks into generated_shorts.title for existing DB records.

Replaces source video titles in `generated_shorts.title` with actual transcript
clip hook text retrieved from `data/clip_plans/<video_id>.json` or rendered file
names.
"""

import json
import re
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DB_PATH = REPO_ROOT / 'data' / 'processed_videos.db'
CLIP_PLANS_DIR = REPO_ROOT / 'data' / 'clip_plans'


def backfill_database_titles():
    if not DB_PATH.exists():
        print(f"Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT gs.id, gs.source_video_id, gs.segment_index, gs.start_time, gs.end_time,
               gs.title as short_title, gs.local_path, pv.title as source_title
        FROM generated_shorts gs
        LEFT JOIN processed_videos pv ON gs.source_video_id = pv.youtube_video_id
    """)
    rows = cursor.fetchall()

    updated_count = 0
    skipped_count = 0
    plans_cache = {}

    for row in rows:
        short_id = row['id']
        video_id = row['source_video_id']
        seg_idx = row['segment_index']
        curr_title = (row['short_title'] or '').strip()
        source_title = (row['source_title'] or '').strip()
        local_path = (row['local_path'] or '').strip()

        # Check if title needs backfill (it matches source_title or is empty)
        if curr_title and source_title and curr_title != source_title:
            skipped_count += 1
            continue

        hook_text = None

        # 1. Try clip_plans
        plan_file = CLIP_PLANS_DIR / f"{video_id}.json"
        if plan_file.exists():
            if video_id not in plans_cache:
                try:
                    with open(plan_file, 'r', encoding='utf-8') as f:
                        plans_cache[video_id] = json.load(f)
                except Exception:
                    plans_cache[video_id] = None

            plan_data = plans_cache.get(video_id)
            if plan_data and 'candidates' in plan_data:
                candidates = plan_data['candidates']
                # Try matching by segment_index / rank or timestamps
                for cand in candidates:
                    cand_idx = cand.get('segment_index') or cand.get('rank') or cand.get('index')
                    if cand_idx == seg_idx or cand_idx == (seg_idx - 1):
                        hook_text = (cand.get('text') or '').strip()
                        break
                    # Timestamp check fallback
                    cand_start = cand.get('start')
                    if cand_start is not None and abs(float(cand_start) - float(row['start_time'])) < 1.0:
                        hook_text = (cand.get('text') or '').strip()
                        break

        # 2. Try output filename (NN_<safe_hook>.mp4)
        if not hook_text and local_path:
            p = Path(local_path)
            stem = p.stem  # e.g., "01_the_moment_everything_changed"
            m = re.match(r'^\d+_(.+)$', stem)
            if m:
                extracted = m.group(1).replace('_', ' ').strip()
                if extracted and not extracted.startswith('clip'):
                    hook_text = extracted

        if not hook_text:
            hook_text = f"clip_{seg_idx}"

        cursor.execute("UPDATE generated_shorts SET title = ? WHERE id = ?", (hook_text, short_id))
        updated_count += 1

    conn.commit()
    conn.close()

    print(f"Backfill complete! Updated: {updated_count} rows, Already correct: {skipped_count} rows.")


if __name__ == '__main__':
    backfill_database_titles()
