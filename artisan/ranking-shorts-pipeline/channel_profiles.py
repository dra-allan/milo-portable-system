"""Channel specialization for ranking and contrast formats.

Configure with RANKING_CHANNEL_PROFILES, for example:
  rankdrop:contrast,rank_main:normal,rank_mix:both
"""
from __future__ import annotations
import os

def profiles() -> dict[str, set[str]]:
    raw = os.getenv('RANKING_CHANNEL_PROFILES', '').strip()
    if not raw:
        raw = f"{os.getenv('RANKING_UPLOAD_CHANNEL', 'rankdrop')}:both"
    out = {}
    for item in raw.split(','):
        if ':' not in item: continue
        channel, modes = item.split(':', 1)
        channel = channel.strip()
        modes = {m.strip().lower() for m in modes.split('|') if m.strip()}
        if channel and modes & {'normal','contrast','both'}:
            if 'both' in modes: modes = {'normal','contrast'}
            out[channel] = modes
    return out

def enabled_channels(mode: str) -> list[str]:
    return [c for c, modes in profiles().items() if mode in modes]

def channel_for(mode: str, cursor: int = 0) -> str | None:
    channels = enabled_channels(mode)
    return channels[cursor % len(channels)] if channels else None
