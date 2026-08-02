"""
env.py — the one .env file, plus secret hygiene.
================================================

All secrets live in exactly one place: ``$MILO_HOME/.env``. It is never
committed. Every config that needs a secret is a **template** with
``{{PLACEHOLDER}}`` markers, rendered at install time and de-rendered at
backup time — no fragile regex stripping like the old ``backup.cjs``.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import paths

# ── Secret catalogue ──────────────────────────────────────────────────────────

#: (key, label, required, secret)
FIELDS: List[Tuple[str, str, bool, bool]] = [
    # Identity
    ("MILO_DISPLAY_NAME", "Assistant display name (Milo or Mylo)", False, False),
    ("MILO_USER_NAME", "Your name (how Milo addresses you)", False, False),
    # Model provider
    ("MILO_PROVIDER", "Default provider (anthropic|openai|openrouter|nous|local)", False, False),
    ("MILO_MODEL", "Default model id", False, False),
    ("ANTHROPIC_API_KEY", "Anthropic API key", False, True),
    ("OPENAI_API_KEY", "OpenAI API key", False, True),
    ("OPENROUTER_API_KEY", "OpenRouter API key", False, True),
    ("NOUS_API_KEY", "Nous Portal API key", False, True),
    # Channels
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token (@BotFather)", False, True),
    ("TELEGRAM_CHAT_ID", "Your Telegram chat id (@userinfobot)", False, False),
    ("ALLOWED_USER_IDS", "Allowed Telegram user ids (comma separated)", False, False),
    ("DISCORD_BOT_TOKEN", "Discord bot token", False, True),
    ("SLACK_BOT_TOKEN", "Slack bot token", False, True),
    # Storage / sync
    ("GITHUB_PAT", "GitHub personal access token (repo scope)", True, True),
    ("GITHUB_USER", "GitHub username", False, False),
    ("SUPABASE_URL", "Supabase project URL", False, False),
    ("SUPABASE_SERVICE_ROLE_KEY", "Supabase service role key", False, True),
    ("SUPABASE_ANON_KEY", "Supabase anon key", False, True),
    # Tools
    ("STITCH_API_KEY", "Google Stitch API key", False, True),
    ("ENGRAM_API_KEY", "Engram API key", False, True),
    ("BRAVE_API_KEY", "Brave Search API key", False, True),
    ("ELEVENLABS_API_KEY", "ElevenLabs API key", False, True),
    ("GOOGLE_CLIENT_ID", "Google OAuth client id", False, False),
    ("GOOGLE_CLIENT_SECRET", "Google OAuth client secret", False, True),
    ("GOOGLE_REFRESH_TOKEN", "Google OAuth refresh token", False, True),
    # Paths (documented here so `milo setup` can override them)
    ("MILO_VAULT_DIR", "Vault directory (leave blank for platform default)", False, False),
    ("MILO_ENGRAM_DIR", "Engram data directory", False, False),
    ("MILO_WORKSPACE", "Default workspace directory", False, False),
]

REQUIRED = [k for k, _, req, _ in FIELDS if req]
SECRET_KEYS = {k for k, _, _, sec in FIELDS if sec}

#: Anything matching these looks like a credential even if we don't know the key.
_SECRETISH = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIAL|PRIVATE)", re.IGNORECASE
)


def is_secret(key: str) -> bool:
    return key in SECRET_KEYS or bool(_SECRETISH.search(key or ""))


def mask(value: str, keep: int = 4) -> str:
    """``ghp_abc123def456`` → ``ghp_…456`` — safe for logs and prompts."""
    if not value:
        return ""
    value = str(value)
    if len(value) <= keep + 2:
        return "•" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


# ── Read / write ──────────────────────────────────────────────────────────────

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value and value[0] == value[-1] and value[0] in "\"'" and len(value) > 1:
            value = value[1:-1]
        out[key] = value
    return out


def load(path: Optional[Path] = None, include_os: bool = True) -> Dict[str, str]:
    """Read ``.env`` merged over the real process environment."""
    path = path or paths.env_file()
    data: Dict[str, str] = {}
    if path.is_file():
        data = parse(path.read_text(encoding="utf-8", errors="replace"))
    if include_os:
        for key, _, _, _ in FIELDS:
            if os.environ.get(key):
                data[key] = os.environ[key]
    return data


def _quote(value: str) -> str:
    if value == "" or re.search(r"[\s#\"']", value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def save(data: Dict[str, str], path: Optional[Path] = None) -> Path:
    """Write ``.env`` grouped and commented, with 0600 permissions."""
    path = path or paths.env_file()
    paths.ensure(path.parent)

    known = [k for k, _, _, _ in FIELDS]
    extra = sorted(k for k in data if k not in known)

    groups: List[Tuple[str, List[str]]] = [
        ("Identity", ["MILO_DISPLAY_NAME", "MILO_USER_NAME"]),
        ("Model provider", [
            "MILO_PROVIDER", "MILO_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "OPENROUTER_API_KEY", "NOUS_API_KEY",
        ]),
        ("Channels", [
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ALLOWED_USER_IDS",
            "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN",
        ]),
        ("Storage & sync", [
            "GITHUB_PAT", "GITHUB_USER", "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY",
        ]),
        ("Tools", [
            "STITCH_API_KEY", "ENGRAM_API_KEY", "BRAVE_API_KEY", "ELEVENLABS_API_KEY",
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
        ]),
        ("Paths", ["MILO_VAULT_DIR", "MILO_ENGRAM_DIR", "MILO_WORKSPACE"]),
    ]

    lines = [
        "# Milo — environment.",
        "# Generated by `milo setup`. Never commit this file.",
        f"# Location: {path}",
        "",
    ]
    for title, keys in groups:
        present = [k for k in keys if k in data]
        if not present:
            continue
        lines.append(f"# ── {title} " + "─" * max(0, 60 - len(title)))
        for k in present:
            lines.append(f"{k}={_quote(data.get(k, ''))}")
        lines.append("")
    if extra:
        lines.append("# ── Additional " + "─" * 52)
        for k in extra:
            lines.append(f"{k}={_quote(data.get(k, ''))}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass
    return path


def update(changes: Dict[str, str], path: Optional[Path] = None) -> Path:
    data = load(path, include_os=False)
    data.update({k: v for k, v in changes.items() if v is not None})
    return save(data, path)


def get(key: str, default: str = "") -> str:
    """Single-key lookup: process env wins, then ``.env``."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    return load(include_os=False).get(key, default)


