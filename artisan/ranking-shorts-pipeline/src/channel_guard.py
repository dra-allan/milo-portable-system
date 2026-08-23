"""Vendored copy of the shorts lane's channel-identity shim.

Identical on purpose -- see ``artisan/youtube-shorts-pipeline/src/channel_guard.py``
for the rationale. The actual logic lives once, in
``artisan/yt_secrets/identity.py``; this only locates and loads it.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_IDENTITY = None
_LOADED = False


class ChannelIdentityError(RuntimeError):
    """Fallback type; replaced by the real one once the module loads."""


class ChannelContentError(ChannelIdentityError):
    """Fallback content-mismatch type; replaced once the module loads."""


def _candidate_paths() -> list:
    here = Path(__file__).resolve()
    out = []
    for parent in here.parents:
        for candidate in (parent / 'artisan' / 'yt_secrets' / 'identity.py',
                          parent / 'yt_secrets' / 'identity.py'):
            if candidate.exists():
                out.append(candidate)
    return out


def _identity():
    global _IDENTITY, _LOADED, ChannelIdentityError, ChannelContentError
    if _LOADED:
        return _IDENTITY
    _LOADED = True
    for path in _candidate_paths():
        try:
            spec = importlib.util.spec_from_file_location(
                'milo_channel_identity', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _IDENTITY = module
            ChannelIdentityError = module.ChannelIdentityError
            ChannelContentError = getattr(module, 'ChannelContentError',
                                          module.ChannelIdentityError)
            return _IDENTITY
        except Exception as exc:
            logger.warning('could not load channel identity from %s: %s',
                           path, exc)
    logger.warning('channel identity module not found; wrong-channel '
                   'protection is DISABLED for this process')
    return None


def resolve_key(channel: Optional[str]) -> str:
    """Canonicalise anything human into the exact registry key."""
    raw = str(channel or '').strip()
    if not raw:
        return ''
    module = _identity()
    if module is None or not hasattr(module, 'resolve_key'):
        return raw
    try:
        return module.resolve_key(raw) or raw
    except Exception:
        return raw


def assert_identity(channel_key: str, observed_id: Optional[str],
                    observed_title: str = '', context: str = '') -> str:
    module = _identity()
    if module is None:
        logger.warning('CHANNEL_IDENTITY_UNVERIFIED key=%s observed=%s',
                       channel_key, observed_id or 'unknown')
        return str(observed_id or '')
    return module.assert_identity(channel_key, observed_id or '',
                                 observed_title, context)


def assert_lane(channel_key: str, pipeline: str, context: str = '') -> None:
    module = _identity()
    if module is None or not hasattr(module, 'assert_lane'):
        return
    module.assert_lane(channel_key, pipeline, context)


def assert_content(channel_key: str, pipeline: str = '', variant: str = '',
                   niche: str = '', context: str = '') -> None:
    module = _identity()
    if module is None or not hasattr(module, 'assert_content'):
        logger.warning('CHANNEL_CONTENT_UNVERIFIED key=%s pipeline=%s variant=%s',
                       channel_key, pipeline or '-', variant or '-')
        return
    module.assert_content(channel_key, pipeline=pipeline, variant=variant,
                          niche=niche, context=context)


def content_summary(channel_key: str) -> str:
    module = _identity()
    if module is None or not hasattr(module, 'content_summary'):
        return ''
    try:
        return module.content_summary(channel_key)
    except Exception:
        return ''


def channels_for_variant(pipeline: str, variant: str) -> list:
    module = _identity()
    if module is None or not hasattr(module, 'channels_for_variant'):
        return []
    try:
        return list(module.channels_for_variant(pipeline, variant))
    except Exception:
        return []


def client_source(channel_key: str) -> str:
    module = _identity()
    if module is None:
        return channel_key
    try:
        return module.client_source(channel_key)
    except Exception:
        return channel_key


def deleted_client_help(error: object) -> str:
    module = _identity()
    if module is None:
        return ''
    try:
        if module.looks_like_deleted_client(error):
            return module.DELETED_CLIENT_RUNBOOK
    except Exception:
        pass
    return ''
