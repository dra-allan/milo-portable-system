"""Lane-local shim onto the one canonical channel-identity implementation.

The logic lives in ``artisan/yt_secrets/identity.py`` -- there is exactly one
ledger and one comparison, because three copies of a security check is three
chances for one of them to drift into being permissive.

The lanes cannot simply ``import yt_secrets``: each runs as ``python -m
src.main`` from inside its own directory, so ``artisan/`` is not on
``sys.path``. This shim finds the module by walking up to the repo root and
loads it by path.

If it cannot be found, the guard degrades to a **loud warning**, not a silent
pass and not a hard failure: a missing registry on a half-copied machine should
not brick uploads, but it must never look like a successful verification.

Two kinds of check are exposed:

* :func:`assert_identity` -- is this the right CHANNEL? (the 2026-08-16 fix)
* :func:`assert_content` / :func:`assert_lane` -- is this the right CONTENT for
  that channel? (the 2026-08-23 fix; a ranking countdown routed onto a shorts
  channel, or a forex niche routed onto a Luganda gossip channel, is the same
  class of accident from the audience's side)
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
    """Fallback type used when the canonical module cannot be loaded.

    Replaced by the real class as soon as the module loads, so callers can
    always ``except channel_guard.ChannelIdentityError``.
    """


class ChannelContentError(ChannelIdentityError):
    """Fallback content-mismatch type; replaced once the module loads."""


def _candidate_paths() -> list:
    here = Path(__file__).resolve()
    out = []
    for parent in here.parents:
        candidate = parent / 'artisan' / 'yt_secrets' / 'identity.py'
        if candidate.exists():
            out.append(candidate)
        candidate = parent / 'yt_secrets' / 'identity.py'
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
            logger.debug('channel identity loaded from %s', path)
            return _IDENTITY
        except Exception as exc:
            logger.warning('could not load channel identity from %s: %s',
                           path, exc)
    logger.warning(
        'channel identity module not found; wrong-channel protection is '
        'DISABLED for this process. Expected artisan/yt_secrets/identity.py.')
    return None


def resolve_key(channel: Optional[str]) -> str:
    """Canonicalise anything human into the exact registry key.

    Everything that accepts a channel from config, an env var or a CLI argument
    should pass through here. ``'RankDrop'`` and ``'the other guys'`` were being
    used as channel keys directly, which produced token filenames and identity
    bindings for channels that are not in the registry at all.
    """
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
    """Verify live credentials belong to ``channel_key``; raise if they do not."""
    module = _identity()
    if module is None:
        logger.warning('CHANNEL_IDENTITY_UNVERIFIED key=%s observed=%s%s',
                       channel_key, observed_id or 'unknown',
                       f' ({context})' if context else '')
        return str(observed_id or '')
    return module.assert_identity(channel_key, observed_id or '',
                                 observed_title, context)


def assert_lane(channel_key: str, pipeline: str, context: str = '') -> None:
    """Refuse when ``pipeline`` is not a lane this channel is registered on."""
    module = _identity()
    if module is None or not hasattr(module, 'assert_lane'):
        return
    module.assert_lane(channel_key, pipeline, context)


def assert_content(channel_key: str, pipeline: str = '', variant: str = '',
                   niche: str = '', context: str = '') -> None:
    """Refuse a publish whose lane, variant or niche is not this channel's.

    Only DECLARED facts are compared, so passing whatever the caller happens to
    know is always safe: a channel that declares nothing is never blocked.
    """
    module = _identity()
    if module is None or not hasattr(module, 'assert_content'):
        logger.warning('CHANNEL_CONTENT_UNVERIFIED key=%s pipeline=%s '
                       'variant=%s niche=%s', channel_key, pipeline or '-',
                       variant or '-', niche or '-')
        return
    module.assert_content(channel_key, pipeline=pipeline, variant=variant,
                          niche=niche, context=context)


def content_summary(channel_key: str) -> str:
    """Plain-English description of what this channel posts, or ''."""
    module = _identity()
    if module is None or not hasattr(module, 'content_summary'):
        return ''
    try:
        return module.content_summary(channel_key)
    except Exception:
        return ''


def channels_for_variant(pipeline: str, variant: str) -> list:
    """Registry keys on ``pipeline`` that declare ``variant``."""
    module = _identity()
    if module is None or not hasattr(module, 'channels_for_variant'):
        return []
    try:
        return list(module.channels_for_variant(pipeline, variant))
    except Exception:
        return []


def client_source(channel_key: str) -> str:
    """Which channel's OAuth client ``channel_key`` should use."""
    module = _identity()
    if module is None:
        return channel_key
    try:
        return module.client_source(channel_key)
    except Exception:
        return channel_key


def deleted_client_help(error: object) -> str:
    """The runbook text when an error is a deleted Google Cloud client, else ''."""
    module = _identity()
    if module is None:
        return ''
    try:
        if module.looks_like_deleted_client(error):
            return module.DELETED_CLIENT_RUNBOOK
    except Exception:
        pass
    return ''
