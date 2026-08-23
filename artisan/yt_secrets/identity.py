"""Channel identity and content routing: what a key means, and what it may post.

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

THE SECOND CLASS OF MISMATCH (added 2026-08-23)
-----------------------------------------------
Identity only answers *"is this the right channel?"*. It cannot answer *"is this
the right content for this channel?"* -- and the pipelines had no answer either:

* The ranking lane routed to ``'RankDrop'`` and ``'the other guys'``: display
  names, not registry keys. Those produced token filenames
  (``youtube_token_the other guys.json``) and identity bindings for channels that
  do not exist in the registry, so the whole guard was being applied to phantom
  keys while the real ones went unchecked.
* ``the_other_guys`` was registered on the ``shorts`` lane with the shorts token
  dir, while actually being a ranking channel. A token minted that way lands
  where the ranking publisher will never look for it.
* A channel's *subject matter* lived in a different file entirely
  (``youtube-shorts-pipeline/config/niches.yaml``), with nothing tying the two
  together, so a Luganda gossip niche and a forex niche were one typo apart from
  each other's audience.

So a channel now declares what it is for -- ``pipelines``, ``variant``,
``niches``, ``content`` -- and :func:`assert_content` refuses a publish whose
lane or variant contradicts the declaration. Same philosophy as identity: the
cheapest outcome is a failed run.

HOW IDENTITY WORKS
------------------
Two sources of truth, checked in this order:

1. ``channels.yaml`` -> ``channels.<key>.channel_id`` (hand-maintained, wins).
2. ``yt-secrets/channel_identity.json`` -- the ledger, written automatically the
   first time a key resolves to a channel.

The YAML stays comment-rich and reviewable; the ledger is machine-written. They
are deliberately separate files so an automated bind can never reformat the
registry or drop its comments. Since 2026-08-23 the auth CLI writes verified ids
into the YAML too, via :mod:`yt_secrets.registry` (line-based, comments kept).

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

MODES (``MILO_CHANNEL_CONTENT``)
--------------------------------
``enforce`` (default)
    A lane or variant that contradicts ``channels.yaml`` raises. Only DECLARED
    facts are checked, so a channel that declares nothing is never blocked --
    which is why enforce can be the default without breaking a single run.
``warn``
    Log the mismatch and continue. For a deliberate one-off cross-post.
``off``
    No content checks at all.

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
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
LEGACY_DIR = HERE.parent / 'yt-secrets'
REGISTRY_PATH = LEGACY_DIR / 'channels.yaml'
LEDGER_PATH = LEGACY_DIR / 'channel_identity.json'
NICHES_PATH = HERE.parent / 'youtube-shorts-pipeline' / 'config' / 'niches.yaml'

MODE_LEARN = 'learn'
MODE_ENFORCE = 'enforce'
MODE_WARN = 'warn'
MODE_OFF = 'off'


class ChannelIdentityError(RuntimeError):
    """A token does not belong to the channel key that is using it.

    Raised rather than logged on purpose. The cheapest possible outcome here is
    a failed run; the expensive outcome is a published video on someone else's
    channel, which cannot be un-published from a git branch.
    """


class ChannelContentError(ChannelIdentityError):
    """The right channel, the wrong content.

    Subclasses :class:`ChannelIdentityError` so every ``except
    ChannelIdentityError`` already written in the pipelines treats a content
    mismatch with the same seriousness as a wrong-channel token. It is the same
    class of accident: the audience gets something that was never meant for them.
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


def niches_path() -> Path:
    override = (os.getenv('MILO_NICHES_FILE') or '').strip()
    return Path(override).expanduser() if override else NICHES_PATH


def mode() -> str:
    raw = (os.getenv('MILO_CHANNEL_IDENTITY') or MODE_LEARN).strip().lower()
    return raw if raw in (MODE_LEARN, MODE_ENFORCE, MODE_OFF) else MODE_LEARN


def content_mode() -> str:
    raw = (os.getenv('MILO_CHANNEL_CONTENT') or MODE_ENFORCE).strip().lower()
    return raw if raw in (MODE_ENFORCE, MODE_WARN, MODE_OFF) else MODE_ENFORCE


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


def load_niches() -> Dict[str, Dict]:
    """``niches.yaml`` as a dict, or {} when unavailable.

    Only used by the audit. The lanes must keep running without it: it lives in
    the shorts pipeline and the ranking/POV lanes have no business requiring it.
    """
    path = niches_path()
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        return {str(k): (v or {}) for k, v in data.items() if isinstance(v, dict)}
    except Exception as exc:
        logger.warning('Could not read niches file %s: %s', path, exc)
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
# Canonical keys
# ---------------------------------------------------------------------------
def channel_keys() -> List[str]:
    return list(load_registry())


