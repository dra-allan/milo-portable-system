"""Channel identity: the binding between a channel KEY and a YouTube channel.

THE INCIDENT THIS PREVENTS
--------------------------
On 2026-08-16 the ``wealth_mindset`` token was authenticated against the **Chop
UG** YouTube channel and four clips were published to the wrong channel before
anyone noticed. Nothing malfunctioned. The mint flow resolved the channel,
printed its name, and wrote the token anyway -- so whichever Google account the
human happened to be signed into silently *became* ``wealth_mindset``, and no
later stage ever re-checked.

That is a data problem, not a discipline problem: there was nowhere to record
"``wealth_mindset`` means channel ``UC...``", so there was nothing to compare
against. This module is that record, plus the comparison.

HOW IT WORKS
------------
Two sources of truth, checked in this order:

1. ``channels.yaml`` -> ``channels.<key>.channel_id`` (hand-maintained, wins).
2. ``yt-secrets/channel_identity.json`` -- the ledger, written automatically the
   first time a key resolves to a channel.

The YAML stays hand-edited and comment-rich; the ledger is machine-written. They
are deliberately separate files so an automated bind can never reformat the
registry or drop its comments.

MODES (``MILO_CHANNEL_IDENTITY``)
---------------------------------
``learn`` (default)
    An unbound key binds to the first channel it resolves to, with a warning.
    Every later use is enforced. This is what makes the fix retroactive without
    anyone having to look up twelve channel ids by hand.
``enforce``
    An unbound key is an error. Use this once the ledger is complete: it turns
    "a token was minted against the wrong account" into a refusal rather than a
    binding.
``off``
    No checks. Present so a genuine channel migration is possible without
    editing code, and for nothing else.

DESIGN CONSTRAINT
-----------------
Zero imports from any pipeline. All three lanes load this file by path (see each
lane's ``src/channel_guard.py``), so it must depend on nothing but the standard
library and optionally PyYAML.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
LEGACY_DIR = HERE.parent / 'yt-secrets'
REGISTRY_PATH = LEGACY_DIR / 'channels.yaml'
LEDGER_PATH = LEGACY_DIR / 'channel_identity.json'

MODE_LEARN = 'learn'
MODE_ENFORCE = 'enforce'
MODE_OFF = 'off'


class ChannelIdentityError(RuntimeError):
    """A token does not belong to the channel key that is using it.

    Raised rather than logged on purpose. The cheapest possible outcome here is
    a failed run; the expensive outcome is a published video on someone else's
    channel, which cannot be un-published from a git branch.
    """


# ---------------------------------------------------------------------------
# Locating the registry
# ---------------------------------------------------------------------------
def registry_path() -> Path:
    override = (os.getenv('MILO_CHANNEL_REGISTRY') or '').strip()
    return Path(override).expanduser() if override else REGISTRY_PATH


def ledger_path() -> Path:
    override = (os.getenv('MILO_CHANNEL_LEDGER') or '').strip()
    return Path(override).expanduser() if override else LEDGER_PATH


def mode() -> str:
    raw = (os.getenv('MILO_CHANNEL_IDENTITY') or MODE_LEARN).strip().lower()
    return raw if raw in (MODE_LEARN, MODE_ENFORCE, MODE_OFF) else MODE_LEARN


def load_registry() -> Dict[str, Dict]:
    """``channels.yaml`` as a dict, or {} when it is missing or unreadable.

    A missing registry must never be fatal: the ledger alone is enough to catch
    a channel swap, and the lanes have to keep working on a machine where only
    the tokens were copied across.
    """
    path = registry_path()
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        logger.info('PyYAML not installed; channel registry not consulted')
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        channels = data.get('channels') or {}
        return {str(k): (v or {}) for k, v in channels.items()}
    except Exception as exc:
        logger.warning('Could not read channel registry %s: %s', path, exc)
        return {}


def load_ledger() -> Dict[str, Dict]:
    path = ledger_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning('Could not read channel identity ledger %s: %s', path, exc)
        return {}


def _save_ledger(ledger: Dict[str, Dict]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True) + '\n',
                   encoding='utf-8')
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Reading and writing a binding
# ---------------------------------------------------------------------------
def expected_channel_id(key: str) -> Tuple[str, str]:
    """The channel id ``key`` must resolve to, and where that came from.

    Returns ``('', 'unbound')`` when the key has never been bound.
    """
    entry = load_registry().get(key) or {}
    declared = str(entry.get('channel_id') or '').strip()
    if declared:
        return declared, 'channels.yaml'
    recorded = str((load_ledger().get(key) or {}).get('channel_id') or '').strip()
    if recorded:
        return recorded, 'channel_identity.json'
    return '', 'unbound'


def bind(key: str, channel_id: str, channel_title: str = '',
         rebind: bool = False) -> None:
    """Record that ``key`` means ``channel_id``.

    Refuses to overwrite an existing, different binding unless ``rebind`` is
    passed. Silently overwriting is precisely how a mis-auth would launder
    itself into the ledger and start looking correct.
    """
    channel_id = str(channel_id or '').strip()
    if not channel_id:
        raise ValueError('refusing to bind an empty channel id')
    ledger = load_ledger()
    existing = str((ledger.get(key) or {}).get('channel_id') or '').strip()
    if existing and existing != channel_id and not rebind:
        raise ChannelIdentityError(
            f'{key} is already bound to {existing}; refusing to silently '
            f'rebind it to {channel_id}. If the channel really did change, '
            f'pass --rebind (or set MILO_CHANNEL_IDENTITY=off for one run).'
        )
    ledger[key] = {
        'channel_id': channel_id,
        'channel_title': str(channel_title or '').strip(),
        'bound_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    }
    _save_ledger(ledger)
    logger.info('CHANNEL_BOUND key=%s channel_id=%s title=%s', key, channel_id,
                channel_title or 'unknown')


def assert_identity(key: str, observed_id: str, observed_title: str = '',
                    context: str = '') -> str:
    """Verify a live token belongs to ``key``. Returns the effective id.

    ``observed_id`` is what ``channels.list(mine=True)`` just returned for the
    credentials in hand. Every publisher already made that call and threw the
    answer away; this is where it finally gets used.

    Raises :class:`ChannelIdentityError` on a mismatch, and in ``enforce`` mode
    also when the key has no binding at all.
    """
    current = mode()
    where = f' during {context}' if context else ''

    if current == MODE_OFF:
        logger.warning('CHANNEL_IDENTITY_OFF key=%s observed=%s%s -- running '
                       'without the wrong-channel guard', key, observed_id, where)
        return str(observed_id or '')

    observed_id = str(observed_id or '').strip()
    if not observed_id:
        # No id means the API call failed or the account owns no channel. Either
        # way we cannot prove the target, and "cannot prove" must not mean
        # "proceed" for an upload.
        raise ChannelIdentityError(
            f'could not resolve a YouTube channel for key {key!r}{where}. '
            'The token may be revoked, or the Google account owns no channel. '
            f'Re-auth with:  cd artisan && python -m yt_secrets auth --channel {key}'
        )

    wanted, source = expected_channel_id(key)

    if not wanted:
        if current == MODE_ENFORCE:
            raise ChannelIdentityError(
                f'{key!r} has no channel binding and MILO_CHANNEL_IDENTITY='
                f'enforce. Bind it deliberately:  cd artisan && python -m '
                f'yt_secrets bind --channel {key} --channel-id {observed_id}'
            )
        bind(key, observed_id, observed_title)
        logger.warning(
            'CHANNEL_IDENTITY_LEARNED key=%s channel_id=%s title=%s%s -- '
            'bound now and enforced from here on. Verify this is correct: an '
            'already-wrong token would bind its wrong channel.',
            key, observed_id, observed_title or 'unknown', where)
        return observed_id

    if observed_id != wanted:
        raise ChannelIdentityError(
            f'WRONG CHANNEL{where}: key {key!r} is bound to {wanted} '
            f'(per {source}) but the token in use resolves to {observed_id}'
            + (f' ({observed_title})' if observed_title else '')
            + '. Nothing was uploaded. This is the 2026-08-16 failure mode: '
              'the token was minted while signed into the wrong Google '
              f'account. Fix it with:  cd artisan && python -m yt_secrets auth '
              f'--channel {key}  (sign in as the owner of {wanted}).'
        )

    logger.info('CHANNEL_IDENTITY_OK key=%s channel_id=%s', key, observed_id)
    return observed_id


# ---------------------------------------------------------------------------
# OAuth client sharing
# ---------------------------------------------------------------------------
def client_source(key: str) -> str:
    """Which channel's OAuth client ``key`` should use.

    Exists because ``flick_shorts``' own Google Cloud OAuth client
    (``929304292327-aggfh...``) was DELETED, so re-auth returns
    ``deleted_client`` forever. The working recovery is to run the flow with a
    live project's client secrets -- which was previously something you had to
    remember. ``client_from: wealth_mindset`` in channels.yaml makes it
    configuration, so the fix survives the next machine and the next session.

    Returns ``key`` itself when there is no redirect.
    """
    entry = load_registry().get(key) or {}
    borrowed = str(entry.get('client_from') or '').strip()
    return borrowed or key


DELETED_CLIENT_RUNBOOK = """\
The OAuth client for this channel has been DELETED in Google Cloud, so Google
returns `deleted_client` and no amount of retrying will help.

Two ways forward:

  A. Borrow a live client (fastest, works today)
     Add to artisan/yt-secrets/channels.yaml under this channel:
         client_from: wealth_mindset
     then re-run the auth command. The grant is per-Google-account, so the
     token still belongs to the right channel -- only the client app differs.
     Note the borrowed project's 10k/day API quota is then shared.

  B. Recreate the client (do this properly, once)
     Google Cloud Console -> the channel's project -> APIs & Services ->
     Credentials -> Create Credentials -> OAuth client ID -> Desktop app.
     Download the JSON to  artisan/yt-secrets/<slug>/credentials.json,
     confirm the consent screen is PUBLISHED (Testing mode expires refresh
     tokens after 7 days), then re-run the auth command.
"""


def looks_like_deleted_client(error: object) -> bool:
    text = str(error or '').lower()
    return 'deleted_client' in text or 'client has been deleted' in text
