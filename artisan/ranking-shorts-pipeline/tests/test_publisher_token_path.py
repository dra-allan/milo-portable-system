"""Token path resolution tests.

The recurring failure this guards against: yt_secrets (reauth_all_channels.bat)
writes ``youtube_token_<key>.json`` while the publisher historically read only
``youtube_token_ranking_<key>.json``. Every re-auth then needed a manual copy
before the pipeline saw the fresh grant, and a stale copy silently kept being
used instead. Resolution must pick whichever candidate exists and is freshest,
and fall back to the legacy name when nothing exists yet.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import publisher  # noqa: E402
from src.config import config  # noqa: E402


def _use_token_dir(tmp_path, monkeypatch):
    tokdir = tmp_path / 'config'
    tokdir.mkdir()
    monkeypatch.setattr(config, 'oauth_token_file',
                        str(tokdir / 'youtube_token_ranking.json'))
    return tokdir


def _touch(path, offset_seconds=0.0):
    path.write_text('{}', encoding='utf-8')
    stamp = time.time() + offset_seconds
    os.utime(path, (stamp, stamp))
    return path


def test_missing_token_falls_back_to_legacy_name(tmp_path, monkeypatch):
    tokdir = _use_token_dir(tmp_path, monkeypatch)
    resolved = publisher._token_path('some_channel')
    assert resolved == tokdir / 'youtube_token_ranking_some_channel.json'


def test_legacy_only(tmp_path, monkeypatch):
    tokdir = _use_token_dir(tmp_path, monkeypatch)
    legacy = _touch(tokdir / 'youtube_token_ranking_k.json')
    assert publisher._token_path('k') == legacy


def test_unprefixed_only_is_found_without_manual_copy(tmp_path, monkeypatch):
    """The reauth tool's filename must work with no copies at all."""
    tokdir = _use_token_dir(tmp_path, monkeypatch)
    local = _touch(tokdir / 'youtube_token_k.json')
    assert publisher._token_path('k') == local


def test_freshest_candidate_wins_local_newer(tmp_path, monkeypatch):
    tokdir = _use_token_dir(tmp_path, monkeypatch)
    _touch(tokdir / 'youtube_token_ranking_k.json')
    local = _touch(tokdir / 'youtube_token_k.json', offset_seconds=10)
    assert publisher._token_path('k') == local


def test_freshest_candidate_wins_legacy_newer(tmp_path, monkeypatch):
    tokdir = _use_token_dir(tmp_path, monkeypatch)
    legacy = _touch(tokdir / 'youtube_token_ranking_k.json', offset_seconds=10)
    _touch(tokdir / 'youtube_token_k.json')
    assert publisher._token_path('k') == legacy


def test_shared_pov_token_still_wins(tmp_path, monkeypatch):
    tokdir = _use_token_dir(tmp_path, monkeypatch)
    shared_dir = tmp_path / 'pov' / 'config'
    shared_dir.mkdir(parents=True)
    shared = _touch(shared_dir / 'youtube_token_k.json')
    _touch(tokdir / 'youtube_token_ranking_k.json', offset_seconds=99)
    monkeypatch.setenv('POV_SECRETS_DIR', str(shared_dir))
    assert publisher._token_path('k') == shared
