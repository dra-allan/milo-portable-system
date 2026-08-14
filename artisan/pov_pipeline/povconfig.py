#!/usr/bin/env python3
"""
povconfig.py - paths, config and the pipeline-level log.
========================================================

Every POV module resolves its filesystem locations through here, so the one
documented Windows default lives in exactly one place and the Linux VPS can
override everything with environment variables.

Environment
-----------
``POV_FACTORY_DIR``  root of all runtime material (default ``Milo Video
                    Factory/pov``).
``POV_PROJECTS_DIR`` project folders (default ``<factory>/projects``)
``POV_DATA_DIR``     sqlite ledger + queue (default ``<factory>/data``)
``POV_STATE_DIR``    pipeline-level state/log (default ``<factory>/state``)
``POV_SECRETS_DIR``  machine-local credentials (default ``<factory>/config``)
``POV_CHANNELS_YAML`` explicit path to the source-curation config

Everything the pipeline *produces* and everything machine-local
(``data/``, ``state/``, secrets) lives under the factory, never inside the
portable repo. The repo holds code, the agent prompts and the config
**templates** only.

Secrets are never read from the YAML directly: any value that still looks
like ``{{PLACEHOLDER}}`` is resolved from the environment variable of the
same name, and treated as absent when that variable is unset.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

# Windows dev default (resolved per-machine, not hardcoded). The VPS
# overrides it with POV_FACTORY_DIR - env-configurable everywhere.
DEFAULT_FACTORY_DIR = Path.home() / "Desktop" / "Milo Video Factory" / "pov"


def eprint(*a: Any, **kw: Any) -> None:
    print(*a, **kw, file=sys.stderr)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


def factory_dir() -> Path:
    """Root of all runtime material. Env-configurable for the Linux VPS."""
    return _env_path("POV_FACTORY_DIR", DEFAULT_FACTORY_DIR)


def projects_dir() -> Path:
    """Where project folders live. Defaults to the factory."""
    return _env_path("POV_PROJECTS_DIR", factory_dir() / "projects")


def data_dir() -> Path:
    """Where the sqlite ledger + queue live. Created on demand."""
    d = _env_path("POV_DATA_DIR", factory_dir() / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def pipeline_state_dir() -> Path:
    """Pipeline-level state (discovery + daemon). Projects have their own."""
    d = _env_path("POV_STATE_DIR", factory_dir() / "state")
    d.mkdir(parents=True, exist_ok=True)
    return d


def secrets_dir() -> Path:
    """Machine-local credentials (OAuth tokens, notify.env, client secrets).

    Distinct from ``config_dir()``: the repo config dir ships templates that
    *are* committed, this dir holds real values that must never be.
    """
    d = _env_path("POV_SECRETS_DIR", factory_dir() / "config")
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_dir() -> Path:
    """Repo config: committed templates only (pov_channels.yaml, *.template)."""
    return HERE / "config"


def channels_yaml() -> Path:
    return _env_path("POV_CHANNELS_YAML", config_dir() / "pov_channels.yaml")


def pipeline_log() -> Path:
    return pipeline_state_dir() / "pipeline.log"


def log_line(event: str, message: str = "", *, level: str = "info",
             echo: bool = True, path: Path | None = None) -> None:
    """Append one line to the pipeline log.

    Same format agent_runner writes, so a project log and the pipeline log
    can be read (or tailed) together. Never raises.
    """
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    line = f"{stamp} [{level}] {event}" + (f" - {message}" if message else "")
    target = path or pipeline_log()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        eprint(f"[log] could not write {target}: {exc}")
    if echo:
        text = f"[{event}] {message}" if message else f"[{event}]"
        (eprint if level == "error" else print)(text)


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"^\{\{([A-Z0-9_]+)\}\}$")


def _strip_comment(text: str) -> str:
    """Remove a trailing ``# comment``, respecting quotes.

    ``- "#shorts"`` must survive; ``min_views: 25000  # note`` must not.
    """
    out: list[str] = []
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or text[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(raw: str) -> Any:
    """Convert a YAML scalar to a Python value."""
    v = raw.strip()
    if not v:
        return ""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("null", "~", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _mini_yaml(text: str) -> dict:
    """Parse the subset of YAML this pipeline's config files use.

    Supports nested mappings by indentation, lists of scalars, quoted and
    bare scalars, ``null``/booleans/numbers, blank lines and comments. That
    is everything ``pov_channels.yaml`` needs, which means the VPS does not
    have to install PyYAML. PyYAML is preferred when it is importable.
    """
    rows: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line)
        if not line.strip():
            continue
        rows.append((len(line) - len(line.lstrip(" ")), line.strip()))

    def parse_block(start: int, indent: int) -> tuple[Any, int]:
        # A block is either a list (every row starts with "- ") or a mapping.
        if start < len(rows) and rows[start][1].startswith("- ") or (
                start < len(rows) and rows[start][1] == "-"):
            items: list[Any] = []
            i = start
            while i < len(rows) and rows[i][0] >= indent:
                depth, body = rows[i]
                if depth > indent:
                    i += 1
                    continue
                if not body.startswith("-"):
                    break
                items.append(_scalar(body[1:].strip()))
                i += 1
            return items, i

        mapping: dict[str, Any] = {}
        i = start
        while i < len(rows):
            depth, body = rows[i]
            if depth < indent:
                break
            if depth > indent:
                i += 1
                continue
            if ":" not in body:
                i += 1
                continue
            key, _, rest = body.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest:
                mapping[key] = _scalar(rest)
                i += 1
                continue
            # Nested block: find the indentation of the next non-empty row.
            if i + 1 < len(rows) and rows[i + 1][0] > depth:
                child, i = parse_block(i + 1, rows[i + 1][0])
                mapping[key] = child
            else:
                mapping[key] = None
                i += 1
        return mapping, i

    if not rows:
        return {}
    parsed, _ = parse_block(0, rows[0][0])
    return parsed if isinstance(parsed, dict) else {}


def read_yaml(path: Path) -> dict:
    """Read a YAML file with PyYAML when available, else the mini parser."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except ImportError:
        return _mini_yaml(text)
    except Exception as exc:  # malformed YAML is a config error, not a crash
        eprint(f"[config] {path.name} could not be parsed by PyYAML ({exc}); "
               "falling back to the built-in reader")
        return _mini_yaml(text)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def resolve_secret(value: Any) -> Any:
    """Turn ``{{NAME}}`` into ``os.environ['NAME']``, or None when unset.

    Config files ship placeholders only. A placeholder whose environment
    variable is missing resolves to None so callers can report "not
    configured" instead of sending the literal string to an API.
    """
    if not isinstance(value, str):
        return value
    m = _PLACEHOLDER_RE.match(value.strip())
    if not m:
        return value
    return os.environ.get(m.group(1), "").strip() or None


