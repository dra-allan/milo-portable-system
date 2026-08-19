"""Pins the yt-dlp hardening contract.

These are the two regressions that cost the most time, so they get tests rather
than a comment:

* ``save_cookies`` must stay a no-op. When it does not, yt-dlp re-exports the
  shared cookiefile without the 1P auth cookies and every later download is
  bot-blocked -- with an error that points at YouTube, not at us.
* the option builder must never hand yt-dlp a client set that cannot use a GVS
  PO Token while also advertising a PO Token provider. That combination is what
  made the provider look broken for a day when it was simply never asked.

No network, no yt-dlp invocation: only the option construction is exercised, so
these run anywhere.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ytdlp = pytest.importorskip('_ytdlp')


def test_save_cookies_is_a_noop():
    """The whole point of the subclass. Do not let this come back."""
    assert _ytdlp.NoWritebackYDL.save_cookies is not __import__(
        'yt_dlp').YoutubeDL.save_cookies
    # Called unbound so no YoutubeDL has to be constructed.
    assert _ytdlp.NoWritebackYDL.save_cookies(object()) is None


def test_default_clients_can_use_a_po_token():
    """android*/ios clients never fetch a GVS PO Token, so they cannot lead."""
    clients = _ytdlp.player_clients()
    assert clients, 'there must always be at least one player client'
    assert not clients[0].startswith(('android', 'ios')), (
        f'{clients[0]!r} cannot use a GVS PO Token; a POT-capable client '
        '(mweb/tv/web_safari) has to come first or the provider is never asked'
    )


def test_harden_wires_the_pot_provider(monkeypatch):
    monkeypatch.setenv('YTDLP_POT_BASE_URL', 'http://127.0.0.1:4416')
    monkeypatch.setenv('YTDLP_PLAYER_CLIENTS', 'mweb')
    opts = _ytdlp.harden({})
    youtube = opts['extractor_args']['youtube']
    assert youtube['player_client'] == ['mweb']
    # 'always' rather than the default policy: under the GVS binding experiment
    # yt-dlp considers the token merely 'recommended' for some clients and
    # skips the provider, which is exactly the failure being fixed.
    assert youtube['fetch_pot'] == ['always']
    # A failed token fetch must degrade to fewer formats, not to zero.
    assert youtube['formats'] == ['missing_pot']
    assert opts['extractor_args']['youtubepot-bgutilhttp']['base_url'] == [
        'http://127.0.0.1:4416']


def test_harden_overrides_stale_caller_clients(monkeypatch):
    """downloader._client_opts used to default to android_vr,ios.

    A merge that let the caller win would silently keep the broken client set,
    so player_client is one of the keys the hardening owns outright.
    """
    monkeypatch.delenv('YTDLP_PLAYER_CLIENTS', raising=False)
    caller = {'extractor_args': {'youtube': {'player_client': ['android_vr', 'ios'],
                                             'skip': ['translated_subs']}}}
    opts = _ytdlp.harden(caller)
    youtube = opts['extractor_args']['youtube']
    assert youtube['player_client'] == list(_ytdlp.DEFAULT_PLAYER_CLIENTS)
    # Unrelated caller args must survive the merge untouched.
    assert youtube['skip'] == ['translated_subs']


def test_harden_does_not_mutate_the_callers_dict():
    """The downloader reuses one template across parallel section fetches."""
    caller = {'quiet': True}
    _ytdlp.harden(caller)
    assert caller == {'quiet': True}


def test_harden_respects_explicit_cookiesfrombrowser(monkeypatch, tmp_path):
    cookies = tmp_path / 'cookies.txt'
    cookies.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
    monkeypatch.setenv('YTDLP_COOKIES_FILE', str(cookies))
    opts = _ytdlp.harden({'cookiesfrombrowser': ('chrome',)})
    assert 'cookiefile' not in opts, (
        'a caller that chose a browser cookie jar must not silently get a file'
    )


def test_harden_can_be_switched_off(monkeypatch):
    monkeypatch.setenv('MILO_YTDLP_HARDEN', '0')
    opts = _ytdlp.harden({'quiet': True})
    assert 'extractor_args' not in opts
