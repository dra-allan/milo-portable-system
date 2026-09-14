"""List and lift the daily upload caps for Ranking Shorts pipeline.

Caps: per-day (UPLOAD_MAX_PER_DAY), per-run (UPLOAD_MAX_PER_RUN), per-channel (UPLOAD_MAX_PER_CHANNEL).
Window: fixed local midnight boundary (auto-resets at midnight).
This script clears today's uploaded_at timestamps so a run can post again.

Already-published shorts keep their youtube_id, so they are never re-uploaded.

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
from src.database import RankingDatabase  # noqa: E402


def _fmt(value, unit='', unlimited='unlimited'):
    if value in (None, '', 0):
        return unlimited
    return f"{value} {unit}".strip()


def list_caps() -> RankingDatabase:
    db = RankingDatabase(config.db_path)
    print('=' * 64)
    print('  RANKING UPLOAD CAPS (config -> .env / defaults)')
    print('=' * 64)
    print(f"  UPLOAD_MAX_PER_DAY    : {_fmt(config.upload_max_per_day)}")
    print(f"  UPLOAD_MAX_PER_RUN    : {_fmt(config.upload_max_per_run)}")
    print(f"  UPLOAD_MAX_PER_CHANNEL: {_fmt(config.upload_max_per_channel)}")
    print(f"  QUEUE_TARGET_TOTAL    : {_fmt(config.queue_target_total)}")
    print(f"  SWEEP_FRESH_SHARE     : {config.sweep_fresh_share}")
    print(f"  SWEEP_BACKLOG_SHARE   : {config.sweep_backlog_share}")
    print()

    print('-' * 64)
    print('  USED TODAY (since local midnight)')
    print('-' * 64)
    with db._connect() as conn:
        ch_rows = conn.execute(
            """SELECT channel, COUNT(*) AS used
               FROM builds
               WHERE status = 'uploaded'
                 AND uploaded_at IS NOT NULL
                 AND datetime(uploaded_at, 'localtime') >= datetime('now', 'localtime', 'start of day')
               GROUP BY channel ORDER BY used DESC"""
        ).fetchall()
        pending = conn.execute(
            """SELECT COUNT(*) FROM builds WHERE status = 'built'"""
        ).fetchone()[0]

    if ch_rows:
        for r in ch_rows:
            cap = config.upload_max_per_channel
            print(f"  channel {r['channel'] or '?'!r}: {r['used']}/{cap} used")
    else:
        print('  channel usage: none today')
    print(f"  pending builds ready to post: {pending}")
    print(f"  database: {db.path}")
    return db


def lift_caps(db: RankingDatabase) -> None:
    backup = db.path.with_name(f"ranking.capsreset-{datetime.now():%Y%m%d-%H%M%S}.db")
    try:
        shutil.copy2(str(db.path), str(backup))
        print(f"  backed up DB -> {backup.name}")
    except OSError as exc:
        print(f"  WARN: could not back up DB: {exc}")
    with db._connect() as conn:
        cur = conn.execute(
            "UPDATE builds SET uploaded_at = NULL WHERE uploaded_at IS NOT NULL")
    print(f"  cleared uploaded_at on {cur.rowcount} row(s)")
    print('  caps lifted: per-channel daily counters are now 0.')
    print('  Re-run the sweep (or --mode upload) and it will post.')


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