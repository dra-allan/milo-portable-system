#!/usr/bin/env python3
"""Build a 3 normal + 3 contrast ranking batch, then upload publicly.

Hard limits: six uploads in any rolling 24-hour period, with a random delay
between successful uploads. The channel key is the shared Shorts/POV token
name, not an API key.
"""
from __future__ import annotations
import os, random, time
from pathlib import Path
from src.config import config
from src.main import RankingPipeline
from src import assembler, ranker, scriptwriter
from src.utils import ensure_dir, safe_slug, setup_logger

log = setup_logger(__name__, config.log_dir / 'ranking.log')

def channel_key() -> str:
    return (os.getenv('RANKING_UPLOAD_CHANNEL') or os.getenv('RANKING_CHANNEL') or 'rankdrop').strip()

def build(pipeline: RankingPipeline, topic: str, variant: str):
    cfg = config.topic(topic)
    if not cfg.get('queries') and not cfg.get('extra_sources'):
        return None
    clips = pipeline.collect_clips(cfg, int(config.get('clips_per_video', 5)))
    if len(clips) < 2: return None
    ordered = ranker.rank(clips, count=len(clips))
    if ordered: ordered[0]['hook_candidate'] = True
    assembler.fit_windows(ordered)
    meta = scriptwriter.write_copy(cfg, ordered)
    subject = (os.getenv('CONTRAST_SUBJECT') or 'GUY').upper()
    if variant == 'contrast':
        for i, clip in enumerate(ordered):
            action = (clip.get('title') or 'THIS').upper().replace('OTHERS ', '').replace('BUT ', '')
            clip['title'] = f'BUT THIS {subject}' if i == len(ordered) - 1 else f'OTHERS {action}'
        meta['video_title'] = f'OTHERS VS THIS {subject}'
        meta['upload_title'] = meta['video_title'] + ' #Shorts'
    slug = f'{safe_slug(topic)}_{variant}_{int(time.time())}'
    scriptwriter.generate_voiceover(ordered, slug)
    scriptwriter.attach_sfx(ordered)
    plan = {'topic': topic, 'variant': variant, 'slug': slug,
            'video_title': meta['video_title'], 'upload_title': meta['upload_title'],
            'description': meta['description'], 'tags': meta['tags'],
            'channel': channel_key(), 'clips': [
                {'path': c['local_path'], 'start': c.get('clip_start', 0.0),
                 'duration': c.get('clip_duration', 4.0), 'action_offset': c.get('action_offset', 0.0),
                 'rank': c['rank'], 'title': c.get('title'), 'vo_path': c.get('vo_path'),
                 'sfx': c.get('sfx') or [], 'text_boxes': c.get('text_boxes') or [],
                 'url': c.get('url'), 'uploader': c.get('uploader'), 'phash': c.get('phash'),
                 'score': c.get('score')} for c in ordered]}
    pipeline._save_plan(plan)
    output = assembler.assemble(plan)
    if not output: return None
    bid = pipeline.db.record_build(topic, meta['upload_title'], str(output), plan)
    for c in plan['clips']: pipeline.db.mark_used(c['url'], topic, c.get('phash'), c.get('title'))
    pipeline.db.touch_topic(topic); plan['local_path'] = str(output); plan['build_id'] = bid
    return plan

def main() -> int:
    daily = int(os.getenv('RANKING_UPLOAD_MAX_PER_DAY', '6'))
    min_delay = int(os.getenv('RANKING_UPLOAD_DELAY_MIN', '45'))
    max_delay = int(os.getenv('RANKING_UPLOAD_DELAY_MAX', '180'))
    used = RankingPipeline().db.uploads_since(86400)
    remaining = max(0, daily - used)
    if remaining <= 0:
        log.info('MIXED_SWEEP_SKIP daily cap %d/%d reached', used, daily); return 0
    pipeline = RankingPipeline(); topics = config.topic_names()
    if not topics: return 2
    wanted = min(6, remaining)
    built = uploaded = 0
    for i in range(wanted):
        topic = topics[i % len(topics)]
        variant = 'normal' if i % 2 == 0 else 'contrast'
        plan = build(pipeline, topic, variant)
        if not plan: continue
        built += 1
        if uploaded and max_delay > 0: time.sleep(random.uniform(min_delay, max_delay))
        if pipeline.upload_build(plan['build_id'], plan) is not None: uploaded += 1
    print(f'mixed sweep built={built} uploaded={uploaded} normal={(wanted+1)//2} contrast={wanted//2} channel={channel_key()} privacy=public')
    return 0 if uploaded or built else 1

if __name__ == '__main__': raise SystemExit(main())
