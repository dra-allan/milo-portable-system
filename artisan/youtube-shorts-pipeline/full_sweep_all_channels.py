#!/usr/bin/env python3
"""Full Shorts sweep: chop every authenticated lane, then post within caps.

Chopping and posting are intentionally separate operations. Daily upload caps
may stop publishing, but they must never stop downloading, transcribing, or
rendering a fresh backlog item for an authenticated channel.
"""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from src.config import config
from src.main import ShortsPipeline

# The first Shorts line is deliberately explicit. Unbound niches remain
# disabled instead of silently routing into a random default channel.
ROUTES = {
    'capital_mindset': 'capital_mindset',
    'flick_shorts': 'flick_shorts',
    'chop_ug': 'chop_ug',
    'nxs': 'NXS',  # token/channel compatibility; canonical display name is nxs
    'wealth_mindset': 'wealth_mindset',
    'forex_god_fx': 'god_did_fx',
}
ALIASES = {'gta_hype': 'nxs'}


def _canonical_niche(raw: str) -> str:
    return ALIASES.get(raw, raw)


def _bind_legacy_nxs() -> None:
    """Make the old gta_hype key behave as the canonical nxs lane."""
    legacy = (config.niches or {}).get('gta_hype')
    if isinstance(legacy, dict):
        legacy['upload_channels'] = ['NXS']
        legacy['channel'] = 'NXS'


def _auth_keys() -> set[str]:
    return {str(x).lower() for x in config.authenticated_channels()}


def main() -> int:
    _bind_legacy_nxs()
    authenticated = _auth_keys()
    if not authenticated:
        print('[sweep] no authenticated channels found', file=sys.stderr)
        return 2

    # One source lane per channel. This bypasses the posting budget entirely.
    lanes = {}
    for raw in config.niche_names():
        canonical = _canonical_niche(raw)
        if canonical not in ROUTES or canonical in lanes:
            continue
        channel = ROUTES[canonical]
        if str(channel).lower() not in authenticated:
            print(f'[sweep] {canonical}: not authenticated, skip')
            continue
        lanes[canonical] = (raw, channel)

    missing = sorted(set(ROUTES) - set(lanes))
    if missing:
        print('[sweep] no routed niche found for: ' + ', '.join(missing))

    pipeline = ShortsPipeline(upload=False)
    chop_failures = 0
    for canonical, (raw, channel) in sorted(lanes.items()):
        print(f'[chop] {canonical} -> {channel}: starting fresh backlog fill')
        # Chop first. No upload flag, no daily-cap check, no shared sweep cap.
        # The existing DB/file resume logic still prevents duplicate work.
        if canonical == 'chop_ug':
            # Luganda is not a faster-whisper language label. Disable the
            # invalid `lg` hint and let Whisper detect what it can.
            cfg = config.niches.get(raw) or {}
            cfg['whisper_language'] = ''
            cfg['language'] = ''
        started = pipeline.run_niche(raw, max_videos=1)
        if started < 1:
            chop_failures += 1
            print(f'[chop] {canonical}: no fresh source was chopped this pass')
        else:
            print(f'[chop] {canonical}: {started} source chopped into backlog')

    # Posting is a second, independent pass. The uploader enforces the
    # per-channel/day cap and keeps excess clips queued for the next window.
    upload_limit = os.getenv('UPLOAD_MAX_PER_RUN', '').strip() or '999999'
    upload_failures = 0
    for canonical, (_raw, channel) in sorted(lanes.items()):
        print(f'[post] {canonical} -> {channel}: draining within cap')
        result = subprocess.run(
            [sys.executable, '-m', 'src.main', '--mode', 'upload-existing',
             '--channel', channel, '--upload-limit', upload_limit],
            cwd=str(HERE),
        )
        if result.returncode:
            upload_failures += 1

    print(f'[sweep] complete: lanes={len(lanes)} chop_failures={chop_failures} '
          f'upload_failures={upload_failures}')
    return 0 if lanes and chop_failures == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
