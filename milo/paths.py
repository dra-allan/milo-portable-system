"""
milo.paths — the single source of truth for *where things live*.

This module is the fix for Milo's #1 migration blocker: hardcoded
``C:/Users/user/...`` paths scattered across a dozen scripts.

Every path Milo touches is resolved here, in this order:

    1. explicit environment variable  (MILO_HOME, MILO_VAULT_DIR, ...)
    2. value stored in the resolved .env file
    3. a platform-aware default

Nothing else in the codebase is allowed to hardcode a path. If you find
yourself typing a ``/`` and a username in another module, put it here
instead.

Platform defaults
-----------------
=============  ==========================================================
Windows        vault -> ~/Desktop/DRA BRAINS
macOS          vault -> ~/Documents/DRA BRAINS
Termux         vault -> ~/vault
Linux/other    vault -> ~/vault
=============  ==========================================================

``MILO_HOME`` is always ``~/.milo`` unless overridden, on every platform.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

__all__ = [
    "Platform",
    "detect_platform",
    "MiloPaths",
    "get_paths",
    "reset_paths_cache",
]


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Platform:
    """Normalised description of the machine Milo is running on."""

    name: str  # "windows" | "macos" | "termux" | "linux" | "unknown"
    is_windows: bool = False
    is_macos: bool = False
    is_termux: bool = False
    is_linux: bool = False  # true for plain Linux, false for Termux
    is_posix: bool = False
    is_wsl: bool = False

    @property
    def service_manager(self) -> str:
        """Which service backend this platform should use."""
        if self.is_windows:
            return "nssm"
        if self.is_termux:
            return "screen"
        if self.is_macos:
            return "launchd"
        if self.is_linux and not self.is_wsl:
            return "systemd"
        # WSL frequently ships without a systemd user bus.
        return "screen"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        extra = " (WSL)" if self.is_wsl else ""
        return f"{self.name}{extra}"


@lru_cache(maxsize=1)
def detect_platform() -> Platform:
    """Detect the current platform once and cache the result."""
    system = platform.system().lower()

    # Termux exports PREFIX=/data/data/com.termux/files/usr
    prefix = os.environ.get("PREFIX", "")
    is_termux = "com.termux" in prefix

    is_wsl = False
    if system == "linux":
        # WSL1 and WSL2 both advertise themselves in the kernel release string.
        try:
            release = platform.uname().release.lower()
            is_wsl = "microsoft" in release or "wsl" in release
        except Exception:  # pragma: no cover - defensive
            is_wsl = False

    if system == "windows":
        return Platform("windows", is_windows=True)
    if system == "darwin":
        return Platform("macos", is_macos=True, is_posix=True)
    if is_termux:
        return Platform("termux", is_termux=True, is_posix=True)
    if system == "linux":
        return Platform("linux", is_linux=True, is_posix=True, is_wsl=is_wsl)
    return Platform(system or "unknown", is_posix=os.name == "posix")


# ---------------------------------------------------------------------------
# Environment file reading (tiny, dependency-free)
# ---------------------------------------------------------------------------


def read_env_file(path: Path) -> Dict[str, str]:
    """Parse a ``KEY=value`` file into a dict.

    Deliberately minimal: no interpolation, no multiline values, no
    dependencies. Supports ``#`` comments, blank lines, ``export`` prefixes
    and optional surrounding quotes.
    """
    values: Dict[str, str] = {}
    if not path or not path.is_file():
        return values
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip a single matched pair of surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _expand(value: str) -> Path:
    """Expand ``~`` and ``$VARS`` then return an absolute Path."""
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


# ---------------------------------------------------------------------------
# The path table
# ---------------------------------------------------------------------------


@dataclass
class MiloPaths:
    """Every filesystem location Milo cares about, resolved for this machine."""

    platform: Platform
    home: Path  # user home
    milo_home: Path  # ~/.milo — all mutable state
    repo_root: Path  # the checked-out milo-portable-system repo
    vault_dir: Path  # dra-brains Obsidian vault (cold memory)
    env_file: Path  # ~/.milo/.env

    # Derived state directories (all under milo_home)
    state_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    backups_dir: Path = field(init=False)
    skills_dir: Path = field(init=False)
    memories_dir: Path = field(init=False)
    run_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    bin_dir: Path = field(init=False)

    # Well-known files
    brain_db: Path = field(init=False)  # unified memory SQLite
    awareness_file: Path = field(init=False)
    config_file: Path = field(init=False)  # milo.json (non-secret settings)

    # External / harness config locations
    opencode_config_dir: Path = field(init=False)
    claude_config_dir: Path = field(init=False)
    engram_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        mh = self.milo_home
        self.state_dir = mh / "state"
        self.logs_dir = mh / "logs"
        self.backups_dir = mh / "backups"
        self.skills_dir = mh / "skills"
        self.memories_dir = mh / "memories"
        self.run_dir = mh / "run"
        self.cache_dir = mh / "cache"
        self.bin_dir = mh / "bin"

        self.brain_db = self.state_dir / "brain.sqlite"
        self.awareness_file = self.state_dir / "awareness.json"
        self.config_file = mh / "milo.json"

        self.opencode_config_dir = _env_path(
            "MILO_OPENCODE_CONFIG_DIR", self.home / ".config" / "opencode"
        )
        self.claude_config_dir = _env_path(
            "MILO_CLAUDE_CONFIG_DIR", self.home / ".claude"
        )
        self.engram_dir = _env_path("ENGRAM_DATA_DIR", self.home / ".engram")

    # -- helpers ----------------------------------------------------------

    @property
    def engram_db(self) -> Path:
        return self.engram_dir / "engram.db"

    @property
    def assets_dir(self) -> Path:
        """Bundled assets shipped inside the repo (identity, skills, templates)."""
        return self.repo_root / "assets"

    def ensure(self) -> "MiloPaths":
        """Create every directory Milo needs. Idempotent."""
        for directory in (
            self.milo_home,
            self.state_dir,
            self.logs_dir,
            self.backups_dir,
            self.skills_dir,
            self.memories_dir,
            self.run_dir,
            self.cache_dir,
            self.bin_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def as_dict(self) -> Dict[str, str]:
        """Flat mapping used for template rendering and ``milo doctor``."""
        return {
            "MILO_HOME": str(self.milo_home),
            "MILO_REPO": str(self.repo_root),
            "MILO_STATE_DIR": str(self.state_dir),
            "MILO_LOGS_DIR": str(self.logs_dir),
            "MILO_BACKUPS_DIR": str(self.backups_dir),
            "MILO_SKILLS_DIR": str(self.skills_dir),
            "MILO_MEMORIES_DIR": str(self.memories_dir),
            "MILO_BIN_DIR": str(self.bin_dir),
            "MILO_BRAIN_DB": str(self.brain_db),
            "MILO_AWARENESS_FILE": str(self.awareness_file),
            "VAULT_DIR": str(self.vault_dir),
            "ENGRAM_DIR": str(self.engram_dir),
            "ENGRAM_DB": str(self.engram_db),
            "OPENCODE_CONFIG_DIR": str(self.opencode_config_dir),
            "CLAUDE_CONFIG_DIR": str(self.claude_config_dir),
            "HOME": str(self.home),
        }


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return _expand(raw) if raw else default


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def default_vault_dir(home: Path, plat: Platform) -> Path:
    """Platform-appropriate default location for the dra-brains vault."""
    if plat.is_windows:
        return home / "Desktop" / "DRA BRAINS"
    if plat.is_macos:
        return home / "Documents" / "DRA BRAINS"
    # Termux, Linux, everything else: short, space-free, shell-friendly.
    return home / "vault"


def _find_repo_root(start: Optional[Path] = None) -> Path:
    """Walk upwards looking for the repo marker, else fall back to cwd."""
    here = (start or Path(__file__).resolve().parent).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "assets").is_dir() and (candidate / "milo").is_dir():
            return candidate
        if (candidate / ".git").exists() and (candidate / "milo").is_dir():
            return candidate
    # Installed as a package without the repo: use the parent of the package.
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

_CACHE: Optional[MiloPaths] = None


def get_paths(refresh: bool = False) -> MiloPaths:
    """Resolve (and cache) every Milo path for this machine."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE

    plat = detect_platform()
    home = Path(os.path.expanduser("~")).resolve()

    milo_home = _env_path("MILO_HOME", home / ".milo")
    env_file = _env_path("MILO_ENV_FILE", milo_home / ".env")

    # Values written into .env act as defaults *below* real env vars, so a
    # one-off `MILO_VAULT_DIR=... milo doctor` still wins.
    file_env = read_env_file(env_file)

    def lookup(key: str) -> Optional[Path]:
        if os.environ.get(key):
            return _expand(os.environ[key])
        if file_env.get(key):
            return _expand(file_env[key])
        return None

    def resolve(fallback: Path, *keys: str) -> Path:
        for key in keys:
            found = lookup(key)
            if found is not None:
                return found
        return fallback

    repo_root = resolve(_find_repo_root(), "MILO_REPO")
    # MILO_VAULT_DIR is the canonical name; VAULT_DIR is kept for
    # backwards compatibility with the old scripts.
    vault_dir = resolve(
        default_vault_dir(home, plat), "MILO_VAULT_DIR", "VAULT_DIR"
    )

    _CACHE = MiloPaths(
        platform=plat,
        home=home,
        milo_home=milo_home,
        repo_root=repo_root,
        vault_dir=vault_dir,
        env_file=env_file,
    )
    return _CACHE


def reset_paths_cache() -> None:
    """Drop the cached path table (used by tests and after ``milo install``)."""
    global _CACHE
    _CACHE = None
    detect_platform.cache_clear()


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    p = get_paths()
    print(f"platform: {p.platform}  (services via {p.platform.service_manager})")
    width = max(len(k) for k in p.as_dict())
    for key, value in p.as_dict().items():
        print(f"  {key:<{width}}  {value}")
    sys.exit(0)
