"""Explicit ranking-channel routing.

The ranking line is separate from the Shorts line. Ranked countdowns (normal
variant) publish to **rankdrop**; the 'OTHERS VS THIS GUY' clips (contrast
variant) publish to **the_other_guys**. No shorts channel is ever a valid
ranking target.

WHAT CHANGED 2026-08-23
-----------------------
This module used to route on DISPLAY NAMES -- ``'RankDrop'`` and ``'the other
guys'`` -- and its docstring claimed those were "the actual token-file
suffixes". They were not. The registry keys are ``rankdrop`` and
``the_other_guys``, so routing produced:

* token lookups for ``youtube_token_the other guys.json``, a file that has never
  existed on any machine, and
* identity assertions against the keys ``'RankDrop'`` / ``'the other guys'``,
  which are absent from channels.yaml -- so the wrong-channel guard was
  carefully protecting two phantom keys while the two real channels went
  completely unchecked.

Routing is now derived from ``channels.yaml`` (each ranking channel declares
``variant: normal`` or ``variant: contrast``) and every channel name that enters
this module is canonicalised through ``channel_guard.resolve_key``. The hardcoded
default survives only as a fallback for when the registry cannot be read.
"""
from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

PIPELINE = 'ranking'

# Display names, old keys and typos -> the registry key. resolve_key() already
# handles case and separators ('The Other Guys' -> the_other_guys), so this only
# needs the mappings it cannot derive.
ALIASES = {
    'rank_drop': 'rankdrop',
    'rank drop': 'rankdrop',
    'otherguy': 'the_other_guys',
    'other_guy': 'the_other_guys',
    'other guy': 'the_other_guys',
    'otherguys': 'the_other_guys',
    'other guys': 'the_other_guys',
    'the other guys': 'the_other_guys',
}

# Fallback only. The live answer comes from channels.yaml variants.
DEFAULT_PROFILES = 'rankdrop:normal,the_other_guys:contrast'


def _guard():
    """The lane's channel_guard shim, or None when it cannot be imported.

    Tried both ways because this module is imported both as ``channel_profiles``
    from the pipeline root and as part of the ``src`` package.
    """
    try:
        from src import channel_guard  # type: ignore
        return channel_guard
    except Exception:
        pass
    try:
        import channel_guard  # type: ignore
        return channel_guard
    except Exception:
        return None


def canonical_channel(value: str) -> str:
    """Registry key for any human spelling of a ranking channel."""
    key = (value or '').strip()
    if not key:
        return ''
    mapped = ALIASES.get(key.lower(), key)
    guard = _guard()
    if guard is not None:
        resolved = guard.resolve_key(mapped)
        if resolved:
            return resolved
    return mapped.lower().replace(' ', '_')


def _profiles_from_registry() -> dict[str, set[str]]:
    """``{registry_key: {variant}}`` for every ranking channel in channels.yaml.

    Deriving the routing table from the registry means a channel can never be
    routed to by a lane it is not registered on -- which is exactly how
    ``the_other_guys`` (registered on ``shorts`` until 2026-08-23) was a valid
    ranking target while its token was being minted into the shorts config dir.
    """
    guard = _guard()
    if guard is None:
        return {}
    out: dict[str, set[str]] = {}
    for variant in ('normal', 'contrast'):
        for key in guard.channels_for_variant(PIPELINE, variant):
            out.setdefault(key, set()).add(variant)
    return out


def profiles() -> dict[str, set[str]]:
    raw = os.getenv('RANKING_CHANNEL_PROFILES', '').strip()
    if not raw:
        from_registry = _profiles_from_registry()
        if from_registry:
            return from_registry
        raw = DEFAULT_PROFILES
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
    """Registry key for a content variant, verified against channels.yaml.

    Ranked countdowns go to the normal lane (rankdrop); 'OTHERS VS THIS GUY'
    clips go to the contrast lane (the_other_guys). The chosen key is then
    checked back against the registry, so a mis-edited
    ``RANKING_CHANNEL_PROFILES`` cannot quietly send contrast clips to the
    countdown channel -- that is a content mismatch the audience notices even
    though every credential involved is perfectly valid.
    """
    mode = 'contrast' if (variant or 'normal').lower() == 'contrast' else 'normal'
    channel = channel_for(mode) or canonical_channel('rankdrop')
    guard = _guard()
    if guard is not None:
        guard.assert_content(channel, pipeline=PIPELINE, variant=mode,
                             context=f'ranking route variant={mode}')
    logger.info('RANKING_ROUTE variant=%s channel=%s', mode, channel)
    return channel
