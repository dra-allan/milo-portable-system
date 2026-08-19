"""Queue-first uploader: spends the channel budget AND the per-source budget.

THE BUG THIS FILE EXISTED WITH
------------------------------
``config.upload_max_per_source`` has been set to 3/day since the 2026-08-09
"five identical-title shorts in nine minutes" incident. This loop never read
it. It asked ``uploaded_count_for_channel_since`` only, so on 2026-08-19
``flick_shorts`` published six clips from ``uUAH82U_jXU`` and
``capital_mindset`` published six from ``yveLqk3DCNs`` -- both perfectly inside
the 6/channel cap, both double the cadence rule that exists because YouTube
test-fights near-identical clips from one source against each other.

The per-source counter was already in the database. Only the loop was missing.

WHY THE SELECTION IS A PURE FUNCTION NOW
----------------------------------------
The previous version was one statement per line, semicolon-chained, with the
budget arithmetic inlined into the same expression as the printing. A missing
check in that shape is genuinely hard to see, and it could not be tested
without Google credentials. :func:`select_uploads` takes rows and budgets and
returns a plan; ``tests/test_per_source_cap.py`` exercises the exact 8/19
scenario. The IO half stays here.

ROUND-ROBIN, NOT FIRST-COME
---------------------------
Within a channel the plan alternates between source videos rather than draining
one source and then moving on. Without that, a per-source cap alone still
produces "3 from A, 3 from B" back to back; interleaving spreads the same
uploads across different sources, which is the actual goal of the cadence rule.
"""
import argparse
import random
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .config import config
from .database import PipelineDatabase
from .uploader import YouTubeUploader

try:
    from .title_optimizer import optimize_title
except Exception:  # pragma: no cover - optimizer is optional
    optimize_title = None


def _delete_after_upload(path, source_id, segment, logger=print) -> bool:
    if not path:
        return False
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
            logger(f'  CLEANED {source_id}#{segment}: {p.name}')
            return True
    except OSError as exc:
        logger(f'  CLEANUP_WARN {source_id}#{segment}: {exc}')
    return False


# ---------------------------------------------------------------------------
# Selection (pure, tested)
# ---------------------------------------------------------------------------
def select_uploads(rows: List[Dict], channel_budgets: Dict[str, int],
                   source_budgets: Dict[str, int],
                   channel_of: Callable[[Dict], str],
                   run_limit: Optional[int] = None
                   ) -> Tuple[List[Tuple[str, Dict]], Dict[str, int]]:
    """Choose which queued clips to publish, respecting every cap.

    Args:
        rows: queued clip rows, oldest first.
        channel_budgets: channel key -> uploads still allowed today.
        source_budgets: source video id -> uploads still allowed today.
            Copies are used internally; the caller's dicts are never mutated,
            because the caller prints them as the *starting* budget afterwards.
        channel_of: maps a row to its upload channel key.
        run_limit: hard ceiling for this run (0/None = no extra ceiling).

    Returns:
        ``(plan, skips)`` where plan is an ordered list of
        ``(channel_key, row)`` and skips counts CLIPS dropped, per reason:
        ``no_channel``, ``channel_cap``, ``source_cap``, ``run_limit``.
        Clip counts, not source counts -- the summary line is the only place
        anyone checks whether the cadence cap actually bit, so "3 clips held
        back" has to read as 3.
    """
    channel_left = dict(channel_budgets)
    source_left = dict(source_budgets)
    skips = {'no_channel': 0, 'channel_cap': 0, 'source_cap': 0, 'run_limit': 0}

    # channel -> source -> [rows], insertion-ordered so "oldest first" survives.
    buckets: 'OrderedDict[str, OrderedDict[str, List[Dict]]]' = OrderedDict()
    for row in rows:
        channel = channel_of(row)
        if not channel:
            skips['no_channel'] += 1
            continue
        source = str(row.get('source_video_id') or '')
        buckets.setdefault(channel, OrderedDict()).setdefault(source, []).append(row)

    plan: List[Tuple[str, Dict]] = []
    limit = int(run_limit or 0)

    for channel, by_source in buckets.items():
        # Sources with the fewest queued clips go first: a source with one clip
        # left should not be starved by a source sitting on twenty.
        order = sorted(by_source.keys(), key=lambda s: len(by_source[s]))
        pointers = {s: 0 for s in order}
        active = list(order)

        while active:
            progressed = False
            for source in list(active):
                index = pointers[source]
                queue = by_source[source]
                if index >= len(queue):
                    active.remove(source)
                    continue
                row = queue[index]
                pointers[source] = index + 1
                progressed = True

                if limit and len(plan) >= limit:
                    skips['run_limit'] += 1
                    continue
                if channel_left.get(channel, 0) <= 0:
                    skips['channel_cap'] += 1
                    continue
                if source_left.get(source, 0) <= 0:
                    # THE CHECK THAT WAS MISSING. Counted separately from
                    # channel_cap so the summary distinguishes "this channel is
                    # done for today" from "this source is done for today".
                    #
                    # Every clip still queued behind this one is blocked by the
                    # same cap, so they are all counted here before the source
                    # leaves the rotation -- otherwise the summary reports 1
                    # where 3 clips were held back.
                    skips['source_cap'] += len(queue) - index
                    active.remove(source)
                    continue

                plan.append((channel, row))
                channel_left[channel] -= 1
                source_left[source] -= 1
            if not progressed:
                break

    return plan, skips


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _title_and_description(row: Dict, channel: str) -> Tuple[str, str]:
    niche = row.get('niche') or channel
    hook = ' '.join(str(row.get('title') or channel).split()).strip()
    title = hook
    if optimize_title:
        try:
            title = optimize_title(hook, niche=niche, keywords=[],
                                   clip_index=row['segment_index'])
        except Exception:
            title = hook
    title = f'{title} #{niche} #Shorts'[:100]
    description = (
        f"Full video: https://youtube.com/watch?v={row['source_video_id']}\n\n"
        f'Follow for more {niche} content!\n#Shorts #{niche}'
    )
    return title, description


