"""Explicit ranking-channel routing.

The ranking line is separate from the Shorts line. NXS is never inferred from
ranking output, and the legacy RankDrop spelling is normalized to rankedup.
"""
from __future__ import annotations
import os

ALIASES = {
    'rankdrop': 'rankedup',
    'rank_drop': 'rankedup',
    'nxs': 'rankedup',  # legacy misroute: ranking must not use Shorts NXS
}


def canonical_channel(value: str) -> str:
    key = (value or '').strip().lower()
    return ALIASES.get(key, key)


def profiles() -> dict[str, set[str]]:
    raw = os.getenv('RANKING_CHANNEL_PROFILES', '').strip()
    # Normal ranking belongs to rankedup. The Other Guys lane is kept in its
    # own channel/profile and never mixed into rankedup or NXS.
    if not raw:
        raw = 'rankedup:normal,other_guys:normal'
    out: dict[str, set[str]] = {}
    for item in raw.split(','):
        if ':' not in item:
            continue
        channel, modes = item.split(':', 1)
        channel = canonical_channel(channel)
        wanted = {m.strip().lower() for m in modes.split('|') if m.strip()}
        if channel and wanted & {'normal', 'contrast', 'both'}:
            out[channel] = {'normal', 'contrast'} if 'both' in wanted else wanted
    return out


def enabled_channels(mode: str) -> list[str]:
    return [channel for channel, modes in profiles().items() if mode in modes]


def channel_for(mode: str, cursor: int = 0) -> str | None:
    channels = enabled_channels(mode)
    return channels[cursor % len(channels)] if channels else None
