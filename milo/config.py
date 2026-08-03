"""
milo.config — settings, secrets and template rendering.

Two kinds of state, kept strictly apart:

``.env``      Secrets. Never committed. Lives at ``~/.milo/.env``.
``milo.json`` Non-secret preferences (harness choice, model, feature flags).
              Lives at ``~/.milo/milo.json`` and is safe to back up.

Template rendering
------------------
Config files that must contain secrets at runtime (``opencode.jsonc``,
Claude settings, MCP definitions) are stored in the repo as ``.tmpl`` files
containing ``{{PLACEHOLDER}}`` tokens.

    render()   template + secrets  ->  live config file
    scrub()    live config file    ->  template  (secrets replaced by tokens)

This is a deterministic, reversible pair. It replaces ``backup.cjs``'s
fragile regex soup, which could silently miss a new secret and push it to
GitHub — exactly how the live Google refresh token ended up committed in
``dra-allan/milo``.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .paths import MiloPaths, get_paths, read_env_file

__all__ = [
    "SECRET_KEYS",
    "OPTIONAL_SECRET_KEYS",
    "Settings",
    "load_settings",
    "load_env",
    "save_env",
    "render",
    "scrub",
    "scan_for_secrets",
    "DEFAULT_SETTINGS",
]


# ---------------------------------------------------------------------------
# Which keys are secrets
# ---------------------------------------------------------------------------

#: Secrets Milo genuinely cannot run without (for the features that use them).
SECRET_KEYS: Tuple[str, ...] = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ALLOWED_USER_IDS",
    "GITHUB_PAT",
)

#: Everything else Milo may use. Prompted as optional during install.
OPTIONAL_SECRET_KEYS: Tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "STITCH_API_KEY",
    "ENGRAM_API_KEY",
    "DISCORD_BOT_TOKEN",
    "USER_ID",
)

ALL_SECRET_KEYS: Tuple[str, ...] = SECRET_KEYS + OPTIONAL_SECRET_KEYS

#: Substrings that mark a config key as secret even if we've never seen it.
_SECRETISH = ("TOKEN", "SECRET", "PASSWORD", "APIKEY", "API_KEY", "_KEY", "PAT")

#: Regexes that catch well-known credential shapes in arbitrary text.
#: Used by ``scan_for_secrets`` as a last line of defence before pushing.
_LEAK_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("github-pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("github-fine-grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("telegram-bot-token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("google-oauth-secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b")),
    ("google-refresh-token", re.compile(r"\b1//[A-Za-z0-9_-]{30,}\b")),
    ("jwt", re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def is_secret_key(key: str) -> bool:
    """True if a config key name should be treated as a secret."""
    upper = key.upper()
    if upper in ALL_SECRET_KEYS:
        return True
    return any(marker in upper for marker in _SECRETISH)


# ---------------------------------------------------------------------------
# Non-secret settings
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: Dict[str, Any] = {
    "version": 2,
    "identity": {
        "name": "Milo Sage",
        "short_name": "Milo",
        # Which bundled identity file to use as the persona source of truth.
        "source": "assets/identity/IDENTITY.md",
    },
    "harness": {
        # Which agent harnesses to render config for on `milo sync`.
        "enabled": ["opencode", "claude-code", "generic"],
        "primary": "opencode",
    },
    "memory": {
        # Hermes-style bounded curated memory injected into the system prompt.
        "curated_enabled": True,
        "memory_char_limit": 2200,
        "user_char_limit": 1375,
        # Nudge the agent to persist knowledge every N assistant turns.
        "nudge_interval": 10,
        # Promote hot observations to the vault at task boundaries.
        "auto_promote": True,
        "promote_min_importance": 3,
    },
    "skills": {
        "enabled": True,
        # Nudge the agent to capture a skill after N tool iterations without one.
        "creation_nudge_interval": 12,
        "curator": {
            "enabled": True,
            "interval_hours": 168,  # weekly
            "stale_after_days": 30,
            "archive_after_days": 90,
            "consolidate": False,  # opt-in; costs auxiliary model calls
        },
    },
    "services": {
        "opencode_port": 4096,
        "autostart": ["MiloServe"],
    },
    "backup": {
        "push_on_backup": True,
        "include_engram": True,
        "keep_local_snapshots": 14,
    },
    "repos": {
        "vault": "https://github.com/dra-allan/dra-brains.git",
    },
}


@dataclass
class Settings:
    """Merged view of ``milo.json`` + ``.env`` + resolved paths."""

    paths: MiloPaths
    data: Dict[str, Any] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)

    # -- dotted access ----------------------------------------------------

    def get(self, dotted: str, default: Any = None) -> Any:
        """``settings.get("skills.curator.interval_hours")``"""
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    def secret(self, key: str, default: str = "") -> str:
        """Look up a secret: real environment first, then ``.env``."""
        return os.environ.get(key) or self.env.get(key) or default

    def save(self) -> None:
        self.paths.milo_home.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text(
            json.dumps(self.data, indent=2) + "\n", encoding="utf-8"
        )

    # -- template variables ----------------------------------------------

    def template_vars(self) -> Dict[str, str]:
        """All substitutable values: paths + secrets + a few derived extras."""
        variables: Dict[str, str] = {}
        variables.update(self.paths.as_dict())
        variables.update({k: v for k, v in self.env.items() if v})
        # Real environment wins over the file.
        for key in ALL_SECRET_KEYS:
            if os.environ.get(key):
                variables[key] = os.environ[key]
        variables.setdefault("MILO_NAME", str(self.get("identity.name", "Milo")))
        variables.setdefault(
            "OPENCODE_PORT", str(self.get("services.opencode_port", 4096))
        )
        return variables


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if (
            key in out
            and isinstance(out[key], Mapping)
            and isinstance(value, Mapping)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(paths: Optional[MiloPaths] = None) -> Settings:
    """Load ``milo.json`` (merged over defaults) and ``.env``."""
    paths = paths or get_paths()
    data: Dict[str, Any] = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    if paths.config_file.is_file():
        try:
            user = json.loads(paths.config_file.read_text(encoding="utf-8"))
            if isinstance(user, Mapping):
                data = _deep_merge(data, user)
        except (json.JSONDecodeError, OSError):
            pass
    return Settings(paths=paths, data=data, env=load_env(paths))


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------


def load_env(paths: Optional[MiloPaths] = None) -> Dict[str, str]:
    paths = paths or get_paths()
    return read_env_file(paths.env_file)


def save_env(values: Mapping[str, str], paths: Optional[MiloPaths] = None) -> Path:
    """Write ``.env`` with 0600 permissions, preserving key order and comments."""
    paths = paths or get_paths()
    paths.milo_home.mkdir(parents=True, exist_ok=True)
    target = paths.env_file

    lines: List[str] = [
        "# Milo secrets — generated by `milo install`.",
        "# NEVER commit this file. It is the only thing that cannot be",
        "# reconstructed from git.",
        "",
    ]
    known = [k for k in ALL_SECRET_KEYS if k in values]
    extra = sorted(k for k in values if k not in ALL_SECRET_KEYS)
    for key in [*known, *extra]:
        value = values.get(key, "")
        lines.append(f"{key}={value}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Best-effort lockdown; Windows ignores POSIX modes.
    try:
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - platform dependent
        pass
    return target


# ---------------------------------------------------------------------------
# Template rendering / scrubbing
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")


def render(
    template: str,
    variables: Mapping[str, str],
    *,
    keep_unknown: bool = True,
) -> str:
    """Replace ``{{TOKEN}}`` with values.

    Unknown tokens are left intact by default so a partially-configured
    machine still produces a valid, obviously-incomplete file rather than
    one with empty strings that look configured.
    """

    def substitute(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key in variables and variables[key] != "":
            return _json_safe(variables[key])
        return match.group(0) if keep_unknown else ""

    return _TOKEN.sub(substitute, template)


def _json_safe(value: str) -> str:
    """Escape backslashes so Windows paths survive being dropped into JSON."""
    return value.replace("\\", "\\\\") if "\\" in value else value


def scrub(
    content: str,
    variables: Mapping[str, str],
    *,
    only_secrets: bool = False,
) -> str:
    """Inverse of :func:`render` — replace live values with ``{{TOKEN}}``.

    Longest values are replaced first so that a short value which happens to
    be a substring of a longer one cannot corrupt the result.
    """
    items = [
        (key, value)
        for key, value in variables.items()
        if value and len(str(value)) >= 4 and (not only_secrets or is_secret_key(key))
    ]
    items.sort(key=lambda kv: len(str(kv[1])), reverse=True)

    out = content
    for key, value in items:
        value = str(value)
        token = "{{" + key + "}}"
        out = out.replace(value, token)
        # Windows paths appear JSON-escaped inside config files.
        if "\\" in value:
            out = out.replace(value.replace("\\", "\\\\"), token)
        # ...and forward-slashed in some tools.
        if "\\" in value:
            out = out.replace(value.replace("\\", "/"), token)
    return out


def scan_for_secrets(
    content: str, *, extra_values: Iterable[str] = ()
) -> List[Tuple[str, str]]:
    """Return ``[(kind, redacted_sample), ...]`` for anything credential-shaped.

    This is the safety net that runs before ``milo backup --push``. It is
    pattern-based, so it also catches secrets we were never told about.
    """
    findings: List[Tuple[str, str]] = []
    for kind, pattern in _LEAK_PATTERNS:
        for match in pattern.findall(content):
            text = match if isinstance(match, str) else match[0]
            findings.append((kind, _redact(text)))
    for value in extra_values:
        value = str(value or "")
        if len(value) >= 8 and value in content:
            findings.append(("known-secret-value", _redact(value)))
    # De-duplicate while keeping order.
    seen = set()
    unique: List[Tuple[str, str]] = []
    for item in findings:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _redact(value: str) -> str:
    if len(value) <= 10:
        return value[:2] + "***"
    return f"{value[:6]}...{value[-4:]} ({len(value)} chars)"
