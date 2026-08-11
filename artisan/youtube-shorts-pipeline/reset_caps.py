"""List and lift the daily upload caps so the pipeline can post again.

The per-source (UPLOAD_MAX_PER_SOURCE) and per-channel (UPLOAD_MAX_PER_CHANNEL)
caps are enforced by counting ``uploaded_at`` timestamps in the last 24h window
in processed_videos.db. A full sweep can run and still post nothing when those
counters are full. This tool shows the caps + current usage, then lets you lift
them by clearing the 24h counters.

Already-published shorts keep their youtube_short_id, so they are never
re-uploaded (the uploader only picks rows where youtube_short_id IS NULL).

Usage:
    python reset_caps.py            list caps + usage, then prompt to lift
    python reset_caps.py --list     list only, no changes
    python reset_caps.py --yes      list + lift without prompting
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import config  # noqa: E402
from src.database import PipelineDatabase  # noqa: E402


def _fmt(value, unit='', unlimited='unlimited'):
    if value in (None, '', 0):
        return unlimited
    return f"{value} {unit}".strip()


def list_caps() -> PipelineDatabase:
    db = PipelineDatabase()
    print('=' * 64)
    print('  UPLOAD CAPS (config -> .env / defaults)')
    print('=' * 64)
    print(f"  UPLOAD_ENABLED        : {config.upload_enabled}")
    print(f"  UPLOAD_MAX_PER_RUN    : {_fmt(config.upload_max_per_run)}")
    print(f"  UPLOAD_MAX_PER_SOURCE : {config.upload_max_per_source} clips/source/24h")
    print(f"  UPLOAD_MAX_PER_CHANNEL: {config.upload_max_per_channel} shorts/channel/24h")
    print(f"  SCHEDULE_MAX_VIDEOS   : {config.schedule_max_videos} videos/run/niche")
    print(f"  SCHEDULE_MAX_TOTAL    : {_fmt(config.schedule_max_total)}")
    print()

    print('-' * 64)
    print('  USED IN LAST 24h (the counters that block posting)')
    print('-' * 64)
    with db._connect() as conn:
        ch_rows = conn.execute(
            """SELECT upload_channel, COUNT(*) AS used
               FROM generated_shorts
               WHERE youtube_short_id IS NOT NULL
                 AND uploaded_at IS NOT NULL
                 AND uploaded_at >= datetime('now', '-24 hours')
               GROUP BY upload_channel ORDER BY used DESC"""
        ).fetchall()
        src_rows = conn.execute(
            """SELECT source_video_id, COUNT(*) AS used
               FROM generated_shorts
               WHERE youtube_short_id IS NOT NULL
                 AND uploaded_at IS NOT NULL
                 AND uploaded_at >= datetime('now', '-24 hours')
               GROUP BY source_video_id ORDER BY used DESC"""
        ).fetchall()
        pending = conn.execute(
            """SELECT COUNT(*) FROM generated_shorts
               WHERE youtube_short_id IS NULL
                 AND local_path IS NOT NULL AND local_path != ''"""
        ).fetchone()[0]

    if ch_rows:
        for r in ch_rows:
            cap = config.upload_max_per_channel
            print(f"  channel {r['upload_channel'] or '?'!r}: "
                  f"{r['used']}/{cap} used")
    else:
        print('  channel usage: none in last 24h')
    if src_rows:
        for r in src_rows[:12]:
            cap = config.upload_max_per_source
            print(f"  source  {r['source_video_id'][:12]}: "
                  f"{r['used']}/{cap} used")
        if len(src_rows) > 12:
            print(f"  ... and {len(src_rows) - 12} more source(s)")
    else:
        print('  source usage: none in last 24h')
    print(f"  pending clips ready to post: {pending}")
    print(f"  database: {db.db_path}")
    return db


def lift_caps(db: PipelineDatabase) -> None:
    backup = db.db_path.with_name(
        f"processed_videos.capsreset-{datetime.now():%Y%m%d-%H%M%S}.db")
    try:
        shutil.copy2(str(db.db_path), str(backup))
        print(f"  backed up DB -> {backup.name}")
    except OSError as exc:
        print(f"  WARN: could not back up DB: {exc}")
    with db._connect() as conn:
        cur = conn.execute(
            "UPDATE generated_shorts SET uploaded_at = NULL "
            "WHERE uploaded_at IS NOT NULL")
    print(f"  cleared uploaded_at on {cur.rowcount} row(s)")
    print('  caps lifted: per-source and per-channel 24h counters are now 0.')
    print('  Re-run the sweep (or option 4 upload) and it will post.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--list', action='store_true',
                        help='list caps + usage only, make no changes')
    parser.add_argument('--yes', action='store_true',
                        help='lift caps without prompting')
    args = parser.parse_args()

    db = list_caps()

    if args.list:
        print('\n  (no changes made -- list only)')
        return 0

    if not args.yes:
        try:
            answer = input('\n  Lift caps now? (y/N): ').strip().lower()
        except EOFError:
            answer = 'n'
        if answer != 'y':
            print('  Cancelled. No changes made.')
            return 0

    print()
    lift_caps(db)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
