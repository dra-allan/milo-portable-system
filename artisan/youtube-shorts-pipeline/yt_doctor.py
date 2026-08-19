#!/usr/bin/env python3
"""Find out, in one command, why YouTube extraction is failing on this box.

    python yt_doctor.py                 # environment report only
    python yt_doctor.py --probe         # + try a real extraction, client by client
    python yt_doctor.py --probe --video dQw4w9WgXcQ

WHY THIS EXISTS
---------------
The 2026-08-17 outage cost about a day because every layer reported the same
useless sentence -- "Video unavailable" -- while the actual causes were an
un-discovered POT plugin, a stale JS-challenge solver, and player clients that
could never have used a PO Token in the first place. None of those are visible
from the error text, and all three are visible in the yt-dlp *debug* header that
nobody reads when the pipeline swallows it.

So this prints that header's contents deliberately, then walks the client ladder
one client at a time and reports which combination actually resolved formats.
That turns "YouTube is blocking us" into a line you can act on.

METADATA ONLY
-------------
``--probe`` calls ``extract_info(download=False)``. Nothing is written to disk
and no bytes of media are fetched, so it is safe to run against a live source
and safe to run repeatedly while tuning. It is still a request to YouTube from
this IP, so the ladder is bounded and paced.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ytdlp  # noqa: E402

# Ladders worth trying, in the order most likely to work on a datacenter IP.
# Each entry is (label, player clients). POT-capable clients come first because
# they are the only ones a running provider can help.
LADDER: Tuple[Tuple[str, str], ...] = (
    ('mweb (upstream recommended with POT)', 'mweb'),
    ('tv', 'tv'),
    ('web_safari', 'web_safari'),
    ('web', 'web'),
    ('mweb,tv,web_safari (the shipped default)', 'mweb,tv,web_safari'),
    ('android_vr (cannot use a GVS POT -- control)', 'android_vr'),
)


def _probe_one(video_id: str, clients: str, timeout: int) -> Tuple[bool, str]:
    """Resolve formats for one client set. Returns ``(ok, detail)``.

    The environment override is set and restored around the call so a single
    process can walk the whole ladder without the hardening caching a stale
    client list.
    """
    previous = os.environ.get('YTDLP_PLAYER_CLIENTS')
    os.environ['YTDLP_PLAYER_CLIENTS'] = clients
    try:
        opts = {
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'skip_download': True,
            'socket_timeout': timeout,
            'extractor_retries': 1,
        }
        try:
            with _ytdlp.NoWritebackYDL(opts) as ydl:
                info = ydl.extract_info(
                    f'https://www.youtube.com/watch?v={video_id}',
                    download=False) or {}
        except Exception as exc:
            return False, str(exc).replace('\n', ' ')[:200]
        formats = [f for f in (info.get('formats') or [])
                   if f.get('url') and f.get('vcodec') not in (None, 'none')]
        if not formats:
            return False, 'extracted but no playable video formats resolved'
        best = max(formats, key=lambda f: (f.get('height') or 0))
        return True, (f"{len(formats)} video format(s), best "
                      f"{best.get('height') or '?'}p {best.get('ext') or ''}"
                      f" ({info.get('title') or 'untitled'})")
    finally:
        if previous is None:
            os.environ.pop('YTDLP_PLAYER_CLIENTS', None)
        else:
            os.environ['YTDLP_PLAYER_CLIENTS'] = previous


def probe(video_id: str, timeout: int, pace: float) -> int:
    print('\nCLIENT LADDER (metadata only, nothing downloaded)')
    winners: List[str] = []
    for label, clients in LADDER:
        ok, detail = _probe_one(video_id, clients, timeout)
        print(f'[{"PASS" if ok else "FAIL"}] {label}')
        print(f'         {detail}')
        if ok:
            winners.append(clients)
        time.sleep(pace)

    print()
    if not winners:
        print('No client resolved formats. In priority order:')
        print('  1. pip install -U yt-dlp yt-dlp-ejs   '
              '(a stale ejs is the documented cause of "The page needs to be '
              'reloaded" -- upstream fixed it by bumping ejs, not by config)')
        print('  2. Make the POT provider visible to THIS interpreter: '
              'python -m pip install -U bgutil-ytdlp-pot-provider, then '
              're-run and confirm "pot plugin" is INFO above.')
        print('  3. pip install "yt-dlp[default,curl-cffi]" so requests carry '
              'a browser TLS fingerprint.')
        print('  4. Only then treat it as an IP-reputation problem and route '
              'extraction through a residential egress.')
        return 1
    print(f'Working client sets: {", ".join(winners)}')
    print(f'Pin it with:  YTDLP_PLAYER_CLIENTS={winners[0]}')
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--probe', action='store_true',
                        help='also try a real (metadata-only) extraction')
    parser.add_argument('--video', default='dQw4w9WgXcQ',
                        help='video id to probe with')
    parser.add_argument('--timeout', type=int, default=20)
    parser.add_argument('--pace', type=float, default=1.5,
                        help='seconds between ladder attempts')
    args = parser.parse_args(argv)

    print('ENVIRONMENT')
    status = _ytdlp.print_diagnosis()
    if not args.probe:
        return status
    return probe(args.video, args.timeout, args.pace) or status


if __name__ == '__main__':
    raise SystemExit(main())
