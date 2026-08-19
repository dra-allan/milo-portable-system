"""Hardened yt-dlp factory for the ranking lane.

This is a verbatim vendored copy of
``artisan/youtube-shorts-pipeline/_ytdlp.py``. The lanes are deliberately
self-contained (each one is deployable on its own), and the two copies were
already identical before this change -- keeping them identical is the contract.
Edit the shorts copy and mirror it; ``tests/test_ytdlp_hardening.py`` in each
lane pins the behaviour that must not drift.

One thing this fixes specifically for ranking: ``src/sourcing.py`` built its own
option dict with cookies from ``YT_COOKIES`` and no player-client selection at
all, so ranking discovery ran with yt-dlp's defaults and no PO Token provider.
Because sourcing already imports ``NoWritebackYDL`` from here, it now inherits
the same hardening as the shorts downloader without a single change to that
file.
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

DEFAULT_PLAYER_CLIENTS = ('mweb', 'tv', 'web_safari')
DEFAULT_POT_BASE_URL = 'http://127.0.0.1:4416'
DEFAULT_IMPERSONATE = 'chrome'
DEFAULT_JS_RUNTIMES = ('node',)

_AUTHORITATIVE = {
    'youtube': ('player_client', 'fetch_pot', 'formats'),
}

_LOGGED_ONCE = set()


def _log_once(key: str, level: int, message: str, *args) -> None:
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


def hardening_enabled() -> bool:
    return _env_flag('MILO_YTDLP_HARDEN', True)


def player_clients() -> List[str]:
    return _env_list('YTDLP_PLAYER_CLIENTS', DEFAULT_PLAYER_CLIENTS)


def js_runtimes() -> List[str]:
    return _env_list('YTDLP_JS_RUNTIMES', DEFAULT_JS_RUNTIMES)


def pot_base_url() -> str:
    raw = (os.getenv('YTDLP_POT_BASE_URL') or DEFAULT_POT_BASE_URL).strip()
    if raw.lower() in ('', 'off', 'none', 'disabled'):
        return ''
    return raw.rstrip('/')


def impersonate_target():
    raw = (os.getenv('YTDLP_IMPERSONATE') or DEFAULT_IMPERSONATE).strip()
    if raw.lower() in ('', 'off', 'none', 'disabled'):
        return None
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        _log_once('impersonate-missing', logging.INFO,
                  'curl_cffi not installed; skipping browser impersonation')
        return None
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str(raw)
    except Exception as exc:
        _log_once('impersonate-bad', logging.WARNING,
                  'YTDLP_IMPERSONATE=%r unusable (%s); continuing without it',
                  raw, exc)
        return None


def plugin_dirs() -> List[str]:
    candidates: List[str] = list(_env_list('YTDLP_PLUGIN_DIRS', ()))
    try:
        import site
        for base in site.getsitepackages() + [site.getusersitepackages()]:
            candidates.append(str(Path(base) / 'yt_dlp_plugins'))
    except Exception:
        pass
    out: List[str] = []
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.name == 'yt_dlp_plugins':
            path = path.parent
        resolved = str(path)
        if path.is_dir() and resolved not in out:
            out.append(resolved)
    return out


def cookiefile() -> str:
    for name in ('YTDLP_COOKIES_FILE', 'YT_COOKIES'):
        raw = (os.getenv(name) or '').strip()
        if raw and Path(raw).exists():
            return raw
    return ''


def _merge_extractor_args(existing: Optional[Dict], extra: Dict) -> Dict:
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
    opts = dict(params or {})
    if not hardening_enabled():
        _log_once('harden-off', logging.WARNING,
                  'MILO_YTDLP_HARDEN=0: yt-dlp hardening disabled')
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


class NoWritebackYDL(yt_dlp.YoutubeDL):
    """See the shorts lane's copy for the full rationale."""

    def __init__(self, params=None, auto_init=True, **kwargs):
        super().__init__(harden(params), auto_init=auto_init, **kwargs)

    def save_cookies(self):
        return None


def pot_provider_ready(timeout: float = 3.0) -> Tuple[bool, str]:
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
    out: List[Tuple[str, str]] = []
    out.append(('INFO', f'yt-dlp        {_version_of("yt_dlp")}'))

    ejs = _version_of('yt_dlp_ejs')
    if ejs == 'not installed':
        out.append(('FAIL', 'yt_dlp_ejs    not installed -- pip install -U '
                            'yt-dlp-ejs'))
    else:
        out.append(('INFO', f'yt_dlp_ejs    {ejs}  (stale ejs causes "The page '
                            'needs to be reloaded")'))

    curl = _version_of('curl_cffi')
    out.append(('INFO' if curl != 'not installed' else 'WARN',
                f'curl_cffi     {curl}'))

    import shutil
    runtimes = js_runtimes()
    missing = [rt for rt in runtimes if not shutil.which(rt)]
    out.append(('FAIL' if missing else 'INFO',
                f'js runtime    {",".join(missing) + " not on PATH" if missing else ",".join(runtimes) + " on PATH"}'))

    importable, detail = pot_plugin_importable()
    out.append(('INFO' if importable else 'FAIL', f'pot plugin    {detail}'))

    ready, detail = pot_provider_ready()
    out.append(('INFO' if ready else 'FAIL', f'pot server    {detail}'))

    dirs = plugin_dirs()
    out.append(('INFO' if dirs else 'WARN',
                f'plugin_dirs   {len(dirs)} passed to yt-dlp'))

    clients = player_clients()
    dead = [c for c in clients if c.startswith(('android', 'ios'))]
    out.append(('WARN' if dead else 'INFO',
                f'clients       {",".join(clients)}'
                + (f' -- {",".join(dead)} cannot use a GVS PO Token' if dead else '')))

    cookies = cookiefile()
    out.append(('INFO' if cookies else 'WARN',
                f'cookies       {cookies or "none"}'))
    return out


def print_diagnosis(rows: Optional[Iterable[Tuple[str, str]]] = None) -> int:
    rows = list(rows if rows is not None else diagnose())
    for level, message in rows:
        print(f'[{level:<4}] {message}')
    return 1 if any(level == 'FAIL' for level, _ in rows) else 0


if __name__ == '__main__':
    raise SystemExit(print_diagnosis())
