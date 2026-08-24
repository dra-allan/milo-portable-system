"""Suppression detector state: which channels YouTube stopped distributing.

2026-08-24 fleet audit: capital_mindset was suppressed around 2026-08-11 and
the pipeline kept rendering + uploading into the void for 13 days because
nothing ever reads view counts back. This module is the read-back half.

``channel_health.py`` (run daily by the pipeline driver) computes the median
view count of each channel's recent uploads and calls :func:`mark_suppressed`
for channels under threshold. The upload paths call :func:`is_suppressed`
before posting, so a suppressed channel stops consuming renders, quota and
API uploads until it recovers (entries expire after ``ttl_days``) or a human
deletes the entry.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)

_TTL_DAYS = 7
_lock = threading.Lock()


def _state_file() -> Path:
    try:
        from .config import config
    except ImportError:  # pragma: no cover - direct execution
        from config import config
    return Path(config.project_root) / 'data' / 'suppressed_channels.yaml'


def _load() -> Dict:
    path = _state_file()
    if not path.exists():
        return {'checked_at': None, 'channels': {}}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {'checked_at': None, 'channels': {}}
        data.setdefault('channels', {})
        return data
    except Exception as exc:
        logger.warning('could not read suppression state %s: %s', path, exc)
        return {'checked_at': None, 'channels': {}}


def _save(data: Dict) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, sort_keys=True)
    tmp.replace(path)


def _norm(channel: str) -> str:
    """Slug-normalise a channel key ('The Other Guys' -> 'the_other_guys')."""
    return '_'.join(str(channel or '').strip().lower().split())


def is_suppressed(channel: str, ttl_days: int = _TTL_DAYS) -> bool:
    """True when *channel* has an unexpired suppression flag."""
    key = _norm(channel)
    if not key:
        return False
    with _lock:
        entry = (_load().get('channels') or {}).get(key)
    if not isinstance(entry, dict):
        return False
    since = entry.get('since')
    try:
        marked = datetime.fromisoformat(str(since))
    except (TypeError, ValueError):
        # Malformed entry: treat as active but log loudly.
        logger.warning('suppression entry for %s has bad timestamp: %r',
                       key, since)
        return True
    expired = datetime.now() - marked > timedelta(days=ttl_days)
    if expired:
        return False
    return True


def mark_suppressed(channel: str, median_views: int, sample_size: int,
                    threshold: int) -> None:
    key = _norm(channel)
    if not key:
        return
    with _lock:
        data = _load()
        channels = data.setdefault('channels', {})
        existing = channels.get(key) or {}
        channels[key] = {
            'median_views': int(median_views),
            'sample_size': int(sample_size),
            'threshold': int(threshold),
            'since': existing.get('since') or datetime.now().isoformat(),
            'last_confirmed': datetime.now().isoformat(),
        }
        _save(data)
    logger.warning('CHANNEL_SUPPRESSED key=%s median_views=%d sample=%d '
                   'threshold=%d -- uploads paused (ttl %dd)',
                   key, median_views, sample_size, threshold, _TTL_DAYS)


def mark_healthy(channel: str) -> None:
    """Drop the flag once a channel's recent uploads perform again."""
    key = _norm(channel)
    with _lock:
        data = _load()
        channels = data.get('channels') or {}
        if key in channels:
            del channels[key]
            _save(data)
            logger.info('channel %s recovered; suppression flag cleared', key)


def clear_channel(channel: str) -> None:
    """Manual override used by humans/ops tools."""
    mark_healthy(channel)


def snapshot() -> Dict[str, dict]:
    """Copy of all entries (expired ones included) for reporting."""
    with _lock:
        return dict(_load().get('channels') or {})


def status_line(channel: str) -> Optional[str]:
    """Human-readable one-liner for reports, or None when healthy."""
    entry = snapshot().get(str(channel or '').strip().lower())
    if not entry:
        return None
    return (f"median {entry.get('median_views')} views "
            f"over {entry.get('sample_size')} uploads")
