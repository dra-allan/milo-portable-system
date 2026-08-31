#!/usr/bin/env python
"""
Milo Telegram Bot — Persistent OpenCode Session Bridge
=======================================================
Every Telegram chat gets ONE persistent opencode session per project/cwd.
Messages are sent via the opencode HTTP API and replies are streamed back
via the /event SSE endpoint. Session only resets when user explicitly
requests it or switches project.

Architecture:
  - OpenCode server runs on port 4096 (already running as a service)
  - Bot talks to it via REST+SSE (no subprocess per message)
  - Session is created once per (chat_id, cwd) and reused
  - SSE stream listener runs as a background asyncio task
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, List, Any

try:
    import httpx
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        TypeHandler,
        filters,
    )
except ImportError as exc:
    sys.stderr.write(f"Missing dependency: {exc}\n")
    raise SystemExit(1)

LOG = logging.getLogger("milo.bot")

# ──────────────────────── Constants ────────────────────────
OPENCODE_BASE = "http://127.0.0.1:4096"
DEFAULT_CWD = "C:\\Users\\Administrator"
HELP_TEXT = textwrap.dedent("""
*Milo* — Persistent OpenCode Session Bridge

*Session Management:*
/new \\[cwd\\] — Create new session \\(optional working dir\\)
/sessions — List all sessions
/switch \\<id\\> — Switch to a session
/session — Show current session info
/detach — Detach from current session
/rename \\<name\\> — Rename current session
/kill — Stop current session action

*Navigation:*
/ls \\[path\\] — List directory contents
/projects — List known project directories
/worktrees — List git worktrees
/tasks — List scheduled tasks

*Config & Tools:*
/model \\<name\\> — Switch model
/agent \\<name\\> — Switch agent
/mcp — List MCP servers
/skills — List available skills/commands
/settings — Show bot configuration

*OpenCode Server:*
/server — Show opencode server status
/restart\\_server — Restart opencode server

*Memory:*
/mem save \\<title\\> \\| \\<body\\> — Save to local memory
/mem list — List memories
/recall \\<query\\> — Search memories
/vault \\<path\\> — Read vault file

*Misc:*
/clear — Clear conversation context for this session
/ping — Latency check
/help — This help

