"""Orchestration and CLI.

The run is a straight line with one loop in the middle:

    discover -> [download -> vet] until enough clips -> rank -> write copy
    -> voice-over -> SFX -> render -> stitch -> upload -> purge

The loop is where the autonomy lives. Vetting rejects most candidates (that is
the point - the reject rate is what keeps the output looking organic), so the
pipeline downloads and vets one at a time until it has its five, instead of
fetching forty clips up front and throwing thirty-five away.

Every build ends by purging its own inputs, and every confirmed upload deletes
its local export (see ``cleanup``). The pipeline used to leave both behind,
which is how a week of runs turned into tens of gigabytes of dead clips.

Variants:
    normal    - a straight TOP-N countdown
    contrast  - the same footage, 'OTHERS ...' / 'BUT THIS <SUBJECT>' copy
    mixed     - alternate the two across the videos of one auto run

Modes:
    once      - build one video for --topic (or the first configured topic)
    auto      - build N videos (--videos, default videos_per_run), rotating
                topics, upload unless --no-upload
    sweep     - one scheduled run now: drain backlog, refill pool toward
                queue_target_total, upload (3 fresh + 3 backlog), all capped
                by the 24h daily cap and the per-run budget. No daemon.
    schedule  - persist as an APScheduler daemon firing run_sweep on the
                RUN_TIMES crons (default 0 9 * * *). Same caps apply.
    source    - discovery + vetting only; print what passed. No render.
    assemble  - re-render from a saved plan (data/plans/<slug>.json)
    upload    - upload anything built but not yet published
    cleanup   - purge disposable runtime assets now
    test      - environment check
"""

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import assembler, cleanup, ranker, scriptwriter, sourcing, vetting
from .config import config
from .database import RankingDatabase
from .utils import ensure_dir, safe_slug, setup_logger, which_ffmpeg

logger = setup_logger(__name__, config.log_dir / 'ranking.log')

VARIANTS = ('normal', 'contrast')