def resolve_key(value: str) -> str:
    """Turn anything human into the exact registry key, or return it unchanged.

    The ranking lane routed on display names (``'RankDrop'``, ``'the other
    guys'``). Those became token filenames and identity bindings, so the guard
    was protecting keys that do not exist while the real ones were never
    checked. Everything that accepts a channel from config, an env var or a CLI
    argument should pass it through here first.

    Matching order: exact key, case-insensitive key, then slugified
    (lowercase, non-alphanumerics collapsed to ``_``) against slugified keys.
    Unknown values come back untouched so the caller can produce its own error
    naming the value the operator actually typed.
    """
    raw = str(value or '').strip()
    if not raw:
        return ''
    channels = load_registry()
    if raw in channels:
        return raw
    lowered = raw.lower()
    for key in channels:
        if key.lower() == lowered:
            return key

    def slug(text: str) -> str:
        return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')

    wanted = slug(raw)
    if not wanted:
        return raw
    for key in channels:
        if slug(key) == wanted:
            return key
    # 'the other guys' -> 'the_other_guys' even with no registry available.
    return raw


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
# Content routing: what this channel is FOR
# ---------------------------------------------------------------------------
def _declared_list(key: str, field: str) -> List[str]:
    raw = (load_registry().get(key) or {}).get(field)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(',') if part.strip()]
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def expected_pipelines(key: str) -> List[str]:
    return [p.lower() for p in _declared_list(key, 'pipelines')]


def expected_niches(key: str) -> List[str]:
    return _declared_list(key, 'niches')


def expected_variant(key: str) -> str:
    return str((load_registry().get(key) or {}).get('variant') or '').strip().lower()


def content_summary(key: str) -> str:
    return str((load_registry().get(key) or {}).get('content') or '').strip()


def channels_for_variant(pipeline: str, variant: str) -> List[str]:
    """Registry keys on ``pipeline`` that declare ``variant``.

    This is the inverse of the routing table the ranking lane hardcoded in
    ``DEFAULT_PROFILES``. Deriving it from the registry means a channel can
    never be routed to by a lane it is not registered on.
    """
    pipeline = (pipeline or '').lower()
    variant = (variant or '').lower()
    out = []
    for key in load_registry():
        lanes = expected_pipelines(key)
        if pipeline and pipeline not in lanes:
            continue
        if variant and expected_variant(key) != variant:
            continue
        out.append(key)
    return out


def _content_failure(message: str, context: str = '') -> None:
    current = content_mode()
    where = f' during {context}' if context else ''
    if current == MODE_OFF:
        return
    if current == MODE_WARN:
        logger.warning('CHANNEL_CONTENT_MISMATCH%s -- %s (MILO_CHANNEL_CONTENT='
                       'warn, continuing anyway)', where, message)
        return
    raise ChannelContentError(message + where + '. Nothing was published. Set '
                              'MILO_CHANNEL_CONTENT=warn for a deliberate '
                              'one-off cross-post, or fix the channel entry in '
                              'artisan/yt-secrets/channels.yaml.')


def assert_lane(key: str, pipeline: str, context: str = '') -> None:
    """Refuse when ``pipeline`` is not a lane this channel is registered on.

    Catches the ``the_other_guys``-on-shorts class of error: a channel whose
    registry entry names the wrong lane mints its token into a config dir the
    real publisher never reads, and publishes content its audience never asked
    for. Skipped silently when the channel declares no pipelines, so an
    incompletely described channel is never blocked by this.
    """
    pipeline = (pipeline or '').strip().lower()
    if not pipeline or content_mode() == MODE_OFF:
        return
    lanes = expected_pipelines(key)
    if not lanes or pipeline in lanes:
        return
    summary = content_summary(key)
    _content_failure(
        f'WRONG LANE: {key!r} is registered for {lanes} but a {pipeline!r} '
        f'publish was attempted'
        + (f'. That channel posts: {summary}' if summary else ''),
        context)


def assert_content(key: str, pipeline: str = '', variant: str = '',
                   niche: str = '', context: str = '') -> None:
    """Full content-routing check for a publish about to happen.

    Every argument is optional and only DECLARED facts are compared, so this is
    safe to call from anywhere with whatever the caller happens to know. What it
    catches:

    * ``pipeline`` not among the channel's ``pipelines``
    * ``variant`` (ranking's normal/contrast) disagreeing with ``variant:``
    * ``niche`` not among the channel's ``niches:`` allow-list
    """
    if content_mode() == MODE_OFF:
        return
    assert_lane(key, pipeline, context)

    variant = (variant or '').strip().lower()
    declared_variant = expected_variant(key)
    if variant and declared_variant and variant != declared_variant:
        _content_failure(
            f'WRONG VARIANT: {key!r} publishes {declared_variant!r} content per '
            f'channels.yaml, but a {variant!r} item was routed to it',
            context)

    niche = (niche or '').strip()
    allowed = expected_niches(key)
    if niche and allowed and niche not in allowed:
        summary = content_summary(key)
        _content_failure(
            f'WRONG NICHE: {key!r} accepts {allowed} but niche {niche!r} was '
            f'routed to it'
            + (f'. That channel posts: {summary}' if summary else ''),
            context)

    logger.info('CHANNEL_CONTENT_OK key=%s pipeline=%s variant=%s niche=%s',
                key, pipeline or '-', variant or '-', niche or '-')


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