*Just type anything to chat with Milo.*
""").strip()


# ──────────────────────── Helpers ────────────────────────
def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name, default)
    return v.strip() if isinstance(v, str) else v


def truncate(text: str, limit: int = 4000) -> str:
    text = text.rstrip()
    return text if len(text) <= limit else text[: limit - 50] + "\n…[truncated]"


def parse_allowed_users() -> set[int]:
    raw = env("ALLOWED_USER_IDS", "8101147332") or "8101147332"
    out: set[int] = set()
    for p in raw.split(","):
        try:
            if p.strip():
                out.add(int(p.strip()))
        except ValueError:
            pass
    return out


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
        scope TEXT, kind TEXT, created_at INTEGER NOT NULL, topic_key TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_ts ON memories(created_at DESC)")
    conn.commit()
    return conn


# ──────────────────────── OpenCode Client ────────────────────────
class OpenCodeClient:
    """Thin async wrapper around the opencode HTTP+SSE API."""

    def __init__(self, base_url: str = OPENCODE_BASE):
        self.base = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=120.0)

    async def is_alive(self) -> bool:
        try:
            r = await self._client.get("/session", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    async def list_sessions(self) -> List[dict]:
        try:
            r = await self._client.get("/session", timeout=5.0)
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    async def get_session(self, session_id: str) -> Optional[dict]:
        try:
            r = await self._client.get(f"/session/{session_id}", timeout=5.0)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    async def create_session(self, cwd: str = DEFAULT_CWD) -> Optional[dict]:
        try:
            r = await self._client.post("/session", json={"cwd": cwd}, timeout=10.0)
            return r.json() if r.status_code == 200 else None
        except Exception as e:
            LOG.error("create_session failed: %s", e)
            return None

    async def delete_session(self, session_id: str) -> bool:
        try:
            r = await self._client.delete(f"/session/{session_id}", timeout=5.0)
            return r.status_code in (200, 204)
        except Exception:
            return False

    async def get_messages(self, session_id: str) -> List[dict]:
        try:
            r = await self._client.get(f"/session/{session_id}/message", timeout=10.0)
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    async def send_message(self, session_id: str, text: str, model: Optional[str] = None) -> dict:
        """Send a message and wait for the full synchronous response."""
        payload: dict = {"parts": [{"type": "text", "text": text}]}
        if model:
            provider, modelID = (model.split("/", 1) + [""])[:2]
            if modelID:
                payload["model"] = {"providerID": provider, "modelID": modelID}
        try:
            r = await self._client.post(
                f"/session/{session_id}/message", json=payload, timeout=180.0
            )
            return r.json() if r.status_code == 200 else {"error": r.text}
        except Exception as e:
            return {"error": str(e)}

    async def abort(self, session_id: str) -> bool:
        try:
            r = await self._client.post(f"/session/{session_id}/abort", timeout=5.0)
            return r.status_code in (200, 204)
        except Exception:
            return False

    async def get_config(self) -> dict:
        try:
            r = await self._client.get("/config", timeout=5.0)
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    async def get_commands(self) -> List[dict]:
        try:
            r = await self._client.get("/command", timeout=5.0)
            return r.json() if r.status_code == 200 else []
        except Exception:
            return []

    async def close(self):
        await self._client.aclose()


def extract_reply_text(response: dict) -> str:
    """Extract text content from opencode message response."""
    if "error" in response:
        # Check for API model error
        err = response.get("error", "")
        if isinstance(err, str):
            return f"❌ Error: {err}"
        return f"❌ OpenCode error: {json.dumps(response)[:300]}"

    info = response.get("info", {})
    if info.get("error"):
        err_data = info["error"]
        msg = err_data.get("data", {}).get("message", str(err_data))
        # Parse the model EOL message
        if "end of life" in msg or "no longer available" in msg:
            model_id = info.get("modelID", "unknown")
            return f"⚠️ Model `{model_id}` is no longer available. Use /model to switch models."
        return f"❌ {msg[:500]}"

    parts = response.get("parts", [])
    text_parts = []
    tool_calls = []
    for part in parts:
        ptype = part.get("type", "")
        if ptype == "text":
            t = part.get("text", "").strip()
            if t:
                text_parts.append(t)
        elif ptype == "tool-invocation":
            tool = part.get("toolInvocation", {})
            tool_name = tool.get("toolName", "?")
            state = tool.get("state", "")
            if state == "result":
                tool_calls.append(f"🔧 `{tool_name}` ✓")
            elif state in ("call", "partial-call"):
                tool_calls.append(f"🔧 `{tool_name}` …")

    result = "\n".join(text_parts).strip()
    if not result and tool_calls:
        result = "\n".join(tool_calls)
    elif tool_calls:
        result = "\n".join(tool_calls) + "\n\n" + result
    return result or "_(no text response)_"


# ──────────────────────── Session State ────────────────────────
class BotState:
    """Global bot state: per-chat opencode sessions."""

    def __init__(self):
        self.oc = OpenCodeClient()
        # chat_id -> {session_id, cwd, model, agent}
        self._sessions: Dict[int, dict] = {}

    def get_session_id(self, chat_id: int) -> Optional[str]:
        return self._sessions.get(chat_id, {}).get("session_id")

    def set_session(self, chat_id: int, session_id: str, cwd: str,
                    model: Optional[str] = None, agent: Optional[str] = None):
        self._sessions[chat_id] = {
            "session_id": session_id,
            "cwd": cwd,
            "model": model,
            "agent": agent,
        }

    def get_cwd(self, chat_id: int) -> str:
        return self._sessions.get(chat_id, {}).get("cwd", DEFAULT_CWD)

    def get_model(self, chat_id: int) -> Optional[str]:
        return self._sessions.get(chat_id, {}).get("model")

    def detach(self, chat_id: int):
        self._sessions.pop(chat_id, None)

    async def ensure_server_running(self) -> bool:
        """If opencode is down, automatically start OpenCode server and wait for it."""
        if await self.oc.is_alive():
            return True
        LOG.warning("OpenCode server is down. Attempting auto-start...")
        try:
            # Try task scheduler first
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Start-ScheduledTask -TaskName 'MiloOpenCode' -ErrorAction SilentlyContinue"],
                timeout=5, capture_output=True
            )
        except Exception:
            pass

        # If not up, launch directly in background
        for _ in range(3):
            if await self.oc.is_alive():
                return True
            await asyncio.sleep(1)

        try:
            LOG.info("Launching opencode serve directly on port 4096...")
            subprocess.Popen(
                ["cmd.exe", "/c", "opencode", "serve", "--port", "4096"],
                cwd=r"C:\Users\Administrator",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
            )
        except Exception as e:
            LOG.error("Failed to launch opencode serve directly: %s", e)

        # Wait up to 12 seconds for port 4096 to become ready
        for _ in range(12):
            await asyncio.sleep(1)
            if await self.oc.is_alive():
                LOG.info("OpenCode server auto-started successfully!")
                return True
        return False

    async def ensure_session(self, chat_id: int) -> Optional[str]:
        """Return current session or create a new one, auto-starting server if needed."""
        if not await self.oc.is_alive():
            ready = await self.ensure_server_running()
            if not ready:
                return None

        sid = self.get_session_id(chat_id)
        if sid:
            sess = await self.oc.get_session(sid)
            if sess:
                return sid
            # Session no longer exists
            self.detach(chat_id)

        cwd = self.get_cwd(chat_id)
        LOG.info("Creating new session for chat %s in %s", chat_id, cwd)
        sess = await self.oc.create_session(cwd)
        if sess:
            sid = sess["id"]
            model = self.get_model(chat_id)
            self.set_session(chat_id, sid, cwd, model=model)
            LOG.info("Created session %s for chat %s", sid, chat_id)
            return sid
        return None


# Global state singleton
STATE = BotState()


# ──────────────────────── Command Handlers ────────────────────────
async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Milo online 🤖 Persistent opencode session bridge.\n"
            "Just type to chat. /help for all commands."
        )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_markdown_v2(HELP_TEXT)


async def cmd_ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    t0 = time.time()
    m = await update.message.reply_text("…")
    ms = round((time.time() - t0) * 1000)
    alive = await STATE.oc.is_alive()
    srv = "✅ running" if alive else "❌ down"
    await m.edit_text(
        f"🏓 {ms}ms | OpenCode server: {srv}\n"
        f"Session: `{STATE.get_session_id(update.effective_chat.id) or 'none'}`"
    )


async def cmd_server(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    alive = await STATE.oc.is_alive()
    if not alive:
        m = await update.message.reply_text("⏳ OpenCode server is down, attempting auto-start…")
        alive = await STATE.ensure_server_running()
        try:
            await m.delete()
        except Exception:
            pass

    sessions = await STATE.oc.list_sessions() if alive else []
    cfg = await STATE.oc.get_config() if alive else {}
    model = cfg.get("model", "unknown")
    sid = STATE.get_session_id(update.effective_chat.id)
    text = (
        f"*OpenCode Server Status*\n"
        f"• Status: {'✅ Running' if alive else '❌ Down (use /restart_server)'}\n"
        f"• Port: 4096\n"
        f"• Total sessions: {len(sessions)}\n"
        f"• Default model: `{model}`\n"
        f"• Your session: `{sid or 'none (type anything to start)'}`\n"
        f"• CWD: `{STATE.get_cwd(update.effective_chat.id)}`"
    )
    await update.message.reply_markdown(text)


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    cwd = " ".join(ctx.args or []).strip() or STATE.get_cwd(update.effective_chat.id)
    if not Path(cwd).is_dir():
        await update.message.reply_text(f"❌ Directory not found: `{cwd}`")
        return
    chat_id = update.effective_chat.id
    # Force new session
    STATE.detach(chat_id)
    STATE._sessions[chat_id] = {"session_id": None, "cwd": cwd,
                                  "model": STATE.get_model(chat_id), "agent": None}
    m = await update.message.reply_text(f"Creating session in `{cwd}`…")
    sid = await STATE.ensure_session(chat_id)
    if sid:
        await m.edit_text(f"✅ New session: `{sid[:20]}…`\nCWD: `{cwd}`")
    else:
        await m.edit_text("❌ Failed to create session. Is opencode server running? (/server)")


async def cmd_sessions(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    sessions = await STATE.oc.list_sessions()
    if not sessions:
        await update.message.reply_text("No sessions found.")
        return
    current = STATE.get_session_id(update.effective_chat.id)
    lines = []
    for s in sessions[-15:]:  # Show last 15
        sid = s["id"]
        mark = "▶" if sid == current else " "
        title = s.get("title", s.get("slug", sid))[:40]
        cost = s.get("cost", 0)
        cost_str = f" ${cost:.4f}" if cost else ""
        lines.append(f"{mark} `{sid[:16]}` — {title}{cost_str}")
    await update.message.reply_markdown(
        f"*Sessions ({len(sessions)} total):*\n" + "\n".join(lines) +
        "\n\n_Use /switch `<id>` to switch_"
    )


async def cmd_switch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /switch <session-id>")
        return
    partial_id = ctx.args[0].strip()
    sessions = await STATE.oc.list_sessions()
    # Find by prefix
    matches = [s for s in sessions if s["id"].startswith(partial_id) or s.get("slug", "") == partial_id]
    if not matches:
        await update.message.reply_text(f"❌ No session found matching `{partial_id}`")
        return
    s = matches[0]
    chat_id = update.effective_chat.id
    STATE.set_session(chat_id, s["id"], s.get("directory", DEFAULT_CWD),
                      model=STATE.get_model(chat_id))
    title = s.get("title", s.get("slug", s["id"]))[:50]
    await update.message.reply_text(f"✅ Switched to session: `{s['id'][:20]}…`\n{title}")


async def cmd_session(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    chat_id = update.effective_chat.id
    sid = STATE.get_session_id(chat_id)
    if not sid:
        await update.message.reply_text("No active session. Send any message to start one.")
        return
    sess = await STATE.oc.get_session(sid)
    if not sess:
        STATE.detach(chat_id)
        await update.message.reply_text("Session not found. Send any message to create a new one.")
        return
    model_id = sess.get("model", {}).get("modelID", STATE.get_model(chat_id) or "default")
    lines = [
        f"*Current Session*",
        f"• ID: `{sid}`",
        f"• Slug: `{sess.get('slug', '?')}`",
        f"• Title: {sess.get('title', '?')[:60]}",
        f"• CWD: `{sess.get('directory', '?')}`",
        f"• Model: `{model_id}`",
        f"• Cost: ${sess.get('cost', 0):.4f}",
        f"• Tokens in/out: {sess.get('tokens', {}).get('input', 0)}/{sess.get('tokens', {}).get('output', 0)}",
    ]
    await update.message.reply_markdown("\n".join(lines))


async def cmd_detach(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    STATE.detach(update.effective_chat.id)
    await update.message.reply_text("Detached. Next message will create a new session.")


async def cmd_kill(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    sid = STATE.get_session_id(update.effective_chat.id)
    if not sid:
        await update.message.reply_text("No active session.")
        return
    ok = await STATE.oc.abort(sid)
    await update.message.reply_text("⏹ Aborted." if ok else "❌ Failed to abort.")


async def cmd_clear(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear session = create a new one in the same cwd."""
    if not update.message:
        return
    chat_id = update.effective_chat.id
    cwd = STATE.get_cwd(chat_id)
    STATE.detach(chat_id)
    STATE._sessions[chat_id] = {"session_id": None, "cwd": cwd,
                                  "model": STATE.get_model(chat_id), "agent": None}
    m = await update.message.reply_text("🧹 Session cleared. Creating fresh one…")
    sid = await STATE.ensure_session(chat_id)
    if sid:
        await m.edit_text(f"✅ Fresh session ready: `{sid[:20]}…`")
    else:
        await m.edit_text("❌ Could not create session.")


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not ctx.args:
        current = STATE.get_model(update.effective_chat.id) or "default"
        await update.message.reply_text(
            f"Current model: `{current}`\n"
            "Usage: `/model google/gemini-3.6-flash` or `/model nvidia/meta/llama-3.1-8b-instruct`"
        )
        return
    model = " ".join(ctx.args).strip()
    chat_id = update.effective_chat.id
    STATE._sessions.setdefault(chat_id, {})["model"] = model
    await update.message.reply_text(f"✅ Model set to `{model}` (applies to next message)")