def run(niche: Optional[str] = None, channel_override: Optional[str] = None,
        limit: Optional[int] = None) -> int:
    db = PipelineDatabase()
    rows = db.unuploaded_shorts(limit=max(100, (limit or 999999) * 3))
    if niche:
        rows = [r for r in rows if r.get('niche') == niche]

    def channel_of(row: Dict) -> str:
        return channel_override or config.get_niche_channel(row.get('niche') or '')

    if channel_override:
        rows = [r for r in rows if channel_of(r) == channel_override]
    if not rows:
        print('UPLOAD SUMMARY\n  queued: 0\n  uploaded: 0\n'
              '  message: no pending clips')
        return 0

    channel_cap = int(getattr(config, 'upload_max_per_channel', 6) or 6)
    source_cap = int(getattr(config, 'upload_max_per_source', 3) or 3)
    run_limit = int(limit or getattr(config, 'upload_max_per_run', 0) or 0)

    channels: List[str] = []
    for row in rows:
        channel = channel_of(row)
        if channel and channel not in channels:
            channels.append(channel)

    authed = set(config.authenticated_channels())
    default_token = Path(config.oauth_token_file).exists()

    def is_authenticated(channel: str) -> bool:
        return (not authed and default_token) or channel in authed

    channel_budgets = {
        channel: max(0, channel_cap - db.uploaded_count_for_channel_since(channel))
        for channel in channels
    }
    sources = {str(r.get('source_video_id') or '') for r in rows}
    source_budgets = {
        source: max(0, source_cap - db.uploaded_count_for_source_since(source))
        for source in sources
    }

    print('UPLOAD RUN')
    print(f'  queued: {len(rows)}')
    print(f'  caps: {channel_cap}/channel/day, {source_cap}/source/day'
          + (f', {run_limit} this run' if run_limit else ''))
    for channel in channels:
        print(f'  {channel}: budget {channel_budgets[channel]}/{channel_cap}, '
              f'authenticated={"yes" if is_authenticated(channel) else "no"}')
    exhausted = [s for s, left in source_budgets.items() if left <= 0]
    if exhausted:
        print(f'  {len(exhausted)} source(s) already at the {source_cap}/day '
              f'cadence cap: {", ".join(sorted(exhausted)[:5])}'
              + (' ...' if len(exhausted) > 5 else ''))

    # Unauthenticated channels are removed before planning, so their clips are
    # reported as unauthenticated rather than silently consuming plan slots.
    summary = {'queued': len(rows), 'uploaded': 0, 'cleaned': 0, 'missing': 0,
               'unauthenticated': 0, 'channel_cap_skips': 0,
               'source_cap_skips': 0, 'no_channel': 0, 'run_limit_skips': 0,
               'failed': 0}
    plannable: List[Dict] = []
    for row in rows:
        channel = channel_of(row)
        if channel and not is_authenticated(channel):
            summary['unauthenticated'] += 1
            continue
        plannable.append(row)

    plan, skips = select_uploads(plannable, channel_budgets, source_budgets,
                                 channel_of, run_limit=run_limit)
    summary['channel_cap_skips'] = skips['channel_cap']
    summary['source_cap_skips'] = skips['source_cap']
    summary['no_channel'] = skips['no_channel']
    summary['run_limit_skips'] = skips['run_limit']

    if not plan:
        print('\nUPLOAD SUMMARY')
        for key, value in summary.items():
            print(f'  {key}: {value}')
        print('  status: nothing eligible (every channel or source is at its '
              'daily cap)')
        return 0

    uploaders: Dict[str, Optional[YouTubeUploader]] = {}
    remaining = dict(channel_budgets)

    for channel, row in plan:
        path = row.get('local_path') or ''
        if not path or not Path(path).exists():
            # Stale absolute paths from the old Windows box land here. Marked so
            # they stop being replanned on every single sweep.
            summary['missing'] += 1
            print(f"  SKIP {row['source_video_id']}#{row['segment_index']}: "
                  'file missing')
            db.update_clip_status(row['source_video_id'], row['segment_index'],
                                  'file_missing')
            continue

        if channel not in uploaders:
            try:
                uploaders[channel] = YouTubeUploader(channel=channel)
            except Exception as exc:
                # Includes ChannelIdentityError: a channel whose token resolves
                # to the wrong YouTube channel is skipped entirely, never
                # retried, and never uploaded to.
                print(f'  ERROR {channel}: {str(exc)[:400]}')
                uploaders[channel] = None
        uploader = uploaders.get(channel)
        if uploader is None:
            summary['failed'] += 1
            continue

        title, description = _title_and_description(row, channel)
        print(f"  UPLOAD {row['source_video_id']}#{row['segment_index']} -> {channel}")
        try:
            vid = uploader.upload_short(path, title, description,
                                        [row.get('niche') or channel, 'Shorts'])
        except Exception as exc:
            vid = None
            print(f'  ERROR {channel}: {str(exc)[:200]}')
        if not vid:
            summary['failed'] += 1
            continue

        db.mark_short_uploaded(row['source_video_id'], row['segment_index'],
                               vid, channel=channel)
        # status is the human-readable mirror of youtube_short_id; leaving it at
        # 'queued' after a successful upload is what made 72 published rows look
        # pending in the DB.
        db.update_clip_status(row['source_video_id'], row['segment_index'],
                              'uploaded')
        summary['uploaded'] += 1
        remaining[channel] = max(0, remaining.get(channel, 0) - 1)
        if _delete_after_upload(path, row['source_video_id'],
                                row['segment_index']):
            summary['cleaned'] += 1
        if config.upload_pacing_max:
            time.sleep(random.uniform(config.upload_pacing_min,
                                      config.upload_pacing_max))

    print('\nUPLOAD SUMMARY')
    for key, value in summary.items():
        print(f'  {key}: {value}')
    for channel in channels:
        print(f'  {channel}: remaining_budget={remaining.get(channel, 0)}')
    return 0 if summary['failed'] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--niche')
    parser.add_argument('--channel')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    return run(args.niche, args.channel, args.limit)


if __name__ == '__main__':
    raise SystemExit(main())
