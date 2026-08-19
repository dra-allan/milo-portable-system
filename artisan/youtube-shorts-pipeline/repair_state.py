#!/usr/bin/env python3
"""Make the shorts database say what is actually true.

    python repair_state.py                       # report only (default)
    python repair_state.py --apply
    python repair_state.py --remap 'C:\\Users\\user\\Desktop\\Milo Video Factory=/srv/milo' --apply

TWO LIES IN THE CURRENT STATE
-----------------------------
**1. Every row says queued.** All 72 ``generated_shorts`` rows carry
``status='queued'`` even though 55 of them are published. Nothing was ever
writing that column on upload -- ``mark_short_uploaded`` set
``youtube_short_id`` and left ``status`` alone -- so the real gate
(``youtube_short_id IS NULL``) and the visible field disagreed.

That is not cosmetic. ``get_queue_health`` and ``get_queued_clips_for_upload``
both filter on ``status='queued'``, so every published clip still counted toward
the queue: the queue looked permanently full, which suppressed discovery, which
is part of why nothing new was being pulled.

**2. Rows point at a machine that no longer exists.** ``local_path`` values like
``C:\\Users\\user\\Desktop\\Milo Video Factory\\...`` are from the old PC. On the
VPS they can never resolve, so each sweep re-selected them, tried to upload,
failed on "file missing", and left them queued to be tried again next sweep.

WHAT THIS DOES
--------------
* backfills ``status`` from ``youtube_short_id`` (the source of truth),
* marks unresolvable rows ``file_missing`` so they leave the queue,
* optionally rewrites a stale path prefix (``--remap OLD=NEW``) and restores any
  row whose file is found again.

Dry run by default, because a repair tool that writes on invocation turns a bad
guess into a permanent one.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _default_db() -> Optional[Path]:
    try:
        from src.config import config
        return Path(config.db_path)
    except Exception:
        candidate = ROOT / 'data' / 'processed_videos.db'
        return candidate if candidate.exists() else None


def _rows(conn) -> List[sqlite3.Row]:
    return conn.execute(
        'SELECT id, source_video_id, segment_index, local_path, '
        'youtube_short_id, status FROM generated_shorts ORDER BY id'
    ).fetchall()


def _remapped(path: str, remap: Optional[Tuple[str, str]]) -> str:
    if not remap or not path:
        return path
    old, new = remap
    # Windows paths arrive with backslashes; compare case-insensitively because
    # the original drive letter casing is not reliable.
    if path.lower().startswith(old.lower()):
        tail = path[len(old):].replace('\\', '/').lstrip('/')
        return str(Path(new) / tail)
    return path


def plan(conn, remap: Optional[Tuple[str, str]]):
    """Work out every change without making any. Returns a list of dicts."""
    changes = []
    for row in _rows(conn):
        current_status = (row['status'] or '').strip()
        path = row['local_path'] or ''
        new_path = _remapped(path, remap)
        exists = bool(new_path) and Path(new_path).exists()

        if row['youtube_short_id']:
            # Published. The only honest status.
            wanted = 'uploaded'
        elif not new_path:
            wanted = 'no_file'
        elif exists:
            wanted = 'queued'
        else:
            wanted = 'file_missing'

        if wanted != current_status or new_path != path:
            changes.append({
                'id': row['id'],
                'clip': f"{row['source_video_id']}#{row['segment_index']}",
                'status_from': current_status or '(empty)',
                'status_to': wanted,
                'path_from': path,
                'path_to': new_path,
                'exists': exists,
            })
    return changes


def apply(conn, changes) -> None:
    for change in changes:
        conn.execute(
            'UPDATE generated_shorts SET status = ?, local_path = ? WHERE id = ?',
            (change['status_to'], change['path_to'], change['id']))
    conn.commit()


def summarise(conn) -> None:
    print('\nCURRENT STATE')
    for label, sql in (
        ('rows total', 'SELECT COUNT(*) FROM generated_shorts'),
        ('published (youtube_short_id set)',
         'SELECT COUNT(*) FROM generated_shorts WHERE youtube_short_id IS NOT NULL'),
        ('unpublished',
         'SELECT COUNT(*) FROM generated_shorts WHERE youtube_short_id IS NULL'),
    ):
        print(f'  {label:<36} {conn.execute(sql).fetchone()[0]}')
    print('  by status:')
    for status, count in conn.execute(
            'SELECT COALESCE(NULLIF(status, ""), "(empty)"), COUNT(*) '
            'FROM generated_shorts GROUP BY 1 ORDER BY 2 DESC').fetchall():
        print(f'    {status:<34} {count}')


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--db', help='path to processed_videos.db')
    parser.add_argument('--apply', action='store_true',
                        help='write the changes (default is a dry run)')
    parser.add_argument('--remap', default='',
                        help='rewrite a stale path prefix, as OLD=NEW')
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else _default_db()
    if not db_path or not Path(db_path).exists():
        print('Could not find the database. Pass --db /path/to/processed_videos.db')
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
        summarise(conn)

        changes = plan(conn, remap)
        if not changes:
            print('\nNothing to repair: status already agrees with '
                  'youtube_short_id and every path resolves.')
            return 0

        print(f'\n{len(changes)} row(s) to change'
              f'{"" if args.apply else " (DRY RUN -- nothing written)"}:')
        buckets = {}
        for change in changes:
            key = f"{change['status_from']} -> {change['status_to']}"
            buckets.setdefault(key, []).append(change)
        for key, items in sorted(buckets.items()):
            print(f'  {key}: {len(items)}')
            for item in items[:5]:
                note = ''
                if item['path_to'] != item['path_from']:
                    note = (f"  path -> {item['path_to']} "
                            f"({'found' if item['exists'] else 'still missing'})")
                print(f"    {item['clip']}{note}")
            if len(items) > 5:
                print(f'    ... and {len(items) - 5} more')

        if not args.apply:
            print('\nRe-run with --apply to write these changes.')
            return 0

        apply(conn, changes)
        print('\nApplied.')
        summarise(conn)
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