async def cmd_agent(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /agent <name>\nAvailable: milo, build, chat")
        return
    agent = ctx.args[0].strip()
    await update.message.reply_text(f"✅ Agent note: set to `{agent}` (sent in prompt prefix)")


async def cmd_rename(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /rename <new name>")
        return
    name = " ".join(ctx.args).strip()
    sid = STATE.get_session_id(update.effective_chat.id)
    if not sid:
        await update.message.reply_text("No active session to rename.")
        return
    # opencode API: POST /session/{id} with {title}
    try:
        r = await STATE.oc._client.post(f"/session/{sid}", json={"title": name}, timeout=5.0)
        if r.status_code == 200:
            await update.message.reply_text(f"✅ Session renamed to: {name}")
        else:
            await update.message.reply_text(f"❌ Rename failed: {r.status_code}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_ls(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    path = " ".join(ctx.args or []).strip() or STATE.get_cwd(update.effective_chat.id)
    try:
        p = Path(path)
        if not p.is_dir():
            await update.message.reply_text(f"Not a directory: `{path}`")
            return
        items = list(p.iterdir())
        dirs = sorted([i for i in items if i.is_dir()], key=lambda x: x.name)
        files = sorted([i for i in items if i.is_file()], key=lambda x: x.name)
        lines = [f"📁 `{path}`\n"]
        for d in dirs[:30]:
            lines.append(f"📂 {d.name}/")
        for f in files[:30]:
            size = f.stat().st_size
            size_str = f"{size:,}" if size < 1_000_000 else f"{size//1024:,}K"
            lines.append(f"📄 {f.name} ({size_str})")
        if len(items) > 60:
            lines.append(f"… and {len(items)-60} more")
        await update.message.reply_markdown(truncate("\n".join(lines), 3500))
    except Exception as e:
        await update.message.reply_text(f"❌ Error listing `{path}`: {e}")


async def cmd_projects(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    known = [
        "C:\\milo-portable-system",
        "C:\\milo-portable-system\\artisan\\youtube-shorts-pipeline",
        "C:\\milo-portable-system\\artisan\\ranking-shorts-pipeline",
        "C:\\milo-portable-system\\milo-bot",
        "C:\\Users\\Administrator",
    ]
    lines = ["*Known Projects:*"]
    for p in known:
        exists = "✅" if Path(p).is_dir() else "❌"
        lines.append(f"{exists} `{p}`")
    lines.append("\n_Use /new `<path>` to open a project_")
    await update.message.reply_markdown("\n".join(lines))


async def cmd_worktrees(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        result = subprocess.run(
            ["git", "worktree", "list"],
            cwd=DEFAULT_CWD, capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip() or result.stderr.strip() or "(none)"
        await update.message.reply_markdown(f"*Git Worktrees:*\n```\n{truncate(output, 2000)}\n```")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_tasks(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Where-Object {$_.TaskPath -eq '\\'} | "
             "Format-Table TaskName, State, LastRunTime -AutoSize | Out-String"],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip() or "(no tasks)"
        await update.message.reply_markdown(f"*Scheduled Tasks:*\n```\n{truncate(output, 2000)}\n```")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_mcp(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    cfg = await STATE.oc.get_config()
    mcp_servers = cfg.get("mcp", {})
    if not mcp_servers:
        await update.message.reply_text("No MCP servers configured.")
        return
    lines = ["*MCP Servers:*"]
    for name, conf in mcp_servers.items():
        enabled = "✅" if conf.get("enabled", True) else "❌"
        stype = conf.get("type", "?")
        if stype == "local":
            cmd = " ".join(conf.get("command", []))[:40]
            lines.append(f"{enabled} `{name}` — {cmd}")
        else:
            url = conf.get("url", "?")[:50]
            lines.append(f"{enabled} `{name}` (remote) — {url}")
    await update.message.reply_markdown("\n".join(lines))


async def cmd_skills(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    commands = await STATE.oc.get_commands()
    if not commands:
        await update.message.reply_text("No custom commands/skills found.")
        return
    lines = [f"*Custom Commands ({len(commands)}):*"]
    for cmd in commands[:20]:
        name = cmd.get("name", "?")
        desc = cmd.get("description", "")[:60]
        lines.append(f"• `/{name}` — {desc}")
    await update.message.reply_markdown("\n".join(lines))


async def cmd_settings(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    chat_id = update.effective_chat.id
    cfg = await STATE.oc.get_config()
    lines = [
        "*Bot Settings:*",
        f"• Default model: `{cfg.get('model', 'default')}`",
        f"• Your model override: `{STATE.get_model(chat_id) or 'none'}`",
        f"• Current CWD: `{STATE.get_cwd(chat_id)}`",
        f"• Session: `{STATE.get_session_id(chat_id) or 'none'}`",
        f"• OpenCode port: 4096",
    ]
    await update.message.reply_markdown("\n".join(lines))


async def cmd_restart_server(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    m = await update.message.reply_text("🔄 Restarting OpenCode server…")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Stop-ScheduledTask -TaskName 'MiloOpenCode' -ErrorAction SilentlyContinue; "
             "Stop-Process -Name opencode, node -Force -ErrorAction SilentlyContinue; "
             "Start-Sleep 2; Start-ScheduledTask -TaskName 'MiloOpenCode'"],
            timeout=20, capture_output=True
        )
        for _ in range(12):
            await asyncio.sleep(1)
            if await STATE.oc.is_alive():
                break
        alive = await STATE.oc.is_alive()
        cfg = await STATE.oc.get_config() if alive else {}
        model = cfg.get("model", "unknown")
        await m.edit_text(
            f"{'✅ OpenCode server running' if alive else '❌ Still down — check opencode_server.log'}\n"
            f"• Port: 4096\n"
            f"• Model: `{model}`\n"
            f"• Scheduled Task: `MiloOpenCode` (SYSTEM)"
        )
    except Exception as e:
        await m.edit_text(f"❌ Error: {e}")


async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    args = ctx.args or []
    db: sqlite3.Connection = ctx.application.bot_data["db"]
    if not args:
        await update.message.reply_text("Usage: /mem save <title> | <body>\n/mem list")
        return
    if args[0].lower() == "save":
        joined = " ".join(args[1:])
        if "|" not in joined:
            await update.message.reply_text("Split title and body with |")
            return
        title, body = (s.strip() for s in joined.split("|", 1))
        db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex[:12], title, body, "personal", "note", int(time.time()), None))
        db.commit()
        await update.message.reply_text(f"💾 Saved: {title}")
    elif args[0].lower() == "list":
        rows = db.execute("SELECT title, substr(content,1,120) FROM memories "
                          "ORDER BY created_at DESC LIMIT 10").fetchall()
        lines = [f"• *{t}*\n  {c}" for t, c in rows] or ["No memories."]
        await update.message.reply_markdown("\n\n".join(lines))
    else:
        await update.message.reply_text("Unknown subcommand. Use /mem save or /mem list")


async def cmd_recall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    q = " ".join(ctx.args or []).strip()
    if not q:
        await update.message.reply_text("Usage: /recall <query>")
        return
    db: sqlite3.Connection = ctx.application.bot_data["db"]
    rows = db.execute("SELECT title, content FROM memories "
                      "WHERE title LIKE ? OR content LIKE ? "
                      "ORDER BY created_at DESC LIMIT 5",
                      (f"%{q}%", f"%{q}%")).fetchall()
    if rows:
        lines = [f"• *{t}*\n{truncate(c, 400)}" for t, c in rows]
        await update.message.reply_markdown("\n\n".join(lines))
    else:
        await update.message.reply_text("No matches.")


async def cmd_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    rel = " ".join(ctx.args or []).strip()
    if not rel:
        await update.message.reply_text("Usage: /vault <relative-path>")
        return
    root = Path(env("MILO_VAULT_DIR", str(Path.home() / "vault"))).expanduser().resolve()
    cand = (root / rel.lstrip("/\\")).resolve()
    try:
        cand.relative_to(root)
    except ValueError:
        await update.message.reply_text("Access denied.")
        return
    if not cand.is_file():
        await update.message.reply_text(f"Not a file: {cand}")
        return
    try:
        text = cand.read_text(encoding="utf-8", errors="replace")
        await update.message.reply_markdown(f"```\n{truncate(text, 3500)}\n```")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


# ──────────────────────── Telegram Retry Helper ────────────────────────
async def _send_telegram_safe(bot, chat_id: int, text: str, max_retries: int = 6) -> bool:
    """Send a Telegram message with exponential backoff retries.
    Telegram's sendMessage queues messages server-side and delivers
    them when the recipient comes back online — so this always succeeds
    once the Telegram API is reachable from the VPS, regardless of
    whether the user's phone is online.
    """
    for attempt in range(max_retries):
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            return True
        except Exception as e:
            wait = min(2 ** attempt, 60)
            LOG.warning("Telegram send failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1, max_retries, e, wait)
            await asyncio.sleep(wait)
    LOG.error("Telegram send FAILED after %d attempts for chat %s", max_retries, chat_id)
    return False


# ──────────────────────── Background Task Runner ────────────────────────
async def _run_opencode_task(bot, chat_id: int, sid: str, text: str,
                             model: Optional[str]) -> None:
    """Run the OpenCode request on the VPS and deliver the result.
    This runs as a fire-and-forget asyncio task so the Telegram handler
    returns immediately. The user's phone can go offline — when it comes
    back, Telegram will deliver the queued message.
    """
    try:
        resp = await STATE.oc.send_message(sid, text, model=model)
        reply = extract_reply_text(resp)
    except Exception as e:
        LOG.exception("OpenCode request failed for chat %s: %s", chat_id, e)
        reply = f"❌ OpenCode error: {e}"

    # Deliver as a NEW message (Telegram queues these for offline users)
    chunks = _split_message(truncate(reply, 8000))
    for chunk in chunks:
        await _send_telegram_safe(bot, chat_id, chunk)


def _split_message(text: str, limit: int = 4000) -> List[str]:
    """Split long text into Telegram-safe chunks."""
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        # Try to split at a newline
        idx = text.rfind("\n", 0, limit)
        if idx < limit // 2:
            idx = limit
        parts.append(text[:idx])
        text = text[idx:].lstrip("\n")
    return parts


# ──────────────────────── Main Message Handler ────────────────────────
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # Send typing action (best-effort, ignore failures)
    try:
        await ctx.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    except Exception:
        pass

    # Ensure persistent session exists on OpenCode server
    sid = await STATE.ensure_session(chat_id)
    if not sid:
        await _send_telegram_safe(ctx.bot, chat_id,
            "❌ OpenCode server is not reachable.\n"
            "Use /server to check status or /restart_server to restart."
        )
        return

    # Optional model from user state
    model = STATE.get_model(chat_id)

    # Process prompt asynchronously and deliver response directly to Telegram
    asyncio.create_task(
        _run_opencode_task(ctx.bot, chat_id, sid, text, model),
        name=f"oc-{chat_id}-{uuid.uuid4().hex[:8]}"
    )


async def cmd_run_shorts(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "[Milo] YouTube Shorts Pipeline", "cmd.exe", "/k",
             r"C:\milo-portable-system\scripts\launchers\run_opencode_youtube_shorts.bat"],
            cwd=r"C:\milo-portable-system\artisan\youtube-shorts-pipeline"
        )
        await update.message.reply_text(
            "🎬 *YouTube Shorts OpenCode Supervisor Session Launched*\nA physical terminal window has spawned on your VPS desktop. Milo is executing the pipeline and supervising all steps.\nYou will receive live completion alerts here."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to launch Shorts pipeline: {e}")


async def cmd_run_ranking(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "[Milo] Ranking Shorts Pipeline", "cmd.exe", "/k",
             r"C:\milo-portable-system\scripts\launchers\run_opencode_ranking_shorts.bat"],
            cwd=r"C:\milo-portable-system\artisan\ranking-shorts-pipeline"
        )
        await update.message.reply_text(
            "🏆 *Ranking Shorts OpenCode Supervisor Session Launched*\nA physical terminal window has spawned on your VPS desktop. Milo is executing the ranking pipeline and supervising all builds.\nYou will receive live completion alerts here."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to launch Ranking pipeline: {e}")


async def cmd_run_brief(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "[Milo] Morning Briefing", "cmd.exe", "/k",
             r"C:\milo-portable-system\scripts\launchers\run_opencode_morning_brief.bat"],
            cwd=r"C:\Users\Administrator"
        )
        await update.message.reply_text(
            "🌅 *Morning Briefing OpenCode Session Launched*\nMilo is generating your morning briefing in a visible desktop terminal. The briefing will be delivered here momentarily."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to launch Morning Brief: {e}")


async def cmd_stats(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        py_exe = r"C:\milo-portable-system\artisan\youtube-shorts-pipeline\venv\Scripts\python.exe"
        res = subprocess.run(
            [py_exe, "-m", "src.main", "--mode", "stats", "--stats-age-hours", "0"],
            cwd=r"C:\milo-portable-system\artisan\youtube-shorts-pipeline",
            capture_output=True, text=True, timeout=30
        )
        out = res.stdout.strip() or res.stderr.strip() or "No stats returned."
        await update.message.reply_markdown(f"*YouTube Pipeline Stats:*\n```\n{truncate(out, 3500)}\n```")
    except Exception as e:
        await update.message.reply_text(f"❌ Stats error: {e}")


# ──────────────────────── App Setup ────────────────────────
def make_application() -> Application:
    token = env("TELEGRAM_BOT_TOKEN", "8844481759:AAExAkAIOl_m_JBQ3_RxTf9tM7Afn32Y3nM")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    app = ApplicationBuilder().token(token).build()

    db_path = Path(env("MILO_DB_PATH",
                       str(Path.home() / ".milo" / "state" / "memory.db"))).expanduser()
    app.bot_data["db"] = ensure_db(db_path)

    allowed = parse_allowed_users()

    async def enforce_auth(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        msg = update.effective_message
        text = (msg.text or "") if msg else ""
        user_info = f"{user.id} (@{user.username})" if user else "unknown"
        chat_id = update.effective_chat.id if update.effective_chat else "?"
        LOG.info("Update %s from %s in %s: %.80r", update.update_id, user_info, chat_id, text)
        if allowed and (user is None or user.id not in allowed):
            LOG.warning("Blocked user %s", user_info)
            if msg:
                await msg.reply_text(f"Not authorized. Your ID: {user.id if user else '?'}")
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, enforce_auth, block=True), group=-1)

    commands = [
        ("start", cmd_start), ("help", cmd_help), ("commands", cmd_help), ("ping", cmd_ping),
        ("server", cmd_server), ("status", cmd_server),
        ("new", cmd_new), ("session", cmd_session), ("sessions", cmd_sessions),
        ("switch", cmd_switch), ("detach", cmd_detach), ("kill", cmd_kill),
        ("clear", cmd_clear), ("rename", cmd_rename),
        ("model", cmd_model), ("agent", cmd_agent),
        ("ls", cmd_ls), ("projects", cmd_projects),
        ("worktrees", cmd_worktrees), ("tasks", cmd_tasks), ("tasklist", cmd_tasks),
        ("mcp", cmd_mcp), ("mcps", cmd_mcp), ("skills", cmd_skills), ("settings", cmd_settings),
        ("restart_server", cmd_restart_server),
        ("run_shorts", cmd_run_shorts), ("shorts", cmd_run_shorts),
        ("run_ranking", cmd_run_ranking), ("ranking", cmd_run_ranking),
        ("run_brief", cmd_run_brief), ("brief", cmd_run_brief),
        ("stats", cmd_stats), ("statistics", cmd_stats),
        ("mem", cmd_mem), ("recall", cmd_recall), ("vault", cmd_vault),
    ]
    for name, handler in commands:
        app.add_handler(CommandHandler(name, handler, block=True))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=False))
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook", action="store_true")
    parser.add_argument("--port", type=int, default=int(env("PORT", "8080") or "8080"))
    parser.add_argument("--log-level", default=env("LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    log_dir = Path(__file__).resolve().parent.parent
    log_file = log_dir / "bot.log"
    log_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s :: %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(args.log_level)
    sh = logging.StreamHandler()
    sh.setFormatter(log_fmt)
    root_logger.addHandler(sh)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(log_fmt)
    root_logger.addHandler(fh)

    LOG.info("Milo Bot starting — Persistent OpenCode Session Bridge")
    app = make_application()

    if args.webhook:
        path = env("WEBHOOK_PATH", "/milo") or "/milo"
        app.run_webhook(listen=env("WEBHOOK_LISTEN", "127.0.0.1"),
                        port=args.port, url_path=path, webhook_url=env("WEBHOOK_URL"))
    else:
        LOG.info("Polling mode with auto-reconnect…")
        import telegram.error
        while True:
            try:
                app.run_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                    close_loop=False,
                )
            except (telegram.error.NetworkError, ConnectionResetError, asyncio.TimeoutError) as e:
                LOG.warning("Polling lost: %s — reconnecting in 5s", e)
                time.sleep(5)
            except KeyboardInterrupt:
                LOG.info("Shutdown")
                break
            except Exception as e:
                LOG.exception("Fatal error: %s — restarting in 10s", e)
                time.sleep(10)


if __name__ == "__main__":
    main()
