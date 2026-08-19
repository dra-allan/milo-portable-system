"""Hardened yt-dlp factory shared by every Milo lane.

WHY THIS FILE IS MORE THAN A ONE-LINE SUBCLASS
----------------------------------------------
It used to be exactly one override (see COOKIE WRITEBACK below). It now also
carries the YouTube-extraction hardening, because every download in this repo
goes through ``from _ytdlp import NoWritebackYDL as YoutubeDL`` -- so fixing
extraction here fixes ``src/downloader.py``, ``src/sourcing.py`` and every
ad-hoc script at once, instead of in a dozen option dicts that drift apart.

COOKIE WRITEBACK (do not regress)
---------------------------------
``YoutubeDL.save_cookies()`` rewrites the configured cookiefile on every
``close()``. On this VPS that re-export drops the 1P auth cookies (the known
"broken 3P-only export"), which bot-blocks every subsequent download. Downloads
only ever need to READ cookies, so the save is a no-op. Pinned by
``tests/test_ytdlp_hardening.py``.

THE 2026-08 EXTRACTION BLOCK, AND WHAT ACTUALLY FIXES IT
--------------------------------------------------------
Symptom: every video, every player client, ``Video unavailable``, and
``dQw4w9WgXcQ`` returning ``The page needs to be reloaded.`` A browser on the
same IP plays fine, so it is not a plain IP ban. Three separate root causes
were hiding behind that one symptom:

1. **The PO Token provider was never being invoked.** bgutil's server was up on
   :4416 and the plugin was pip-installed, but yt-dlp logs
   ``[pot] PO Token Providers: none``. Two reasons, both real:

   * the configured clients were ``android_vr,ios,web_safari``.
     ``android*``/``ios`` clients do not use GVS PO Tokens at all, and
     ``android`` is skipped outright once cookies are present ("Skipping client
     android since it does not support cookies"). yt-dlp had no reason to ask
     for a token, so the provider sat idle while the run burned through clients
     a token could never have helped.
   * plugin discovery is per-interpreter. ``pip install`` into one environment
     and running the daemon from another gives ``Plugin directories: none``
     with no error.

   Fix: POT-capable clients first (``mweb`` is upstream's recommendation), an
   explicit ``youtubepot-bgutilhttp:base_url``, ``fetch_pot=always`` so the
   token is fetched even when yt-dlp only thinks it is *recommended*,
   ``formats=missing_pot`` so a failed fetch degrades to fewer formats instead
   of zero, and ``plugin_dirs`` set explicitly.

2. **"The page needs to be reloaded" is a stale JS-challenge solver, not a
   ban.** Upstream closed that report (yt-dlp#16212) with a bump of
   ``yt-dlp-ejs``, not with a config change. A yt-dlp pinned at 2026.7.4 next
   to an old ``yt_dlp_ejs`` reproduces it forever. :func:`diagnose` reports
   both versions so this is a one-command finding rather than a day of
   guessing.

3. **No browser-grade TLS.** yt-dlp supports impersonation natively through
   curl_cffi. A datacenter IP with a stock urllib fingerprint is the cheapest
   possible thing to flag. ``YTDLP_IMPERSONATE`` (default ``chrome``) turns it
   on when curl_cffi is installed and does nothing when it is not.

Every default here is the upstream-recommended value rather than a guess, and
every one is env-overridable so the VPS can be retuned without a deploy. Set
``MILO_YTDLP_HARDEN=0`` to opt a single process back out entirely.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import yt_dlp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# POT-capable clients, most-likely-to-work first.
#
# ``mweb`` is upstream's TL;DR recommendation for a GVS PO Token setup. ``tv``
# and ``web_safari`` follow as fallbacks. ``android_vr`` and ``ios`` are
# deliberately ABSENT: they cannot use a GVS PO Token, so on a fingerprinted IP
# they are dead ends that also prevent the provider from ever being asked. Add
# them back through YTDLP_PLAYER_CLIENTS if a future experiment makes them
# useful again.
DEFAULT_PLAYER_CLIENTS = ('mweb', 'tv', 'web_safari')

# bgutil-ytdlp-pot-provider's HTTP server. 4416 is the port MiloRoutines starts
# it on; upstream's own default is 4416 as well.
DEFAULT_POT_BASE_URL = 'http://127.0.0.1:4416'

# curl_cffi impersonation target. 'chrome' tracks the newest Chrome build the
# installed curl_cffi knows about, which is what we want -- pinning a version
# here would go stale silently.
DEFAULT_IMPERSONATE = 'chrome'

DEFAULT_JS_RUNTIMES = ('node',)

# extractor_args keys the hardening OWNS. A caller that sets these is a caller
# that predates this file (downloader._client_opts defaulted to android_vr/ios),
# so the hardened value wins rather than losing a merge to stale config.
_AUTHORITATIVE = {
    'youtube': ('player_client', 'fetch_pot', 'formats'),
}

_LOGGED_ONCE = set()


def _log_once(key: str, level: int, message: str, *args) -> None:
    """Log a configuration fact once per process.

    Section fetches run several workers in parallel and each builds its own
    YoutubeDL, so an unguarded info line here would print dozens of times per
    clip and bury the actual download log.
    """
    if key in _LOGGED_ONCE:
        return
    _LOGGED_ONCE.add(key)
    logger.log(level, message, *args)


def _env_list(name: str, default: Sequence[str]) -> List[str]:
    raw = (os.getenv(name) or '').strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(',') if item.strip()]


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).split('#')[0].strip().lower() in ('1', 'true', 'yes', 'on')


# ---------------------------------------------------------------------------
# Option builders
# ---------------------------------------------------------------------------
def hardening_enabled() -> bool:
    return _env_flag('MILO_YTDLP_HARDEN', True)


def player_clients() -> List[str]:
    return _env_list('YTDLP_PLAYER_CLIENTS', DEFAULT_PLAYER_CLIENTS)


def js_runtimes() -> List[str]:
    return _env_list('YTDLP_JS_RUNTIMES', DEFAULT_JS_RUNTIMES)


def pot_base_url() -> str:
    """Base URL of the bgutil POT provider, or '' when disabled.

    Set ``YTDLP_POT_BASE_URL=off`` to stop advertising a provider at all (useful
    when proving that a failure is not POT-related).
    """
    raw = (os.getenv('YTDLP_POT_BASE_URL') or DEFAULT_POT_BASE_URL).strip()
    if raw.lower() in ('', 'off', 'none', 'disabled'):
        return ''
    return raw.rstrip('/')


def impersonate_target():
    """An ``ImpersonateTarget`` for curl_cffi, or None.

    Returns None -- rather than raising -- when curl_cffi is not installed or
    the requested target is unknown, because impersonation is an improvement to
    the request fingerprint, not a requirement. A box without curl_cffi should
    keep working exactly as before.
    """
    raw = (os.getenv('YTDLP_IMPERSONATE') or DEFAULT_IMPERSONATE).strip()
    if raw.lower() in ('', 'off', 'none', 'disabled'):
        return None
    try:
        import curl_cffi  # noqa: F401  (presence check only)
    except ImportError:
        _log_once('impersonate-missing', logging.INFO,
                  'curl_cffi not installed; skipping browser impersonation '
                  '(pip install "yt-dlp[default,curl-cffi]" to enable)')
        return None
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str(raw)
    except Exception as exc:
        _log_once('impersonate-bad', logging.WARNING,
                  'YTDLP_IMPERSONATE=%r is not a usable target (%s); '
                  'continuing without impersonation', raw, exc)
        return None


def plugin_dirs() -> List[str]:
    """Directories yt-dlp should scan for plugins, newest-first.

    Passing these explicitly is the fix for ``Plugin directories: none``:
    discovery is per-interpreter, so a provider pip-installed into one venv is
    invisible to a daemon started from another. Only directories that exist are
    returned, so a stale entry never breaks a run.
    """
    candidates: List[str] = []
    for raw in _env_list('YTDLP_PLUGIN_DIRS', ()):
        candidates.append(raw)
    # The layout `pip install bgutil-ytdlp-pot-provider` produces, resolved
    # against whichever interpreter is importing this module.
    try:
        import site
        for base in site.getsitepackages() + [site.getusersitepackages()]:
            candidates.append(str(Path(base) / 'yt_dlp_plugins'))
    except Exception:
        pass
    out: List[str] = []
    for candidate in candidates:
        path = Path(candidate).expanduser()
        # yt-dlp wants the directory CONTAINING yt_dlp_plugins, so hand it the
        # parent when the env value points at the package itself.
        if path.name == 'yt_dlp_plugins':
            path = path.parent
        resolved = str(path)
        if path.is_dir() and resolved not in out:
            out.append(resolved)
    return out


def cookiefile() -> str:
    """Shared cookies file from the environment, or ''.

    Both ``YTDLP_COOKIES_FILE`` (shorts lane) and ``YT_COOKIES`` (ranking lane)
    are accepted, because the two lanes named the same thing differently and
    the ranking lane consequently ran without cookies at all.
    """
    for name in ('YTDLP_COOKIES_FILE', 'YT_COOKIES'):
        raw = (os.getenv(name) or '').strip()
        if raw and Path(raw).exists():
            return raw
    return ''


def _merge_extractor_args(existing: Optional[Dict], extra: Dict) -> Dict:
    """Deep-merge extractor args, letting the hardening win where it must."""
    out: Dict[str, Dict] = {ie: dict(args or {})
                            for ie, args in (existing or {}).items()}
    for ie, args in extra.items():
        target = out.setdefault(ie, {})
        owned = _AUTHORITATIVE.get(ie, ())
        for key, value in args.items():
            if key in owned or key not in target:
                target[key] = value
    return out


def youtube_extractor_args() -> Dict[str, Dict[str, List[str]]]:
    """The extractor-args half of the fix.

    ``fetch_pot=always`` matters more than it looks: yt-dlp only *requests* a
    token when its policy for the chosen client says the token is required.
    Under the GVS binding experiment the policy still reports "recommended" for
    some clients, so the provider is skipped and the request 403s / comes back
    UNPLAYABLE. ``always`` removes that judgement call.

    ``formats=missing_pot`` keeps a failed token fetch survivable: the formats
    that need a token are still listed (and may still work), instead of the run
    dying with "Requested format is not available".
    """
    args: Dict[str, Dict[str, List[str]]] = {
        'youtube': {
            'player_client': player_clients(),
            'fetch_pot': ['always'],
            'formats': ['missing_pot'],
        },
    }
    base = pot_base_url()
    if base:
        args['youtubepot-bgutilhttp'] = {'base_url': [base]}
    return args


def harden(params: Optional[Dict]) -> Dict:
    """Return a copy of ``params`` with the YouTube hardening applied.

    Never mutates the caller's dict: the downloader reuses one option template
    across parallel section fetches, and mutating it in place would let one
    worker's retry tuning leak into another's.
    """
    opts = dict(params or {})
    if not hardening_enabled():
        _log_once('harden-off', logging.WARNING,
                  'MILO_YTDLP_HARDEN=0: yt-dlp hardening disabled for this '
                  'process (cookie writeback is still suppressed)')
        return opts

    opts['extractor_args'] = _merge_extractor_args(
        opts.get('extractor_args'), youtube_extractor_args())

    if not opts.get('js_runtimes'):
        opts['js_runtimes'] = {rt: {} for rt in js_runtimes()}

    dirs = plugin_dirs()
    if dirs and not opts.get('plugin_dirs'):
        opts['plugin_dirs'] = dirs

    if not opts.get('impersonate'):
        target = impersonate_target()
        if target is not None:
            opts['impersonate'] = target

    # Cookies: only fill in when the caller has not decided. A caller that set
    # cookiesfrombrowser explicitly must not silently get a file instead.
    if not opts.get('cookiefile') and not opts.get('cookiesfrombrowser'):
        found = cookiefile()
        if found:
            opts['cookiefile'] = found

    _log_once(
        'harden-on', logging.INFO,
        'yt-dlp hardened: clients=%s pot=%s impersonate=%s plugin_dirs=%d '
        'cookies=%s',
        ','.join(player_clients()), pot_base_url() or 'off',
        getattr(opts.get('impersonate'), 'client', 'off') or 'off',
        len(opts.get('plugin_dirs') or []),
        'yes' if (opts.get('cookiefile') or opts.get('cookiesfrombrowser')) else 'no',
    )
    return opts


# ---------------------------------------------------------------------------
# The class every lane imports
# ---------------------------------------------------------------------------
class NoWritebackYDL(yt_dlp.YoutubeDL):
    """YoutubeDL that never rewrites the cookiefile and is always hardened.

    Hardening is applied in ``__init__`` rather than at each call site on
    purpose: there are three lanes, two dozen option dicts and several scripts,
    and the last time this was configured per-site the lanes silently drifted
    onto different player clients. One choke point, one behaviour.
    """

    def __init__(self, params=None, auto_init=True, **kwargs):
        super().__init__(harden(params), auto_init=auto_init, **kwargs)

    def save_cookies(self):
        # See COOKIE WRITEBACK in the module docstring. Do not "fix" this.
        return None


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
def pot_provider_ready(timeout: float = 3.0) -> Tuple[bool, str]:
    """Is the bgutil POT server answering? Returns ``(ok, detail)``.

    Checked over HTTP rather than by looking for a listening socket, because a
    process bound to the port that is not the provider is exactly the failure
    this is meant to catch.
    """
    base = pot_base_url()
    if not base:
        return False, 'POT provider disabled (YTDLP_POT_BASE_URL=off)'
    try:
        with urllib.request.urlopen(base + '/ping', timeout=timeout) as response:
            body = response.read(4096).decode('utf-8', 'replace').strip()
        try:
            parsed = json.loads(body)
            version = parsed.get('version') or parsed.get('server_uptime') or body
        except ValueError:
            version = body[:120] or 'ok'
        return True, f'{base} responding ({version})'
    except urllib.error.URLError as exc:
        return False, f'{base} unreachable: {exc.reason}'
    except Exception as exc:
        return False, f'{base} unreachable: {exc}'


def pot_plugin_importable() -> Tuple[bool, str]:
    """Can THIS interpreter import the provider plugin?

    The distinction between "installed" and "importable by the process that
    actually runs the pipeline" is the whole bug: ``pip show`` said yes while
    yt-dlp said ``PO Token Providers: none``.
    """
    try:
        import yt_dlp_plugins  # noqa: F401
    except ImportError as exc:
        return False, f'yt_dlp_plugins not importable: {exc}'
    try:
        __import__('yt_dlp_plugins.extractor.getpot_bgutil_http')
        return True, 'getpot_bgutil_http importable'
    except ImportError as exc:
        return False, f'yt_dlp_plugins found but provider missing: {exc}'


def _version_of(module_name: str) -> str:
    try:
        module = __import__(module_name)
    except ImportError:
        return 'not installed'
    for attr in ('__version__', 'version', 'VERSION'):
        value = getattr(module, attr, None)
        if isinstance(value, str):
            return value
    try:
        from importlib.metadata import version
        return version(module_name.replace('_', '-'))
    except Exception:
        return 'unknown'


def diagnose() -> List[Tuple[str, str]]:
    """Everything that decides whether extraction works, as (level, message).

    Ordered so the first FAIL is the thing to fix. ``yt_dlp_ejs`` is checked
    explicitly because a stale solver is what produces "The page needs to be
    reloaded", and nothing in the error text says so.
    """
    out: List[Tuple[str, str]] = []

    out.append(('INFO', f'yt-dlp        {_version_of("yt_dlp")}'))

    ejs = _version_of('yt_dlp_ejs')
    if ejs == 'not installed':
        out.append(('FAIL', 'yt_dlp_ejs    not installed -- the JS challenge '
                            'cannot be solved. pip install -U yt-dlp-ejs'))
    else:
        out.append(('INFO', f'yt_dlp_ejs    {ejs}  (a stale ejs is what causes '
                            '"The page needs to be reloaded"; upstream fixed '
                            'yt-dlp#16212 by bumping it, so update this first)'))

    curl = _version_of('curl_cffi')
    out.append((
        'INFO' if curl != 'not installed' else 'WARN',
        f'curl_cffi     {curl}'
        + ('' if curl != 'not installed'
           else ' -- no browser TLS fingerprint; install for impersonation'),
    ))

    runtimes = js_runtimes()
    import shutil
    missing = [rt for rt in runtimes if not shutil.which(rt)]
    if missing:
        out.append(('FAIL', f'js runtime    {",".join(missing)} not on PATH -- '
                            'the n-challenge cannot be solved and only '
                            'storyboard formats will resolve'))
    else:
        out.append(('INFO', f'js runtime    {",".join(runtimes)} on PATH'))

    importable, detail = pot_plugin_importable()
    out.append(('INFO' if importable else 'FAIL', f'pot plugin    {detail}'))

    ready, detail = pot_provider_ready()
    out.append(('INFO' if ready else 'FAIL', f'pot server    {detail}'))

    dirs = plugin_dirs()
    out.append((
        'INFO' if dirs else 'WARN',
        f'plugin_dirs   {len(dirs)} passed to yt-dlp'
        + (f' ({dirs[0]})' if dirs else ' -- discovery left to yt-dlp defaults'),
    ))

    clients = player_clients()
    dead = [c for c in clients if c.startswith(('android', 'ios'))]
    if dead:
        out.append(('WARN', f'clients       {",".join(clients)} -- '
                            f'{",".join(dead)} cannot use a GVS PO Token, so '
                            'the provider is never asked on those attempts'))
    else:
        out.append(('INFO', f'clients       {",".join(clients)}'))

    cookies = cookiefile()
    out.append((
        'INFO' if cookies else 'WARN',
        f'cookies       {cookies or "none (age-gated sources will fail)"}',
    ))

    return out


def print_diagnosis(rows: Optional[Iterable[Tuple[str, str]]] = None) -> int:
    rows = list(rows if rows is not None else diagnose())
    for level, message in rows:
        print(f'[{level:<4}] {message}')
    return 1 if any(level == 'FAIL' for level, _ in rows) else 0


if __name__ == '__main__':
    raise SystemExit(print_diagnosis())