def _resolve_tree(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _resolve_tree(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_tree(v) for v in node]
    return resolve_secret(node)


# ---------------------------------------------------------------------------
# The POV config
# ---------------------------------------------------------------------------

INHERITABLE = (
    "language", "min_duration", "max_duration", "min_views",
    "preferred_upload_days", "min_score", "max_videos", "upload_channel",
    "privacy", "published_at",
)


def load_config(path: Path | None = None) -> dict:
    """Load ``pov_channels.yaml`` with defaults merged into every niche.

    Returns a dict with:
        ``defaults``  the raw defaults block (cadence, api, privacy, ...)
        ``niches``    ``{name: niche}`` where each niche has every
                      inheritable key filled in from defaults and the
                      global negative keywords appended to its own
        ``cadence`` / ``api``  convenience copies of the defaults sub-blocks

    A missing or unreadable config returns an empty-but-valid structure, so
    a caller can report "nothing configured" rather than raising.
    """
    cfg_path = path or channels_yaml()
    if not cfg_path.exists():
        eprint(f"[config] not found: {cfg_path}")
        return {"defaults": {}, "niches": {}, "cadence": {}, "api": {},
                "path": str(cfg_path)}

    raw = _resolve_tree(read_yaml(cfg_path))
    defaults = raw.get("defaults") or {}
    global_negatives = [str(k).lower() for k in (raw.get("global_negative_keywords") or [])]
    niches: dict[str, dict] = {}

    for name, niche in (raw.get("niches") or {}).items():
        if not isinstance(niche, dict):
            continue
        merged = dict(niche)
        for key in INHERITABLE:
            if merged.get(key) is None and key in defaults:
                merged[key] = defaults[key]
        merged["name"] = name
        merged["keywords"] = [str(k).lower() for k in (merged.get("keywords") or [])]
        merged["require_keywords"] = [str(k).lower()
                                      for k in (merged.get("require_keywords") or [])]
        # Global negatives apply on top of the niche's own list.
        own_negatives = [str(k).lower() for k in (merged.get("negative_keywords") or [])]
        merged["negative_keywords"] = sorted(set(own_negatives) | set(global_negatives))
        merged["channels"] = [str(c).strip() for c in (merged.get("channels") or []) if str(c).strip()]
        niches[name] = merged

    return {
        "defaults": defaults,
        "niches": niches,
        "cadence": defaults.get("cadence") or {},
        "api": defaults.get("api") or {},
        "global_negative_keywords": global_negatives,
        "path": str(cfg_path),
    }


def youtube_api_key(cfg: dict | None = None) -> str | None:
    """The YouTube Data API key: env first, then the resolved config value."""
    env = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if env:
        return env
    api = (cfg or {}).get("api") or {}
    key = api.get("youtube_api_key")
    return key if isinstance(key, str) and key else None