def apply_contrast(clips: List[Dict], meta: Dict) -> Dict:
    """Rewrite a ranked plan into 'others vs this one' contrast copy.

    This has to run before the render and before the voice-over, not after:
    the titles are burned into the frames, so patching them into the saved
    plan afterwards produced a video with ordinary ranking labels and a plan
    that lied about it.
    """
    subject = (os.getenv('CONTRAST_SUBJECT') or config.contrast_subject
               or 'GUY').upper()
    total = len(clips)
    for index, clip in enumerate(clips):
        action = (clip.get('title') or 'THIS').upper() \
            .replace('OTHERS ', '').replace('BUT ', '')
        clip['title'] = (f'BUT THIS {subject}' if index == total - 1
                         else f'OTHERS {action}')[:72]
    meta = dict(meta)
    meta['video_title'] = f'OTHERS VS THIS {subject}'
    meta['upload_title'] = f"{meta['video_title']} #Shorts"
    return meta


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
        # candidate: if the budget has failed, the topic's queries or
        # thresholds are wrong and another 30 downloads will not fix it.
        # RANKING_REJECT_BUDGET now controls this; the floor is `needed` so a
        # build can never abort before one full pass of candidates.
        reject_budget = max(int(config.reject_budget), needed)
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
    def build(self, topic_name: str, upload: bool = True,
              variant: str = 'normal',
              channel: Optional[str] = None) -> Optional[Dict]:
        variant = (variant or 'normal').lower()
        if variant not in VARIANTS:
            variant = 'normal'
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

        # Fit all clips into the Shorts window before any copy or sound is
        # written: head-trimming moves the start, so the SFX cue and the plan
        # must be computed after the trim, never before.
        assembler.fit_windows(ordered)

        meta = scriptwriter.write_copy(topic_cfg, ordered)
        if variant == 'contrast':
            meta = apply_contrast(ordered, meta)
        slug = f"{topic_name}_{variant}_{int(time.time())}"
        scriptwriter.generate_voiceover(ordered, slug)
        scriptwriter.attach_sfx(ordered)

        plan = {
            'topic': topic_name,
            'variant': variant,
            'slug': slug,
            'video_title': meta['video_title'],
            'upload_title': meta['upload_title'],
            'description': meta['description'],
            'tags': meta['tags'],
            'channel': channel or topic_cfg.get('channel'),
            'clips': [
                {
                    'path': clip['local_path'],
                    'start': clip.get('clip_start', 0.0),
                    'duration': clip.get('clip_duration', 4.0),
                    'action_offset': clip.get('action_offset', 0.0),
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

        # The sources and stage renders are dead the moment the stitch lands:
        # the clips are retired in the database and can never be reused.
        cleanup.purge_runtime(reason=f'build:{slug}')

        if upload:
            self.upload_build(build_id, plan)
        return plan

    # -- upload --------------------------------------------------------
    def upload_build(self, build_id: int, plan: Dict) -> Optional[str]:
        """Upload one build, but never beyond the 24h daily cap.

        This is the hard ceiling every path honours (auto, once, drain,
        sweep). The per-run budget lives in the sweep orchestrator; this
        method only refuses to exceed ``upload_max_per_day`` in 24h.
        """
        cap = int(config.upload_max_per_day)
        if cap and self.db.uploads_since(3600 * 24) >= cap:
            logger.info('daily upload cap (%d/24h) reached; leaving %s queued',
                        cap, plan.get('local_path'))
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
            # YouTube has it and the row keeps the id, so the local export is
            # a duplicate. Keeping it is what filled the output folder.
            cleanup.delete_local_video(plan.get('local_path'))
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

    # -- sweep ---------------------------------------------------------
    def _upload_pending(self, cap: int) -> int:
        """Upload up to ``cap`` oldest built-but-unpublished videos."""
        uploaded = 0
        for row in self.db.pending_builds(limit=100):
            if uploaded >= cap:
                break
            path = row.get('local_path')
            if not path or not Path(path).exists():
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

    def _build_fresh(self, count: int, variant: str = 'normal') -> List[int]:
        """Build up to ``count`` new videos, rotating topics round-robin.

        Returns build ids for the videos actually produced. A topic whose
        build fails is skipped rather than retried in a tight loop: failed
        topics are set aside for this round so the next candidate can run.
        """
        topics = list(config.topic_names())
        if not topics:
            return []
        built_ids: List[int] = []
        attempted: set = set()
        tried = 0
        while len(built_ids) < count and tried < len(topics) * 3:
            tried += 1
            remaining = [t for t in topics if t not in attempted]
            topic = self.db.next_topic(remaining)
            if topic is None:
                break
            attempted.add(topic)
            this_variant = (VARIANTS[len(built_ids) % len(VARIANTS)]
                            if variant == 'mixed' else variant)
            plan = self.build(topic, upload=False, variant=this_variant)
            if plan and plan.get('build_id'):
                built_ids.append(int(plan['build_id']))
        return built_ids

    def run_sweep(self, variant: str = 'normal',
                  videos: Optional[int] = None) -> Dict:
        """One scheduled run, mirroring the shorts pipeline's sweep.

        Order:
          1. Drain the backlog (oldest first) up to the backlog share.
          2. Refill the pool toward ``queue_target_total`` with fresh builds.
          3. Upload from the freshly built pool up to the fresh share.
          4. If per-run budget remains, top up from the backlog again.

        Everything is clamped by the 24h daily cap and the per-run budget, so
        running the sweep by hand and the scheduler firing both respect the
        same limits.
        """
        used = self.db.uploads_since(3600 * 24)
        daily = int(config.upload_max_per_day)
        remaining_daily = max(0, daily - used)
        if remaining_daily <= 0:
            logger.info('SWEEP_SKIP daily cap reached (%d/%d)', used, daily)
            return {'built': 0, 'uploaded': 0,
                    'uploaded_backlog': 0, 'uploaded_fresh': 0,
                    'uploaded_topup': 0}

        per_run = max(0, min(int(config.upload_max_per_run), remaining_daily))
        if per_run <= 0:
            logger.info('SWEEP_SKIP no upload budget this run')
            return {'built': 0, 'uploaded': 0,
                    'uploaded_backlog': 0, 'uploaded_fresh': 0,
                    'uploaded_topup': 0}

        backlog_share = min(per_run, int(config.sweep_backlog_share))
        fresh_share = min(per_run, int(config.sweep_fresh_share))

        # 1. Backlog first: post the oldest unposted videos so the pool never
        #    ages into irrelevance.
        uploaded_backlog = self._upload_pending(backlog_share)
        logger.info('SWEEP_BACKLOG uploaded=%d', uploaded_backlog)

        # 2. Refill the ready pool toward the queue target. We build without
        #    uploading, then choose from the just-built pool below.
        pool = self.db.pending_builds_count()
        if videos is None:
            to_build = max(0, int(config.queue_target_total) - pool)
        else:
            to_build = max(0, int(videos))
        built_ids = self._build_fresh(to_build, variant) if to_build > 0 else []
        logger.info('SWEEP_REPLENISH built=%d (pool %d -> %d)',
                    len(built_ids), pool, self.db.pending_builds_count())

        # 3. Upload from the freshly built pool, newest freshness first.
        uploaded_fresh = 0
        remaining_run = max(0, per_run - uploaded_backlog)
        fresh_cap = min(fresh_share, remaining_run)
        for build_id in built_ids[:fresh_cap]:
            if not self._upload_one(build_id):
                continue
            uploaded_fresh += 1
        logger.info('SWEEP_FRESH uploaded=%d (cap %d)', uploaded_fresh, fresh_cap)

        # 4. Any leftover per-run budget goes back to the backlog.
        leftover = max(0, per_run - uploaded_backlog - uploaded_fresh)
        uploaded_topup = self._upload_pending(leftover) if leftover > 0 else 0

        total = uploaded_backlog + uploaded_fresh + uploaded_topup
        logger.info(
            'SWEEP_DONE built=%d uploaded=%d (backlog=%d fresh=%d topup=%d) '
            'daily=%d/%d',
            len(built_ids), total, uploaded_backlog, uploaded_fresh,
            uploaded_topup, used + total, daily,
        )
        return {
            'built': len(built_ids),
            'uploaded': total,
            'uploaded_backlog': uploaded_backlog,
            'uploaded_fresh': uploaded_fresh,
            'uploaded_topup': uploaded_topup,
        }

    def _upload_one(self, build_id: int) -> bool:
        """Upload a single build by id. Returns True on success."""
        row = self.db.build_row(build_id)
        if not row:
            return False
        path = row.get('local_path')
        if not path or not Path(path).exists():
            self.db.mark_failed(build_id, 'file_missing')
            return False
        try:
            plan = json.loads(row.get('plan_json') or '{}')
        except json.JSONDecodeError:
            plan = {}
        plan['local_path'] = path
        plan.setdefault('upload_title', row.get('title') or 'TOP 5')
        plan.setdefault('description', '')
        return bool(self.upload_build(build_id, plan))

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
    print(f'[ok]   copy model: {config.script_model} '
          f'(fallbacks: {", ".join(config.script_model_fallbacks) or "none"})')
    print(f'[ok]   purge after build: {config.cleanup_after_build} | '
          f'delete after upload: {config.delete_after_upload}')
    print(f'[ok]   disk: {cleanup.disk_report()}')
    return 1 if problems else 0


def _run_schedule(pipeline: RankingPipeline, args) -> int:
    """Persistent APScheduler daemon firing the sweep on RUN_TIMES crons.

    The same caps the one-shot sweep respects (24h daily cap, per-run budget)
    are enforced inside run_sweep, so a missed run or an overlapping fire can
    never blow through the daily ceiling.
    """
    try:
        from .scheduler import PipelineScheduler
    except ImportError as exc:
        logger.error('Scheduler needs APScheduler: %s (pip install apscheduler)',
                     exc)
        return 2

    run_times = [t.strip() for t in config.schedule_run_times if t.strip()]
    if not run_times:
        run_times = ['0 9 * * *']

    # Anti-burst jitter: a random minute offset keeps the batch from landing
    # on the same :00 cliff every day.
    jitter = int(config.schedule_jitter_minutes or 0)
    if jitter:
        jittered = []
        for cron in run_times:
            parts = cron.split()
            if len(parts) == 5:
                try:
                    parts[0] = str(random.randint(0, min(jitter, 59)))
                except (ValueError, TypeError):
                    pass
            jittered.append(' '.join(parts))
        run_times = jittered
        logger.info('Sweep times jittered by up to %d minute(s): %s',
                    jitter, ', '.join(run_times))

    variant = getattr(args, 'variant', 'normal') or 'normal'

    def job():
        try:
            pipeline.run_sweep(variant=variant)
        except Exception as exc:  # noqa: BLE001
            logger.error('Scheduled sweep failed: %s', exc, exc_info=True)

    sched = PipelineScheduler()
    for i, cron in enumerate(run_times):
        try:
            sched.add_daily_job(job, cron, job_id=f'ranking_sweep_{i}')
        except Exception as exc:
            logger.error('Bad cron entry %r: %s', cron, exc)

    sched.start()
    logger.info('Scheduler running (%s). Press Ctrl+C to stop.',
                ', '.join(run_times))
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info('Shutting down scheduler')
        sched.shutdown()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Ranking Shorts pipeline: sources clips, ranks them 5-1, '
                    'composes and publishes a countdown.')
    parser.add_argument('--mode', default='once',
                        choices=['once', 'auto', 'sweep', 'schedule',
                                 'source', 'assemble', 'upload', 'cleanup',
                                 'auth', 'test'])
    parser.add_argument('--topic', default=None,
                        help="topic key, or 'auto' to rotate")
    parser.add_argument('--variant', default='normal',
                        choices=['normal', 'contrast', 'mixed'],
                        help='content type; mixed alternates normal/contrast '
                             'across the videos of one run')
    parser.add_argument('--channel', default=None,
                        help='channel key to publish to, or to authenticate '
                             'with --mode auth (default: NXS)')
    parser.add_argument('--videos', type=int, default=None,
                        help='number of videos to build in --mode auto '
                             '(default: RANKING_VIDEOS_PER_RUN or 1)')
    parser.add_argument('--plan', default=None,
                        help='plan JSON for --mode assemble')
    parser.add_argument('--no-upload', action='store_true')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='keep downloaded clips and stage renders after a '
                             'build (needed for --mode assemble later)')
    parser.add_argument('--keep-uploads', action='store_true',
                        help='keep the local mp4 after a confirmed upload')
    parser.add_argument('--dry-run', action='store_true',
                        help='write the plan, render nothing')
    args = parser.parse_args(argv)

    if args.dry_run:
        config.dry_run = True
    if args.no_cleanup:
        config.cleanup_after_build = False
    if args.keep_uploads:
        config.delete_after_upload = False

    if args.mode == 'test':
        return environment_check()

    if args.mode == 'cleanup':
        print(f'before: {cleanup.disk_report()}')
        removed = cleanup.purge_runtime(reason='manual', force=True)
        print(f'after:  {cleanup.disk_report()}')
        print(f'removed {removed} disposable item(s)')
        return 0

    pipeline = RankingPipeline()

    if args.mode == 'auth':
        channel = (args.channel or 'NXS').strip()
        try:
            from googleapiclient.discovery import build
            _ = build  # touch: googleapiclient must be importable for OAuth
        except ImportError:
            print('google-api-python-client is not installed')
            return 1
        try:
            from .publisher import auth as ranking_auth
            channel_id = ranking_auth(channel)
        except Exception as exc:  # noqa: BLE001
            logger.error('auth failed for %r: %s', channel, exc)
            print(f'auth failed for channel {channel!r}: {exc}')
            return 1
        if not channel_id:
            print(f'channel {channel!r} not reachable after auth (no channel id)')
            return 1
        print(f'Authenticated channel {channel} (id={channel_id}).')
        print(f'Token saved near {config.oauth_token_file} as '
              f'youtube_token_ranking_{channel}.json')
        authed = {p.name[len('youtube_token_ranking_'):-5]
                  for p in Path(config.oauth_token_file).parent.glob(
                      'youtube_token_ranking_*.json')}
        print(f'Authenticated ranking channels now: {sorted(authed) or "(none)"}')
        return 0

    if args.mode == 'upload':
        count = pipeline.drain_queue()
        print(f'uploaded {count} build(s)')
        return 0

    if args.mode == 'sweep':
        result = pipeline.run_sweep(variant=args.variant,
                                    videos=args.videos)
        print(f"built {result['built']} | uploaded {result['uploaded']} "
              f"(backlog {result['uploaded_backlog']}, "
              f"fresh {result['uploaded_fresh']}, "
              f"topup {result['uploaded_topup']})")
        return 0

    if args.mode == 'schedule':
        return _run_schedule(pipeline, args)

    if args.mode == 'assemble':
        if not args.plan:
            print('--plan is required for --mode assemble')
            return 2
        plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
        output = assembler.assemble(plan)
        print(output or 'assembly failed')
        return 0 if output else 1

    topic = args.topic
    if topic and topic.lower() == 'auto':
        topic = None
        args.mode = 'auto' if args.mode == 'once' else args.mode

    if args.mode == 'auto':
        # Build N videos per run (default 1). After a build the used clips are
        # retired, so the next iteration sources fresh material and picks the
        # least-recently-run topic again - each video is a distinct TOP-5.
        topics = config.topic_names()
        if not topics:
            print('no topics configured in config/ranking.yaml')
            return 2

        wanted = args.videos if args.videos and args.videos > 0 else int(
            config.get('videos_per_run', 1) or 1)
        wanted = max(1, wanted)
        built = 0
        for index in range(wanted):
            variant = (VARIANTS[index % len(VARIANTS)]
                       if args.variant == 'mixed' else args.variant)
            # Try each topic at most once per video so a starved topic (every
            # candidate already rejected/used) cannot block the rest.
            attempted: list = []
            plan = None
            while len(attempted) < len(topics):
                remaining = [t for t in topics if t not in attempted]
                candidate = pipeline.db.next_topic(remaining)
                if candidate is None:
                    break
                attempted.append(candidate)
                plan = pipeline.build(candidate, upload=not args.no_upload,
                                      variant=variant, channel=args.channel)
                if plan:
                    break
            if plan:
                built += 1
                logger.info('AUTO_BUILT %d/%d [%s] %s: %s', built, wanted,
                            variant, plan.get('topic'),
                            plan.get('upload_title', ''))
        print(f'built {built}/{wanted} video(s)')
        return 0 if built else 1

    topic = topic or pipeline.db.next_topic(config.topic_names()) \
        or (config.topic_names() or [None])[0]
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

    variant = 'normal' if args.variant == 'mixed' else args.variant
    plan = pipeline.build(topic, upload=not args.no_upload, variant=variant,
                          channel=args.channel)
    return 0 if plan else 1


if __name__ == '__main__':
    sys.exit(main())
