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
    global _IDENTITY, _LOADED, ChannelIdentityError
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
            logger.debug('channel identity loaded from %s', path)
            return _IDENTITY
        except Exception as exc:
            logger.warning('could not load channel identity from %s: %s',
                           path, exc)
    logger.warning(
        'channel identity module not found; wrong-channel protection is '
        'DISABLED for this process. Expected artisan/yt_secrets/identity.py.')
    return None


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
