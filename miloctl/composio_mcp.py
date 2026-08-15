"""composio_mcp.py — resolve a hosted Composio Tool Router MCP endpoint.

Composio v3 exposes an agent's tools over a per-session hosted MCP server
(``session.mcp.url`` + ``session.mcp.headers``). The session id is stable per
user; we cache it under ``$MILO_HOME/state/`` so ``milo sync`` re-emits the
same endpoint instead of minting a new session every run.

Requires the ``composio`` SDK (the v3 package, ``>=0.19``) and a
``COMPOSIO_API_KEY`` in ``.env``. If either is missing, or the network call
fails, :func:`resolve` returns ``None`` and the harness simply skips the
composio server — a sync must never fail because an integration is
half-configured.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import paths

#: Stable per-user id for the Tool Router session backing the MCP endpoint.
_USER_ID = "milo"


def _cache_path() -> Path:
    return paths.milo_home() / "state" / "composio-session.json"


def _load_cached_id() -> Optional[str]:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8")).get("session_id")
    except (OSError, ValueError, TypeError):
        return None


def _save_id(session_id: str) -> None:
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
    except OSError:
        pass


def resolve(api_key: Optional[str] = None) -> Optional[Tuple[str, Dict[str, str]]]:
    """Return ``(url, headers)`` for the Composio MCP session, or ``None``."""
    api_key = (api_key or os.environ.get("COMPOSIO_API_KEY", "")).strip()
    if not api_key:
        return None
    try:
        from composio import Composio
    except ImportError:
        return None
    try:
        client = Composio(api_key=api_key)
    except Exception:
        return None
    session = None
    cached = _load_cached_id()
    if cached:
        try:
            session = client.sessions.use(cached, mcp=True)
        except Exception:
            session = None
    if session is None:
        try:
            session = client.sessions.create(user_id=_USER_ID, mcp=True)
        except Exception:
            return None
    mcp = getattr(session, "mcp", None)
    if mcp is None:
        return None
    url = (getattr(mcp, "url", "") or "").strip()
    headers = dict(getattr(mcp, "headers", {}) or {})
    if not url:
        return None
    sid = url.rstrip("/").split("/")[-2]
    if sid:
        _save_id(sid)
    return url, headers