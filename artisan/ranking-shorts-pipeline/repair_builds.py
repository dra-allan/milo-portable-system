#!/usr/bin/env python3
"""Triage the ranking build ledger after the move to the VPS.

    python repair_builds.py --db data/ranking.db
    python repair_builds.py --db data/ranking.db \\
        --remap 'C:\\Users\\user\\Desktop\\Milo Video Factory=/srv/milo' --apply

THE STATE THIS EXISTS FOR
-------------------------
74 builds: 55 uploaded, 19 failed. Every one of the 19 failed with
``file_missing`` or ``upload_failed`` against a path like
``C:\\Users\\user\\Desktop\\Milo Video Factory\\...`` -- the old PC's layout,
which cannot exist on this box. The output directory is empty and
``pending_builds()`` returns nothing, so the lane reports "no pending builds"
and looks idle rather than broken.

Be clear about what this can and cannot fix. It can recover a build whose file
survived the move under a different root: ``--remap`` rewrites the prefix, and
any build whose file is then found goes back to ``built`` so the normal upload
path picks it up. It cannot conjure a rendered video that was left on a machine
that is gone -- those get an explicit ``failed:file_lost`` so they stop looking
like something a retry might fix.

What it will NOT do is unwedge the lane. Ranking has produced nothing new since
~8/17 because downloads are blocked upstream (see yt_doctor.py in the shorts
lane). This only cleans the ledger so that, once extraction works, "no pending
builds" means the queue is genuinely empty.

Dry run by default.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent


def _default_db() -> Optional[Path]:
    for candidate in (ROOT / 'data' / 'ranking.db',
                      ROOT / 'data' / 'ranking-shorts.db'):
        if candidate.exists():
            return candidate
    try:
        sys.path.insert(0, str(ROOT))
        from src.config import config  # type: ignore
        path = Path(getattr(config, 'db_path', '') or '')
        return path if path.exists() else None
    except Exception:
        return None


def _remapped(path: str, remap: Optional[Tuple[str, str]]) -> str:
    if not remap or not path:
        return path
    old, new = remap
    if path.lower().startswith(old.lower()):
        tail = path[len(old):].replace('\\', '/').lstrip('/')
        return str(Path(new) / tail)
    return path


def report(conn) -> None:
    print('\nBUILD LEDGER')
    total = conn.execute('SELECT COUNT(*) FROM builds').fetchone()[0]
    print(f'  builds total        {total}')
    for status, count in conn.execute(
            'SELECT status, COUNT(*) FROM builds GROUP BY status '
            'ORDER BY 2 DESC').fetchall():
        print(f'    {status:<32} {count}')

    missing = 0
    windows = 0
    for row in conn.execute(
            'SELECT local_path FROM builds WHERE local_path IS NOT NULL '
            "AND local_path != ''").fetchall():
        path = row[0]
        if '\\' in path or (len(path) > 2 and path[1] == ':'):
            windows += 1
        if not Path(path).exists():
            missing += 1
    print(f'  paths not on this box  {missing}')
    print(f'  paths in Windows form  {windows}'
          + ('  <- these are from the old PC' if windows else ''))


def plan(conn, remap: Optional[Tuple[str, str]]):
    changes = []
    rows = conn.execute(
        'SELECT id, topic, title, local_path, status, youtube_id FROM builds '
        'ORDER BY id').fetchall()
    for row in rows:
        status = row['status'] or ''
        if status == 'uploaded' or row['youtube_id']:
            continue                      # already published, leave alone
        path = row['local_path'] or ''
        new_path = _remapped(path, remap)
        exists = bool(new_path) and Path(new_path).exists()

        if exists and status != 'built':
            # Recoverable: the render survived the move.
            wanted = 'built'
        elif not exists and status.startswith('failed'):
            wanted = 'failed:file_lost'
        elif not exists and status == 'built':
            # Worse than a failure: it is sitting in the pending queue and will
            # fail on every upload attempt forever.
            wanted = 'failed:file_lost'
        else:
            continue

        if wanted != status or new_path != path:
            changes.append({'id': row['id'], 'topic': row['topic'],
                            'title': (row['title'] or '')[:48],
                            'status_from': status or '(empty)',
                            'status_to': wanted,
                            'path_from': path, 'path_to': new_path,
                            'exists': exists})
    return changes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--db')
    parser.add_argument('--remap', default='', help='OLD=NEW path prefix rewrite')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else _default_db()
    if not db_path or not Path(db_path).exists():
        print('Could not find ranking.db. Pass --db /path/to/ranking.db')
        return 1

    remap = None
    if args.remap:
        if '=' not in args.remap:
            parser.error('--remap needs the form OLD=NEW')
        old, new = args.remap.split('=', 1)
        remap = (old.strip(), new.strip())

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        print(f'database: {db_path}')
        report(conn)

        changes = plan(conn, remap)
        if not changes:
            print('\nLedger is consistent. If there are still no pending '
                  'builds, the blocker is upstream: nothing can be built while '
                  'downloads are failing. Run the shorts lane\'s '
                  'yt_doctor.py --probe first.')
            return 0

        recovered = [c for c in changes if c['status_to'] == 'built']
        lost = [c for c in changes if c['status_to'] != 'built']
        print(f'\n{len(changes)} build(s) to change'
              f'{"" if args.apply else " (DRY RUN -- nothing written)"}:')
        if recovered:
            print(f'  recoverable -> built: {len(recovered)}')
            for item in recovered[:8]:
                print(f"    #{item['id']} {item['topic']}: {item['title']}")
        if lost:
            print(f'  unrecoverable -> failed:file_lost: {len(lost)}')
            for item in lost[:8]:
                print(f"    #{item['id']} {item['topic']}: {item['path_from'][:70]}")
            if len(lost) > 8:
                print(f'    ... and {len(lost) - 8} more')

        if not args.apply:
            print('\nRe-run with --apply to write these changes.')
            if not recovered and remap is None:
                print('Tip: if the renders were copied to this box under a new '
                      'root, pass --remap to point the ledger at them before '
                      'writing them off.')
            return 0

        for change in changes:
            conn.execute('UPDATE builds SET status = ?, local_path = ? '
                         'WHERE id = ?',
                         (change['status_to'], change['path_to'], change['id']))
        conn.commit()
        print('\nApplied.')
        report(conn)
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
