"""
mcp.py — Milo's brain, exposed as MCP tools.
============================================

This replaces the Engram MCP server. It speaks the Model Context Protocol over
stdio with **no third-party dependencies** — just JSON-RPC 2.0 on stdin/stdout,
which is all MCP stdio transport actually is.

That matters for portability: ``pip install mcp`` is one more thing that can
fail on a fresh Termux install at 2am. This file needs Python 3.8 and nothing
else.

Run it directly::

    python -m miloctl.mcp

Any MCP client (OpenCode, Claude Code, Codex, Cursor) can attach. The tool
names deliberately match the old Engram ones (``mem_save``, ``mem_recall``,
``mem_context``) so existing habits and prompts keep working.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

from . import paths
from .naming import display_name

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "milo-memory"


# ── Tool implementations ──────────────────────────────────────────────────────


def _brain():
    from .memory import store
    return store()


def _t_mem_save(args: Dict[str, Any]) -> str:
    content = str(args.get("content", "")).strip()
    if not content:
        return "Nothing saved: content was empty."
    tags = args.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    mem, created = _brain().save(
        content,
        title=str(args.get("title", ""))[:200],
        category=str(args.get("category", "note")),
        project=str(args.get("project", "milo")),
        tags=tags,
        importance=int(args.get("importance", 3) or 3),
        source="mcp",
        pinned=bool(args.get("pinned", False)),
    )
    verb = "Saved" if created else "Already knew that — refreshed"
    return f"{verb}: [{mem.category}] {mem.summary_line(90)}  (id {mem.id})"


def _t_mem_recall(args: Dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 10) or 10)
    rows = _brain().search(query, limit=limit) if query else _brain().recent(limit)
    if not rows:
        return f"No memories match {query!r}."
    lines = [f"{len(rows)} result(s):"]
    for m in rows:
        pin = "* " if m.pinned else "  "
        lines.append(f"{pin}[{m.category}] {m.content.strip()}")
    return "\n".join(lines)


def _t_mem_context(args: Dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    budget = int(args.get("budget", 12) or 12)
    rows = _brain().context(query, budget=budget)
    if not rows:
        return "No context stored yet."
    out = [f"What {display_name()} knows that's relevant:"]
    for m in rows:
        pin = "* " if m.pinned else "  "
        out.append(f"{pin}[{m.category}] {m.content.strip()}")
    return "\n".join(out)


def _t_mem_forget(args: Dict[str, Any]) -> str:
    mid = str(args.get("id", "")).strip()
    if not mid:
        return "Need an id."
    ok = _brain().forget(mid, hard=bool(args.get("hard", False)))
    return f"{'Archived' if ok else 'No such memory'}: {mid}"


def _t_mem_about(args: Dict[str, Any]) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return "Need a name."
    data = _brain().about(name)
    return json.dumps(data, indent=2, default=str)


def _t_vault_search(args: Dict[str, Any]) -> str:
    from .vault import vault
    v = vault()
    if not v.exists:
        return f"No vault at {v.root}."
    hits = v.search(str(args.get("query", "")), limit=int(args.get("limit", 15) or 15))
    if not hits:
        return "No matches in the vault."
    return "\n".join(h.render() for h in hits)


def _t_vault_note(args: Dict[str, Any]) -> str:
    from .vault import vault
    v = vault()
    if not v.exists:
        return f"No vault at {v.root}."
    text = str(args.get("text", "")).strip()
    if not text:
        return "Nothing to write."
    if args.get("inbox"):
        p = v.capture(text, str(args.get("title", "")))
    else:
        p = v.append_daily(text, heading=str(args.get("heading", "")))
    return f"Written to {p}"


def _t_skills_list(args: Dict[str, Any]) -> str:
    from .skills import registry
    return registry().index() or "No skills yet."


def _t_skill_read(args: Dict[str, Any]) -> str:
    from .skills import registry
    name = str(args.get("name", "")).strip()
    sk = registry().get(name)
    if not sk:
        return f"No skill named {name!r}."
    try:
        return sk.path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Could not read {sk.path}: {exc}"


def _t_sessions_search(args: Dict[str, Any]) -> str:
    from .sessions import store as sess
    rows = sess().search(
        str(args.get("query", "")), limit=int(args.get("limit", 15) or 15)
    )
    if not rows:
        return "Nothing in past sessions matches."
    return "\n".join(f"{r['when']}  {r['excerpt']}" for r in rows)


def _t_profile(args: Dict[str, Any]) -> str:
    from .profile import Profile
    return Profile().prompt_block() or "No user model built yet."


def _t_whoami(args: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "agent": display_name(),
            "aliases": ["milo", "mylo"],
            "home": str(paths.milo_home()),
            "vault": str(paths.vault_dir()),
            "memory_db": str(paths.memory_db()),
        },
        indent=2,
    )


#: name -> (description, json-schema properties, required, handler)
TOOLS: Dict[str, tuple] = {
    "mem_save": (
        "Save something durable to Milo's long-term memory. Use for decisions, "
        "preferences, facts about Allan, and hard-won fixes.",
        {
            "content": {"type": "string", "description": "What to remember"},
            "title": {"type": "string", "description": "Optional short label"},
            "category": {
                "type": "string",
                "description": "fact | decision | preference | procedure | note",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "importance": {"type": "integer", "description": "1-5, default 3"},
            "project": {"type": "string"},
            "pinned": {"type": "boolean", "description": "Always in context"},
        },
        ["content"],
        _t_mem_save,
    ),
    "mem_recall": (
        "Search Milo's memory for anything matching a query.",
        {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        ["query"],
        _t_mem_recall,
    ),
    "mem_context": (
        "Get the highest-signal memories for the current task. Call this at the "
        "start of a session before assuming anything.",
        {"query": {"type": "string"}, "budget": {"type": "integer"}},
        [],
        _t_mem_context,
    ),
    "mem_forget": (
        "Archive a memory by id (recoverable; nothing is really deleted).",
        {"id": {"type": "string"}, "hard": {"type": "boolean"}},
        ["id"],
        _t_mem_forget,
    ),
    "mem_about": (
        "Everything Milo knows about a person, project or thing, plus its "
        "relationships.",
        {"name": {"type": "string"}},
        ["name"],
        _t_mem_about,
    ),
    "vault_search": (
        "Full-text search the Obsidian vault (long-term human-readable notes).",
        {"query": {"type": "string"}, "limit": {"type": "integer"}},
        ["query"],
        _t_vault_search,
    ),
    "vault_note": (
        "Append to today's daily note, or drop a capture in the inbox.",
        {
            "text": {"type": "string"},
            "heading": {"type": "string"},
            "inbox": {"type": "boolean", "description": "Write to inbox instead"},
            "title": {"type": "string"},
        },
        ["text"],
        _t_vault_note,
    ),
    "skills_list": (
        "List every skill Milo knows, with descriptions.",
        {},
        [],
        _t_skills_list,
    ),
    "skill_read": (
        "Read one skill's full SKILL.md before performing it.",
        {"name": {"type": "string"}},
        ["name"],
        _t_skill_read,
    ),
    "sessions_search": (
        "Search transcripts of past Milo sessions across every machine.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}},
        ["query"],
        _t_sessions_search,
    ),
    "user_profile": (
        "Milo's current working model of Allan — preferences, style, context.",
        {},
        [],
        _t_profile,
    ),
    "milo_whoami": (
        "Where Milo's state lives on this machine.",
        {},
        [],
        _t_whoami,
    ),
}


def _tool_schema() -> List[Dict[str, Any]]:
    out = []
    for name, (desc, props, required, _fn) in TOOLS.items():
        out.append(
            {
                "name": name,
                "description": desc,
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            }
        )
    return out


# ── JSON-RPC plumbing ─────────────────────────────────────────────────────────


def _result(rid: Any, payload: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": payload}


def _error(rid: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC request. Returns None for notifications."""
    method = request.get("method", "")
    rid = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return _result(
            rid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": __import__("miloctl").__version__,
                },
            },
        )

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(rid, {})

    if method == "tools/list":
        return _result(rid, {"tools": _tool_schema()})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        entry = TOOLS.get(name)
        if not entry:
            return _error(rid, -32601, f"unknown tool: {name}")
        handler = entry[3]
        try:
            text = handler(args)
        except Exception as exc:  # surface the error to the model, don't die
            text = f"{name} failed: {exc}\n{traceback.format_exc(limit=3)}"
            return _result(
                rid,
                {"content": [{"type": "text", "text": text}], "isError": True},
            )
        return _result(rid, {"content": [{"type": "text", "text": str(text)}]})

    if method in ("resources/list", "prompts/list"):
        key = method.split("/")[0]
        return _result(rid, {key: []})

    if rid is None:
        return None
    return _error(rid, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    paths.ensure_tree()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(request, list):  # batch
            responses = [r for r in (handle(x) for x in request) if r is not None]
            if responses:
                stdout.write(json.dumps(responses) + "\n")
                stdout.flush()
            continue
        response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main() -> int:
    try:
        serve()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
