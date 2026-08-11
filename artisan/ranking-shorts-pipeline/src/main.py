"""Orchestration and CLI.

The run is a straight line with one loop in the middle:

    discover -> [download -> vet] until enough clips -> rank -> write copy
    -> voice-over -> SFX -> render -> stitch -> upload

The loop is where the autonomy lives. Vetting rejects most candidates (that is
the point - the reject rate is what keeps the output looking organic), so the
pipeline downloads and vets one at a time until it has its five, instead of
fetching forty clips up front and throwing thirty-five away.

Modes:
    once      - build one video for --topic (or the first configured topic)
    auto      - pick the least-recently-run topic, build, upload
    source    - discovery + vetting only; print what passed. No render.
    assemble  - re-render from a saved plan (data/plans/<slug>.json)
    upload    - upload anything built but not yet published
    test      - environment check
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import assembler, ranker, scriptwriter, sourcing, vetting
from .config import config
from .database import RankingDatabase
from .utils import ensure_dir, safe_slug, setup_logger, which_ffmpeg

logger = setup_logger(__name__, config.log_dir / 'ranking.log')


class RankingPipeline:
    def __init__(self) -> None:
        self.db = RankingDatabase(config.db_path)

    # -- clip acquisition ----------------------------------------------
    def collect_clips(self, topic_cfg: Dict, needed: int) -> List[Dict]:
        """Download and vet candidates until ``needed`` clips pass."""
        candidates = sourcing.discover(topic_cfg, self.db)
        if not candidates:
            logger.error("no candidates for topic '%s'", topic_cfg['name'])
            return []

        known = self.db.known_hashes()
        accepted: List[Dict] = []
        # Give up after this many rejects rather than grinding through every
        # candidate: if 4x the target has failed, the topic's queries or
        # thresholds are wrong and another 30 downloads will not fix it.
        reject_budget = max(12, needed * 4)
        rejects = 0

        for candidate in candidates:
            if len(accepted) >= needed:
                break
            if rejects >= reject_budget:
                logger.warning('reject budget spent (%d); stopping with %d '
                               'clip(s)', rejects, len(accepted))
                break

            path = sourcing.download(candidate)
            if not path:
                rejects += 1
                self.db.mark_rejected(candidate['url'], topic_cfg['name'],
                                      'download_failed')
                continue

            candidate['allow_commentary'] = bool(
                topic_cfg.get('allow_commentary'))
            candidate['allow_music'] = bool(topic_cfg.get('allow_music'))
            result = vetting.vet(candidate, known)
            if not result.get('ok'):
                rejects += 1
                reason = result.get('reason') or 'unknown'
                logger.info('rejected (%s): %s', reason,
                            (candidate.get('title') or '')[:60])
                self.db.mark_rejected(candidate['url'], topic_cfg['name'],
                                      reason)
                # Delete the file: rejects are the majority, and keeping them
                # fills the disk with clips that will never be used.
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
                continue

            if result.get('phash'):
                known.append(result['phash'])
            accepted.append(result)

        logger.info('collected %d/%d clip(s) after %d reject(s)',
                    len(accepted), needed, rejects)
        return accepted

    # -- build ---------------------------------------------------------
    def build(self, topic_name: str, upload: bool = True) -> Optional[Dict]:
        topic_cfg = config.topic(topic_name)
        if not topic_cfg.get('queries') and not topic_cfg.get('extra_sources'):
            logger.error("topic '%s' has no queries or sources configured",
                         topic_name)
            return None

        needed = int(config.get('clips_per_video', 5))
        clips = self.collect_clips(topic_cfg, needed)
        if len(clips) < 2:
            logger.error('only %d usable clip(s); a countdown needs at least '
                         '2. Loosen thresholds or add queries.', len(clips))
            return None
        if len(clips) < needed:
            # Renumber to what was actually found rather than shipping a
            # "TOP 5" with three clips in it.
            logger.warning('building a top-%d instead of a top-%d',
                           len(clips), needed)

        ordered = ranker.rank(clips, count=len(clips))
        if ordered:
            ordered[0]['hook_candidate'] = True

        meta = scriptwriter.write_copy(topic_cfg, ordered)
        slug = f"{topic_name}_{int(time.time())}"
        scriptwriter.generate_voiceover(ordered, slug)
        scriptwriter.attach_sfx(ordered)

        plan = {
            'topic': topic_name,
            'slug': slug,
            'video_title': meta['video_title'],
            'upload_title': meta['upload_title'],
            'description': meta['description'],
            'tags': meta['tags'],
            'channel': topic_cfg.get('channel'),
            'clips': [
                {
                    'path': clip['local_path'],
                    'start': clip.get('clip_start', 0.0),
                    'duration': clip.get('clip_duration', 4.0),
                    'rank': clip['rank'],
                    'title': clip.get('title'),
                    'vo_path': clip.get('vo_path'),
                    'sfx': clip.get('sfx') or [],
                    'text_boxes': clip.get('text_boxes') or [],
                    'url': clip.get('url'),
                    'uploader': clip.get('uploader'),
                    'phash': clip.get('phash'),
                    'score': clip.get('score'),
                }
                for clip in ordered
            ],
        }
        self._save_plan(plan)

        if config.dry_run:
            logger.info('DRY_RUN: plan written, nothing rendered')
            return plan

        output = assembler.assemble(plan)
        if not output:
            logger.error('assembly failed')
            return None

        build_id = self.db.record_build(topic_name, meta['upload_title'],
                                        str(output), plan)
        for clip in plan['clips']:
            self.db.mark_used(clip['url'], topic_name, clip.get('phash'),
                              clip.get('title'))
        self.db.touch_topic(topic_name)
        plan['local_path'] = str(output)
        plan['build_id'] = build_id
        logger.info('built %s', output)

        if upload:
            self.upload_build(build_id, plan)
        return plan

    # -- upload --------------------------------------------------------
    def upload_build(self, build_id: int, plan: Dict) -> Optional[str]:
        cap = int(config.upload_max_per_run)
        if cap and self.db.uploads_since(3600 * 6) >= cap:
            logger.info('upload cap reached for this window; leaving %s queued',
                        plan.get('local_path'))
            return None
        try:
            from .publisher import RankingPublisher
            publisher = RankingPublisher(channel=plan.get('channel'))
        except Exception as exc:  # noqa: BLE001
            logger.error('publisher unavailable: %s', exc)
            self.db.mark_failed(build_id, 'no_publisher')
            return None

        video_id = publisher.upload(
            plan['local_path'], plan['upload_title'], plan['description'],
            plan.get('tags') or [])
        if video_id:
            self.db.mark_uploaded(build_id, video_id)
        else:
            self.db.mark_failed(build_id, 'upload_failed')
        return video_id

    def drain_queue(self) -> int:
        """Upload builds that were rendered but never published."""
        uploaded = 0
        for row in self.db.pending_builds():
            path = row.get('local_path')
            if not path or not Path(path).exists():
                # The file is gone (cleanup, moved directory). Leaving the row
                # 'built' means every future run retries it forever.
                self.db.mark_failed(int(row['id']), 'file_missing')
                continue
            try:
                plan = json.loads(row.get('plan_json') or '{}')
            except json.JSONDecodeError:
                plan = {}
            plan['local_path'] = path
            plan.setdefault('upload_title', row.get('title') or 'TOP 5')
            plan.setdefault('description', '')
            if self.upload_build(int(row['id']), plan):
                uploaded += 1
        return uploaded

    # -- plans ---------------------------------------------------------
    def _save_plan(self, plan: Dict) -> Path:
        plans_dir = ensure_dir(config.data_dir / 'plans')
        path = plans_dir / f"{safe_slug(plan['slug'])}.json"
        path.write_text(json.dumps(plan, indent=2, default=str),
                        encoding='utf-8')
        logger.info('plan -> %s', path)
        return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def environment_check() -> int:
    problems = 0
    try:
        which_ffmpeg()
        print('[ok]   ffmpeg')
    except RuntimeError as exc:
        print(f'[FAIL] {exc}')
        problems += 1
    try:
        print(f'[ok]   font: {config.resolve_font()}')
    except RuntimeError as exc:
        print(f'[FAIL] {exc}')
        problems += 1

    for module, note in (
        ('yt_dlp', 'required for sourcing'),
        ('yaml', 'required for config'),
        ('faster_whisper', 'optional: commentary detection'),
        ('pytesseract', 'optional: on-screen text blurring'),
        ('librosa', 'optional: music-bed detection'),
        ('PIL', 'optional: duplicate detection'),
        ('google.genai', 'optional: voice-over + copywriting'),
        ('googleapiclient', 'optional: uploading'),
    ):
        try:
            __import__(module)
            print(f'[ok]   {module}')
        except ImportError:
            required = 'required' in note
            print(f"[{'FAIL' if required else 'warn'}] {module} - {note}")
            if required:
                problems += 1

    topics = config.topic_names()
    print(f'[ok]   {len(topics)} topic(s): {", ".join(topics) or "none"}')
    if not config.sfx_dir.exists():
        print(f'[warn] no SFX directory at {config.sfx_dir} - transitions '
              'will be silent')
    print(f"[ok]   encoder: {assembler._Encoder.resolve()}")
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Ranking Shorts pipeline: sources clips, ranks them 5-1, '
                    'composes and publishes a countdown.')
    parser.add_argument('--mode', default='once',
                        choices=['once', 'auto', 'source', 'assemble',
                                 'upload', 'test'])
    parser.add_argument('--topic', default=None)
    parser.add_argument('--plan', default=None,
                        help='plan JSON for --mode assemble')
    parser.add_argument('--no-upload', action='store_true')
    parser.add_argument('--dry-run', action='store_true',
                        help='write the plan, render nothing')
    args = parser.parse_args(argv)

    if args.dry_run:
        config.dry_run = True

    if args.mode == 'test':
        return environment_check()

    pipeline = RankingPipeline()

    if args.mode == 'upload':
        count = pipeline.drain_queue()
        print(f'uploaded {count} build(s)')
        return 0

    if args.mode == 'assemble':
        if not args.plan:
            print('--plan is required for --mode assemble')
            return 2
        plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
        output = assembler.assemble(plan)
        print(output or 'assembly failed')
        return 0 if output else 1

    topic = args.topic
    if args.mode == 'auto':
        topic = pipeline.db.next_topic(config.topic_names())
    topic = topic or (config.topic_names() or [None])[0]
    if not topic:
        print('no topics configured in config/ranking.yaml')
        return 2

    if args.mode == 'source':
        topic_cfg = config.topic(topic)
        needed = int(config.get('clips_per_video', 5))
        clips = pipeline.collect_clips(topic_cfg, needed)
        for clip in ranker.rank(clips, count=len(clips)):
            print(f"#{clip['rank']} score={clip['score']} "
                  f"{clip.get('title')} <- {clip.get('url')}")
        return 0

    plan = pipeline.build(topic, upload=not args.no_upload)
    return 0 if plan else 1


if __name__ == '__main__':
    sys.exit(main())
