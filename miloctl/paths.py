"""
paths.py — every filesystem location Milo knows about.
======================================================

This module exists because the old system had ``C:/Users/user/...`` burned
into six different scripts. Change the username, change the OS, change the
drive — everything broke silently.

**Nothing anywhere else in the codebase may hardcode a path.** Ask here.

Resolution order for every location
-----------------------------------
1. Explicit environment variable (``MILO_HOME``, ``MILO_VAULT_DIR``, …)
2. Value stored in ``$MILO_HOME/.env``
3. Platform-aware default (below)

Platform defaults
-----------------
=================  ==========================================================
Location           Default
=================  ==========================================================
MILO_HOME          Windows ``%LOCALAPPDATA%\\milo`` · else ``~/.milo``
VAULT_DIR          Windows ``~/Desktop/DRA BRAINS`` · Termux ``~/storage/shared/vault``
                   · else ``~/vault``
ENGRAM_DIR         ``~/.engram``
WORKSPACE          ``~/milo-workspace``
=================  ==========================================================
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Dict, Optional

# ── Platform detection ────────────────────────────────────────────────────────

SYSTEM = platform.system()
IS_WINDOWS = SYSTEM == "Windows"
IS_MACOS = SYSTEM == "Darwin"
IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "") or Path(
    "/data/data/com.termux/files/usr"
).exists()
IS_WSL = "microsoft" in platform.uname().release.lower() if SYSTEM == "Linux" else False
IS_LINUX = SYSTEM == "Linux" and not IS_TERMUX

HOME = Path.home()


def platform_id() -> str:
    """Short slug used in configs, service names and telemetry-free logs."""
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_TERMUX:
        return "termux"
    if IS_WSL:
        return "wsl"
    if IS_LINUX:
        return "linux"
    return SYSTEM.lower() or "unknown"


# ── Bootstrap env reader (used before miloctl.env exists) ─────────────────────


def _read_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env parser. Kept here to avoid a circular import with env.py."""
    out: Dict[str, str] = {}
    try:
        if not path.is_file():
            return out
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            out[key.strip()] = value
    except OSError:
        pass
    return out


def _expand(value: str) -> Path:
    """Expand ``~``, ``$VAR`` and ``%VAR%`` then resolve to an absolute path."""
    value = os.path.expandvars(value.strip().strip('"').strip("'"))
    if IS_WINDOWS:
        # os.path.expandvars handles %VAR% on Windows already; be explicit for
        # cross-platform test runs.
        for key, val in os.environ.items():
            value = value.replace(f"%{key}%", val)
    return Path(value).expanduser()


# ── MILO_HOME ─────────────────────────────────────────────────────────────────


def _default_home() -> Path:
    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "milo"
    return HOME / ".milo"


_HOME_CACHE: Optional[Path] = None


def milo_home() -> Path:
    """Root of all Milo state. Everything else hangs off this."""
    global _HOME_CACHE
    if _HOME_CACHE is not None:
        return _HOME_CACHE
    raw = os.environ.get("MILO_HOME", "").strip()
    _HOME_CACHE = _expand(raw) if raw else _default_home()
    return _HOME_CACHE


def reset_cache() -> None:
    """Forget memoised values — used by tests and by ``milo config set``."""
    global _HOME_CACHE
    _HOME_CACHE = None


# ── Derived locations ─────────────────────────────────────────────────────────


def env_file() -> Path:
    return milo_home() / ".env"


#: Older/shorter spellings people actually type. Both forms always work, so a
#: hand-edited ``.env`` never silently fails to take effect.
_KEY_ALIASES: Dict[str, tuple] = {
    "MILO_VAULT_DIR": ("VAULT_DIR", "MILO_VAULT", "BRAINS_DIR"),
    "MILO_ENGRAM_DIR": ("ENGRAM_DIR",),
    "MILO_WORKSPACE": ("WORKSPACE_DIR", "MILO_WORKSPACE_DIR"),
    "MILO_REPOS_DIR": ("REPOS_DIR",),
    "MILO_SKILLS_DIR": ("SKILLS_DIR",),
}


def _cfg(key: str) -> str:
    """Look a key up in env vars first, then ``$MILO_HOME/.env``.

    Aliases are honoured so ``VAULT_DIR`` and ``MILO_VAULT_DIR`` are the same
    setting — one less way for a migration to fail quietly.
    """
    candidates = (key, *_KEY_ALIASES.get(key, ()))
    for name in candidates:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    dotenv = _read_env_file(env_file())
    for name in candidates:
        val = dotenv.get(name, "").strip()
        if val:
            return val
    return ""


def _resolve(key: str, default: Path) -> Path:
    raw = _cfg(key)
    return _expand(raw) if raw else default


def _default_vault() -> Path:
    if IS_WINDOWS:
        return HOME / "Desktop" / "DRA BRAINS"
    if IS_TERMUX:
        shared = HOME / "storage" / "shared" / "vault"
        return shared if shared.parent.exists() else HOME / "vault"
    if IS_MACOS:
        return HOME / "Documents" / "DRA BRAINS"
    return HOME / "vault"


def vault_dir() -> Path:
    """The Obsidian/markdown long-term memory vault (``dra-brains``)."""
    return _resolve("MILO_VAULT_DIR", _default_vault())


