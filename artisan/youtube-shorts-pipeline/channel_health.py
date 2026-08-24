"""Per-channel distribution health check: find suppression before it costs weeks.

2026-08-24 fleet audit: capital_mindset was suppressed by YouTube around
2026-08-11 (cadence flooded to 15-21 uploads/day) and nobody noticed for 13
days because the pipeline never reads view counts back. This script is the
read-back loop the pipeline driver runs daily:

    python channel_health.py             report only, changes nothing
    python channel_health.py --apply     write/clear suppression flags

For every authenticated channel it pulls the newest uploads via the Data API,
computes the median view count over uploads older than --min-age-hours, and
flags channels whose median is below --threshold. A flagged channel lands in
data/suppressed_channels.yaml; src/suppression.is_suppressed() then blocks its
uploads everywhere until the entry expires (7 days) or the median recovers and
--apply clears it.

Exit code 0 always: health reporting must never break the driver's sweep.
"""
import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import config  # noqa: E402
from src import suppression  # noqa: E402


def _uploader(slug: str):
    from src.uploader import YouTubeUploader
    # verify_identity=False: the health check has no niche context, so the
    # content-lane assert would refuse a perfectly valid token. Identity
    # itself (token owns the channel) is still checked inside __init__.
    return YouTubeUploader(channel=slug, niche='', verify_identity=False)


def _fetch_recent(uploader, max_results: int = 12):
    """[(video_id, published_at, views)] for the channel's newest uploads."""
    yt = uploader.youtube
    channel_id = uploader.actual_channel_id
    if not channel_id:
        return []
    uploads = f'UU{channel_id[2:]}'
    items = yt.playlistItems().list(
        part='contentDetails', playlistId=uploads,
        maxResults=min(max_results, 50)).execute()
    ids = [it['contentDetails']['videoId']
           for it in items.get('items', [])][:max_results]
    if not ids:
        return []
    videos = yt.videos().list(part='statistics,snippet', id=','.join(ids)).execute()
    out = []
    seen = set()
    for v in videos.get('items', []):
        vid = v['id']
        if vid in seen:
            continue
        seen.add(vid)
        published = v['snippet'].get('publishedAt', '')
        views = int(v.get('statistics', {}).get('viewCount', 0) or 0)
        out.append((vid, published, views))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window', type=int, default=10,
                        help='newest uploads to evaluate per channel')
    parser.add_argument('--min-age-hours', type=float, default=24.0,
                        help='only count uploads older than this (views need time)')
    parser.add_argument('--threshold', type=int, default=15,
                        help='median views below this = suppressed')
    parser.add_argument('--min-sample', type=int, default=4,
                        help='need at least this many aged uploads to judge')
    parser.add_argument('--apply', action='store_true',
                        help='write/clear suppression flags in data/')
    args = parser.parse_args()

    slugs = config.authenticated_channels()
    if not slugs:
        print('no authenticated channels found (no youtube_token_*.json)')
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.min_age_hours)
    rows = []
    for slug in sorted(slugs):
        try:
            uploader = _uploader(slug)
            recent = _fetch_recent(uploader, args.window + 4)
        except Exception as exc:
            rows.append({'channel': slug, 'status': 'AUTH/API ERROR',
                         'median': None, 'n': 0, 'note': str(exc)[:80]})
            continue
        aged = [(v, p, c) for (v, p, c) in recent
                if _parse_ts(p) and _parse_ts(p) <= cutoff]
        views = [c for (_, _, c) in aged[:args.window]]
        median = int(statistics.median(views)) if views else None
        n = len(aged[:args.window])
        if median is None:
            status = 'NO AGED UPLOADS'
        elif n < args.min_sample:
            status = 'SAMPLE SMALL'
        elif median < args.threshold:
            status = 'SUPPRESSED'
        else:
            status = 'healthy'
        rows.append({'channel': slug, 'status': status, 'median': median,
                     'n': n, 'note': ''})
        if args.apply:
            if status == 'SUPPRESSED':
                was = suppression.is_suppressed(slug)
                suppression.mark_suppressed(slug, median, n, args.threshold)
                if not was:
                    print(f'  -> flagged {slug}: median {median} views '
                          f'over {n} uploads; uploads paused')
            elif status == 'healthy' and suppression.is_suppressed(slug):
                suppression.mark_healthy(slug)
                print(f'  -> {slug} recovered; flag cleared')

    print('=' * 72)
    print(f"  CHANNEL HEALTH  {now:%Y-%m-%d %H:%M UTC}  "
          f"(window={args.window}, min-age={args.min_age_hours:g}h, "
          f"threshold={args.threshold})")
    print('=' * 72)
    width = max(len(r['channel']) for r in rows) + 2
    for r in rows:
        median = '-' if r['median'] is None else r['median']
        note = f"  {r['note']}" if r['note'] else ''
        print(f"  {r['channel']:<{width}} {r['status']:<16} "
              f"median={median:>7}  n={r['n']}{note}")
    flagged = [r['channel'] for r in rows if r['status'] == 'SUPPRESSED']
    if flagged:
        print(f"\n  SUPPRESSED: {', '.join(flagged)}")
        print('  uploads paused via data/suppressed_channels.yaml'
              + ('' if args.apply else '  (--apply to enforce)'))
    else:
        print('\n  no suppressed channels')
    return 0


def _parse_ts(raw: str):
    try:
        return datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


if __name__ == '__main__':
    sys.exit(main())
