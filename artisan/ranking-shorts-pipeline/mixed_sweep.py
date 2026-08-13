#!/usr/bin/env python3
"""Build ranking output into explicit channel lanes, then post with caps."""
from __future__ import annotations
import os
import random
import time
from src.config import config
from src.main import RankingPipeline
from src.utils import setup_logger
from channel_profiles import profiles, route_channel

log = setup_logger(__name__, config.log_dir / 'ranking.log')


def main():
    daily = int(os.getenv('RANKING_UPLOAD_MAX_PER_CHANNEL',
                          os.getenv('RANKING_UPLOAD_MAX_PER_DAY',
                                    str(config.upload_max_per_channel))))
    lo = int(os.getenv('RANKING_UPLOAD_DELAY_MIN', '45'))
    hi = int(os.getenv('RANKING_UPLOAD_DELAY_MAX', '180'))
    per_run = max(1, int(os.getenv('RANKING_VIDEOS_PER_RUN', str(config.get('videos_per_run', 1) or 1))))
    other_topics = {x.strip() for x in os.getenv('OTHER_GUYS_TOPICS', '').split(',') if x.strip()}
    pipeline = RankingPipeline()
    channel_map = profiles()
    topics = list(config.topic_names())
    if not topics or not channel_map:
        print('nothing to do: no topics or no channel profiles')
        return 2

    log.info('MIXED_SWEEP_START builds=%d channels=%s cap_per_channel=%d/24h',
             per_run, channel_map, daily)

    built = uploaded = skipped = 0
    done: list[str] = []
    for _ in range(per_run):
        topic = pipeline.db.next_topic([t for t in topics if t not in done]) or pipeline.db.next_topic(topics)
        if not topic:
            break
        done.append(topic)
        topic_cfg = config.topic(topic)
        is_other_guys = topic in other_topics
        # Contrast is opt-in twice: the topic must be listed for Other Guys and
        # must declare contrast_mode. Lightning or any ordinary topic can never
        # become "OTHERS VS THIS GUY" by accident.
        variant = 'contrast' if is_other_guys and topic_cfg.get('contrast_mode') else 'normal'
        channel = route_channel(variant)
        plan = pipeline.build(topic, upload=False, variant=variant, channel=channel)
        if not plan:
            continue
        built += 1
        if pipeline.db.uploaded_count_for_channel_since(channel, 86400) >= daily:
            log.info('MIXED_SWEEP_QUEUED cap reached for %s; %s remains queued',
                     channel, plan.get('local_path'))
            skipped += 1
            continue
        if uploaded and hi > 0:
            time.sleep(random.uniform(lo, hi))
        if pipeline.upload_build(plan['build_id'], plan) is not None:
            uploaded += 1

    print(f'mixed sweep built={built} uploaded={uploaded} cap_skipped={skipped} topics={",".join(done) or "none"} profiles={channel_map} privacy={config.privacy_status}')
    return 0 if (built or uploaded) else 1


if __name__ == '__main__':
    raise SystemExit(main())