def engram_dir() -> Path:
    """Engram's own data directory (hot memory tier, if installed)."""
    return _resolve("MILO_ENGRAM_DIR", HOME / ".engram")


def workspace_dir() -> Path:
    """Default cwd the agent operates in when no project is specified."""
    return _resolve("MILO_WORKSPACE", HOME / "milo-workspace")


# -- state tree under MILO_HOME ------------------------------------------------

def state_dir() -> Path:
    return milo_home() / "state"


def logs_dir() -> Path:
    return _resolve("MILO_LOGS_DIR", milo_home() / "logs")


def backups_dir() -> Path:
    return _resolve("MILO_BACKUPS_DIR", milo_home() / "backups")


def cache_dir() -> Path:
    return milo_home() / "cache"


def bin_dir() -> Path:
    return milo_home() / "bin"


def run_dir() -> Path:
    """PID files, sockets, service scratch."""
    return milo_home() / "run"


def repos_dir() -> Path:
    """Where satellite git repos (vault, legacy repos) get cloned."""
    return _resolve("MILO_REPOS_DIR", milo_home() / "repos")


# -- memory --------------------------------------------------------------------

def memory_db() -> Path:
    """The single unified memory database. One brain, not two."""
    return _resolve("MILO_MEMORY_DB", state_dir() / "memory.db")


def sessions_db() -> Path:
    """Session transcripts + FTS index for cross-session recall."""
    return _resolve("MILO_SESSIONS_DB", state_dir() / "sessions.db")


def profile_file() -> Path:
    """The deepening user model (JSON)."""
    return state_dir() / "profile.json"


def memories_dir() -> Path:
    """MEMORY.md / USER.md — the bounded, human-editable curated tier.

    Deliberately *not* under ``state/``: these two files are meant to be opened
    and corrected by hand, so they sit at the top of ``$MILO_HOME`` where they
    are easy to find, not buried with the databases.
    """
    return _resolve("MILO_MEMORIES_DIR", milo_home() / "memories")


# -- skills / agents / cron -----------------------------------------------------

def skills_dir() -> Path:
    """User + agent-authored skills (writable)."""
    return _resolve("MILO_SKILLS_DIR", milo_home() / "skills")


def agents_dir() -> Path:
    """Persona + subagent definitions (writable copies)."""
    return milo_home() / "agents"


def cron_file() -> Path:
    return state_dir() / "cron.json"


def curator_state_file() -> Path:
    return skills_dir() / ".curator_state.json"


# -- the installed package itself ----------------------------------------------

def package_root() -> Path:
    """Directory containing the ``miloctl`` package."""
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    """The milo-portable-system checkout (parent of ``miloctl/``)."""
    return package_root().parent


def bundled(*parts: str) -> Path:
    """Path to a file shipped inside the repo (skills, templates, agents)."""
    return repo_root().joinpath(*parts)


# ── Third-party tool locations ────────────────────────────────────────────────


def opencode_config_dir() -> Path:
    raw = _cfg("OPENCODE_CONFIG_DIR")
    if raw:
        return _expand(raw)
    if IS_WINDOWS:
        appdata = os.environ.get("USERPROFILE") or str(HOME)
        return Path(appdata) / ".config" / "opencode"
    return Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "opencode"


def claude_config_dir() -> Path:
    raw = _cfg("CLAUDE_CONFIG_DIR")
    return _expand(raw) if raw else HOME / ".claude"


def codex_config_dir() -> Path:
    return HOME / ".codex"


def cursor_rules_dir() -> Path:
    return workspace_dir() / ".cursor" / "rules"


# ── Utilities ─────────────────────────────────────────────────────────────────


def ensure(*dirs: Path) -> None:
    for d in dirs:
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def ensure_tree() -> None:
    """Create the full ``$MILO_HOME`` skeleton. Idempotent."""
    ensure(
        milo_home(),
        state_dir(),
        logs_dir(),
        backups_dir(),
        cache_dir(),
        bin_dir(),
        run_dir(),
        repos_dir(),
        skills_dir(),
        agents_dir(),
        memories_dir(),
    )


def portable(path: Path) -> str:
    """Render a path with ``~`` for the home dir — safe to print or commit."""
    try:
        return "~/" + str(Path(path).resolve().relative_to(HOME)).replace("\\", "/")
    except (ValueError, OSError):
        return str(path)


def describe() -> Dict[str, str]:
    """Every resolved location — powers ``milo doctor`` and ``milo where``."""
    return {
        "platform": platform_id(),
        "python": sys.version.split()[0],
        "MILO_HOME": str(milo_home()),
        "env_file": str(env_file()),
        "state": str(state_dir()),
        "logs": str(logs_dir()),
        "backups": str(backups_dir()),
        "repos": str(repos_dir()),
        "skills": str(skills_dir()),
        "agents": str(agents_dir()),
        "memories": str(memories_dir()),
        "vault": str(vault_dir()),
        "engram": str(engram_dir()),
        "workspace": str(workspace_dir()),
        "memory_db": str(memory_db()),
        "sessions_db": str(sessions_db()),
        "opencode_config": str(opencode_config_dir()),
        "claude_config": str(claude_config_dir()),
        "package": str(package_root()),
    }
