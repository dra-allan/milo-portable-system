#!/usr/bin/env python3
"""Drain pending ranking builds publicly with the shared channel cap."""
from __future__ import annotations
import os, random, time, json
from pathlib import Path
from src.config import config
from src.main import RankingPipeline

def main():
    fallback = (os.getenv('RANKING_UPLOAD_CHANNEL') or os.getenv('RANKING_CHANNEL') or 'RankDrop').strip()
    daily = int(os.getenv('RANKING_UPLOAD_MAX_PER_CHANNEL',
                          os.getenv('RANKING_UPLOAD_MAX_PER_DAY', '6')))
    lo = int(os.getenv('RANKING_UPLOAD_DELAY_MIN', '45')); hi = int(os.getenv('RANKING_UPLOAD_DELAY_MAX', '180'))
    p = RankingPipeline(); uploaded = 0; plan = {}; skipped = 0
    for row in p.db.pending_builds(limit=100):
        path = row.get('local_path')
        if not path or not Path(path).exists():
            p.db.mark_failed(int(row['id']), 'file_missing'); continue
        try: plan = json.loads(row.get('plan_json') or '{}')
        except json.JSONDecodeError: plan = {}
        plan['local_path'] = path
        plan['channel'] = plan.get('channel') or fallback
        plan.setdefault('upload_title', row.get('title') or 'TOP 5')
        plan.setdefault('description', '')
        channel = p._resolve_channel(plan)
        if p.db.uploaded_count_for_channel_since(channel, 86400) >= daily:
            skipped += 1
            continue
        if uploaded and hi > 0: time.sleep(random.uniform(lo, hi))
        if p.upload_build(int(row['id']), plan) is not None: uploaded += 1
    print(f'uploaded {uploaded} pending build(s), cap_skipped={skipped}, privacy=public, cap={daily}/24h per channel')
    return 0
if __name__ == '__main__': raise SystemExit(main())
