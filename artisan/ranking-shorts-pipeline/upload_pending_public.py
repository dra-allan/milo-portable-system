#!/usr/bin/env python3
"""Drain pending ranking builds publicly with the shared channel cap."""
from __future__ import annotations
import os, random, time, json
from pathlib import Path
from src.config import config
from src.main import RankingPipeline

def main():
    fallback = (os.getenv('RANKING_UPLOAD_CHANNEL') or os.getenv('RANKING_CHANNEL') or 'RankDrop').strip()
    daily = int(os.getenv('RANKING_UPLOAD_MAX_PER_DAY', '6'))
    lo = int(os.getenv('RANKING_UPLOAD_DELAY_MIN', '45')); hi = int(os.getenv('RANKING_UPLOAD_DELAY_MAX', '180'))
    p = RankingPipeline(); remaining = max(0, daily - p.db.uploads_since(86400))
    uploaded = 0
    for row in p.db.pending_builds(limit=100):
        if uploaded >= remaining: break
        path = row.get('local_path')
        if not path or not Path(path).exists():
            p.db.mark_failed(int(row['id']), 'file_missing'); continue
        try: plan = json.loads(row.get('plan_json') or '{}')
        except json.JSONDecodeError: plan = {}
        plan['local_path'] = path
        plan['channel'] = plan.get('channel') or fallback
        plan.setdefault('upload_title', row.get('title') or 'TOP 5')
        plan.setdefault('description', '')
        if uploaded and hi > 0: time.sleep(random.uniform(lo, hi))
        if p.upload_build(int(row['id']), plan) is not None: uploaded += 1
    print(f'uploaded {uploaded} pending build(s), channel={plan.get("channel", fallback)}, privacy=public, remaining_daily={max(0, remaining-uploaded)}')
    return 0
if __name__ == '__main__': raise SystemExit(main())
