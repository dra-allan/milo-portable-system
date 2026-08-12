#!/usr/bin/env python3
"""Build ranking output into explicit channel lanes, then post with caps."""
from __future__ import annotations
import os
import random
import time
from src.config import config
from src.main import RankingPipeline
from src.utils import setup_logger
from channel_profiles import profiles, channel_for

log = setup_logger(__name__, config.log_dir / 'ranking.log')


def main():
    daily = int(os.getenv('RANKING_UPLOAD_MAX_PER_DAY', str(config.upload_max_per_day)))
    lo = int(os.getenv('RANKING_UPLOAD_DELAY_MIN', '45'))
    hi = int(os.getenv('RANKING_UPLOAD_DELAY_MAX', '180'))
    per_run = max(1, int(os.getenv('RANKING_VIDEOS_PER_RUN', str(config.get('videos_per_run', 1) or 1))))
    pipeline = RankingPipeline()
    channel_map = profiles()
    topics = list(config.topic_names())
    if not topics or not channel_map:
        print('nothing to do: no topics or no channel profiles')
        return 2

    # Never manufacture contrast labels for an ordinary topic. A topic must
    # explicitly opt in with contrast_mode: true, otherwise it is normal copy.
    modes = ['normal']
    used = pipeline.db.uploads_since(86400)
    remaining = max(0, daily - used)
    log.info('MIXED_SWEEP_START builds=%d channels=%s upload_budget=%d/%d', per_run, channel_map, remaining, daily)

    built = uploaded = 0
    done: list[str] = []
    for i in range(per_run):
        channel = channel_for('normal', i)
        if not channel:
            continue
        topic = pipeline.db.next_topic([t for t in topics if t not in done]) or pipeline.db.next_topic(topics)
        if not topic:
            break
        done.append(topic)
        topic_cfg = config.topic(topic)
        variant = 'contrast' if topic_cfg.get('contrast_mode') else 'normal'
        # Other Guys gets only explicitly tagged contrast topics; all other
        # topics stay normal and cannot become "OTHERS VS THIS GUY" by accident.
        if channel == 'other_guys' and not topic_cfg.get('contrast_mode'):
            channel = 'rankedup'
        plan = pipeline.build(topic, upload=False, variant=variant, channel=channel)
        if not plan:
            continue
        built += 1
        if remaining <= 0:
            log.info('MIXED_SWEEP_QUEUED cap reached; %s remains queued', plan.get('local_path'))
            continue
        if uploaded and hi > 0:
            time.sleep(random.uniform(lo, hi))
        if pipeline.upload_build(plan['build_id'], plan) is not None:
            uploaded += 1
            remaining -= 1

    print(f'mixed sweep built={built} uploaded={uploaded} topics={",".join(done) or "none"} profiles={channel_map} privacy={config.privacy_status}')
    return 0 if (built or uploaded) else 1


if __name__ == '__main__':
    raise SystemExit(main())
