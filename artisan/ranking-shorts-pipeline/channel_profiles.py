"""Explicit ranking-channel routing.

The ranking line is separate from the Shorts line. Ranked countdowns (normal
variant) publish to RankDrop; the 'OTHERS VS THIS GUY' clips (contrast
variant) publish to The Other Guys. NXS is never used by ranking output.

Channel keys in this module are the actual token-file suffixes, so a key here
maps one-to-one to ``config/youtube_token_ranking_<key>.json``.
"""
from __future__ import annotations
import os

ALIASES = {
    'rankdrop': 'RankDrop',
    'rank_drop': 'RankDrop',
    'rank drop': 'RankDrop',
    'otherguy': 'the other guys',
    'other_guy': 'the other guys',
    'other guy': 'the other guys',
    'otherguys': 'the other guys',
    'other_guys': 'the other guys',
    'other guys': 'the other guys',
}

DEFAULT_PROFILES = 'RankDrop:normal,the other guys:contrast'


def canonical_channel(value: str) -> str:
    key = (value or '').strip().lower()
    return ALIASES.get(key, key)


def profiles() -> dict[str, set[str]]:
    raw = os.getenv('RANKING_CHANNEL_PROFILES', '').strip() or DEFAULT_PROFILES
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


def route_channel(variant: str) -> str:
    """Channel for a content variant.

    Ranked countdowns go to the normal lane (RankDrop); 'OTHERS VS THIS GUY'
    clips go to the contrast lane (The Other Guys). Falls back to RankDrop
    when the profiles don't declare the lane.
    """
    mode = 'contrast' if (variant or 'normal').lower() == 'contrast' else 'normal'
    return channel_for(mode) or 'RankDrop'