"""
Shared fixtures.

Every test runs against a throwaway ``MILO_HOME``. That is not politeness — the
modules cache resolved paths at import time, so a test that forgets to isolate
would read and *write* the developer's real brain. The ``milo_home`` fixture
makes that impossible to get wrong by accident.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


#: Modules holding module-level singletons or cached paths. Reloading them
#: after MILO_HOME changes is what makes a second "machine" genuinely fresh.
_STATEFUL = (
    "miloctl.paths",
    "miloctl.env",
    "miloctl.curated",
    "miloctl.memory",
    "miloctl.sessions",
    "miloctl.profile",
    "miloctl.skills",
    "miloctl.routines",
    "miloctl.vault",
    "miloctl.backup",
)


def reload_miloctl() -> None:
    """Re-import the stateful modules so they re-resolve paths from the env."""
    from miloctl import paths

    paths.reset_cache()
    for name in _STATEFUL:
        mod = sys.modules.get(name)
        if mod is not None:
            importlib.reload(mod)


@pytest.fixture
def milo_home(tmp_path, monkeypatch):
    """An isolated Milo installation. Yields its root."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MILO_HOME", str(home))
    monkeypatch.setenv("MILO_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("MILO_REPO_ROOT", str(tmp_path / "repo"))
    # Keep harness sync away from the real ~/.config during tests.
    monkeypatch.setenv("HOME", str(home))
    reload_miloctl()

    from miloctl import paths

    paths.ensure_tree()
    yield home


@pytest.fixture
def git_repo(tmp_path):
    """A real git repo to act as the backup remote/checkout."""
    repo = tmp_path / "portable"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    # Make it look like a real checkout so repo_root() accepts it.
    (repo / "pyproject.toml").write_text("[project]\nname='milo'\n")
    (repo / "miloctl").mkdir()
    return repo


def switch_machine(monkeypatch, tmp_path, name: str, repo: Path):
    """Simulate moving to a different computer that shares only ``repo``."""
    home = tmp_path / name
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("MILO_HOME", str(home))
    monkeypatch.setenv("MILO_VAULT_DIR", str(home / "vault"))
    monkeypatch.setenv("MILO_REPO_ROOT", str(repo))
    monkeypatch.setenv("HOME", str(home))
    reload_miloctl()
    from miloctl import paths

    paths.ensure_tree()
    return home