def export_to_process(path: Optional[Path] = None) -> int:
    """Push ``.env`` values into ``os.environ`` (does not overwrite existing)."""
    count = 0
    for key, value in load(path, include_os=False).items():
        if value and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


# ── Missing / validation ──────────────────────────────────────────────────────


def missing(required_only: bool = True, path: Optional[Path] = None) -> List[str]:
    data = load(path)
    keys = REQUIRED if required_only else [k for k, _, _, _ in FIELDS]
    return [k for k in keys if not data.get(k)]


def summary(path: Optional[Path] = None) -> List[Tuple[str, str, bool]]:
    """``(key, displayable_value, is_set)`` for every known field."""
    data = load(path)
    rows: List[Tuple[str, str, bool]] = []
    for key, _label, _req, secret in FIELDS:
        raw = data.get(key, "")
        shown = mask(raw) if (secret and raw) else raw
        rows.append((key, shown, bool(raw)))
    return rows


# ── Template rendering (replaces the old regex secret-stripping) ──────────────

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render(template: str, data: Optional[Dict[str, str]] = None,
           extra: Optional[Dict[str, str]] = None) -> str:
    """Substitute ``{{KEY}}`` with values from ``.env`` (+ ``extra``).

    Unknown placeholders are left intact so a partially-configured install
    still produces a valid, obviously-incomplete file.
    """
    values = dict(data if data is not None else load())
    if extra:
        values.update(extra)

    def _sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        val = values.get(key)
        return val if val else m.group(0)

    return _PLACEHOLDER.sub(_sub, template)


def unrender(rendered: str, data: Optional[Dict[str, str]] = None,
             keys: Optional[Iterable[str]] = None) -> str:
    """Inverse of :func:`render` — turn live values back into placeholders.

    Used by ``milo backup`` so a live ``opencode.jsonc`` full of real keys can
    be committed as a template. Longest values are replaced first so a short
    secret that is a substring of a longer one can't corrupt the output.
    """
    values = dict(data if data is not None else load())
    candidates = [
        (k, v) for k, v in values.items()
        if v and len(v) >= 8 and (keys is None or k in set(keys) or is_secret(k))
    ]
    candidates.sort(key=lambda kv: len(kv[1]), reverse=True)
    out = rendered
    for key, value in candidates:
        if value in out:
            out = out.replace(value, "{{" + key + "}}")
    return out


def scrub(text: str, data: Optional[Dict[str, str]] = None) -> str:
    """Redact every known secret value in arbitrary text (logs, diffs)."""
    values = data if data is not None else load()
    out = text
    for key, value in sorted(values.items(), key=lambda kv: len(kv[1]), reverse=True):
        if value and len(value) >= 8 and is_secret(key):
            out = out.replace(value, f"‹{key}›")
    return out


def placeholders(template: str) -> List[str]:
    """Every ``{{KEY}}`` referenced by a template, in order of appearance."""
    seen: List[str] = []
    for m in _PLACEHOLDER.finditer(template):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen
