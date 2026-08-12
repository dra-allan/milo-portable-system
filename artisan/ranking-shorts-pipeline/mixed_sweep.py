#!/usr/bin/env python3
"""Build/upload ranking videos across specialized channels.

RANKING_CHANNEL_PROFILES example:
  rankdrop:contrast,rankings_main:normal,rankings_mix:both

A rolling 24-hour cap of six uploads is shared across all configured channels.
Each successful upload waits a random delay before the next one.
"""
from __future__ import annotations
import os, random, time
from src.config import config
from src.main import RankingPipeline
from src import assembler, ranker, scriptwriter
from src.utils import safe_slug, setup_logger
from channel_profiles import profiles, channel_for
log = setup_logger(__name__, config.log_dir / 'ranking.log')

def build(pipeline, topic, variant, channel):
    cfg = config.topic(topic)
    clips = pipeline.collect_clips(cfg, int(config.get('clips_per_video', 5)))
    if len(clips) < 2: return None
    ordered = ranker.rank(clips, count=len(clips)); assembler.fit_windows(ordered)
    if ordered: ordered[0]['hook_candidate'] = True
    meta = scriptwriter.write_copy(cfg, ordered)
    if variant == 'contrast':
        subject = (os.getenv('CONTRAST_SUBJECT') or 'GUY').upper()
        for i, clip in enumerate(ordered):
            action = (clip.get('title') or 'THIS').upper().replace('OTHERS ', '').replace('BUT ', '')
            clip['title'] = f'BUT THIS {subject}' if i == len(ordered)-1 else f'OTHERS {action}'
        meta['video_title'] = f'OTHERS VS THIS {subject}'; meta['upload_title'] = meta['video_title'] + ' #Shorts'
    slug = f'{safe_slug(topic)}_{variant}_{channel}_{int(time.time())}'
    scriptwriter.generate_voiceover(ordered, slug); scriptwriter.attach_sfx(ordered)
    plan = {'topic': topic, 'variant': variant, 'slug': slug, 'channel': channel,
            'video_title': meta['video_title'], 'upload_title': meta['upload_title'],
            'description': meta['description'], 'tags': meta['tags'], 'clips': [
                {'path': c['local_path'], 'start': c.get('clip_start', 0.0), 'duration': c.get('clip_duration', 4.0), 'action_offset': c.get('action_offset', 0.0), 'rank': c['rank'], 'title': c.get('title'), 'vo_path': c.get('vo_path'), 'sfx': c.get('sfx') or [], 'text_boxes': c.get('text_boxes') or [], 'url': c.get('url'), 'uploader': c.get('uploader'), 'phash': c.get('phash'), 'score': c.get('score')} for c in ordered]}
    pipeline._save_plan(plan); output = assembler.assemble(plan)
    if not output: return None
    bid = pipeline.db.record_build(topic, meta['upload_title'], str(output), plan)
    for c in plan['clips']: pipeline.db.mark_used(c['url'], topic, c.get('phash'), c.get('title'))
    pipeline.db.touch_topic(topic); plan.update(local_path=str(output), build_id=bid)
    return plan

def main():
    daily = int(os.getenv('RANKING_UPLOAD_MAX_PER_DAY', '6')); lo = int(os.getenv('RANKING_UPLOAD_DELAY_MIN', '45')); hi = int(os.getenv('RANKING_UPLOAD_DELAY_MAX', '180'))
    pipeline = RankingPipeline(); used = pipeline.db.uploads_since(86400); remaining = max(0, daily-used)
    if not remaining: log.info('MIXED_SWEEP_SKIP daily cap reached %d/%d', used, daily); return 0
    channel_map = profiles(); topics = config.topic_names()
    if not topics or not channel_map: return 2
    modes = [m for m in ('normal','contrast') if any(m in x for x in channel_map.values())]
    built = uploaded = 0
    for i in range(min(6, remaining)):
        variant = modes[i % len(modes)]
        channel = channel_for(variant, i // len(modes))
        if not channel: continue
        plan = build(pipeline, topics[i % len(topics)], variant, channel)
        if not plan: continue
        built += 1
        if uploaded and hi > 0: time.sleep(random.uniform(lo, hi))
        if pipeline.upload_build(plan['build_id'], plan) is not None: uploaded += 1
    print(f'mixed sweep built={built} uploaded={uploaded} profiles={channel_map} privacy=public')
    return 0 if uploaded or built else 1
if __name__ == '__main__': raise SystemExit(main())
