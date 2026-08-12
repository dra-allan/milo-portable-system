#!/usr/bin/env python3
"""Build and upload ranking videos across specialized channels.

RANKING_CHANNEL_PROFILES example:
  rankdrop:contrast,rankings_main:normal,rankings_mix:both

Topics rotate through the database's least-recently-run order - the same
rotation ``--mode auto`` uses. The old version indexed ``topics[i % len]``
starting from zero every run, so with a small per-run count it rebuilt the
first entry in ranking.yaml (fishing) forever and the other twenty topics were
decoration.

How many videos a sweep builds is RANKING_VIDEOS_PER_RUN (or videos_per_run in
ranking.yaml). Uploads are separate: a rolling 24-hour cap is shared across all
configured channels, and anything built past the cap stays queued for the next
run instead of being thrown away. Each successful upload waits a random delay
before the next one.
"""
from __future__ import annotations
import os, random, time
from src.config import config
from src.main import RankingPipeline
from src.utils import setup_logger
from channel_profiles import profiles, channel_for
log = setup_logger(__name__, config.log_dir / 'ranking.log')


def main():
    daily = int(os.getenv('RANKING_UPLOAD_MAX_PER_DAY',
                          str(config.upload_max_per_day)))
    lo = int(os.getenv('RANKING_UPLOAD_DELAY_MIN', '45'))
    hi = int(os.getenv('RANKING_UPLOAD_DELAY_MAX', '180'))
    per_run = max(1, int(os.getenv('RANKING_VIDEOS_PER_RUN',
                                   str(config.get('videos_per_run', 1) or 1))))

    pipeline = RankingPipeline()
    channel_map = profiles()
    topics = list(config.topic_names())
    if not topics or not channel_map:
        print('nothing to do: no topics or no channel profiles')
        return 2

    modes = [m for m in ('normal', 'contrast')
             if any(m in wanted for wanted in channel_map.values())] or ['normal']
    used = pipeline.db.uploads_since(86400)
    remaining = max(0, daily - used)
    log.info('MIXED_SWEEP_START builds=%d modes=%s upload_budget=%d/%d',
             per_run, modes, remaining, daily)

    built = uploaded = 0
    done: list[str] = []
    for i in range(per_run):
        variant = modes[i % len(modes)]
        channel = channel_for(variant, i // len(modes))
        if not channel:
            continue
        # Least-recently-run topic that this sweep has not already used.
        topic = pipeline.db.next_topic([t for t in topics if t not in done]) \
            or pipeline.db.next_topic(topics)
        if not topic:
            break
        done.append(topic)
        plan = pipeline.build(topic, upload=False, variant=variant,
                              channel=channel)
        if not plan:
            continue
        built += 1
        if remaining <= 0:
            log.info('MIXED_SWEEP_QUEUED cap %d/24h spent; %s waits for the '
                     'next run', daily, plan.get('local_path'))
            continue
        if uploaded and hi > 0:
            time.sleep(random.uniform(lo, hi))
        if pipeline.upload_build(plan['build_id'], plan) is not None:
            uploaded += 1
            remaining -= 1

    print(f'mixed sweep built={built} uploaded={uploaded} '
          f'topics={",".join(done) or "none"} profiles={channel_map} '
          f'privacy={config.privacy_status}')
    return 0 if (built or uploaded) else 1


if __name__ == '__main__':
    raise SystemExit(main())
