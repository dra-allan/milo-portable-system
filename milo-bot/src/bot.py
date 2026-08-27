#!/usr/bin/env python
"""Milo Telegram bot - fast chat, persistent agent sessions, remote ops.

Design, and why
===============

Three problems this file exists to solve:

1. **A new session on every message.** The old bot spawned a bare
   ``opencode run <prompt>`` per message, so every reply started from zero
   context and paid MCP cold-boot again. Now each chat owns a *session id*,
   persisted in ``state/telegram_sessions.json`` (the same file miloctl's
   stdlib bot uses), continued with ``opencode run --session <id>``. ``/new``
   is the only thing that resets it.

2. **Speed.** Two paths:

   * **fast path** - plain text goes straight to an OpenAI-compatible chat
     completion (NVIDIA by default) with the real Milo persona and a rolling
     per-chat history. ~1-2s, no subprocess, no tools.
   * **agent path** - ``/do``, ``/oc`` and agent-mode text go to opencode.
     If ``OPENCODE_SERVER_URL`` is set (and it should be: an ``opencode serve``
     daemon runs on the VPS) the run *attaches* to that live server instead of
     booting a fresh one, which is where the multi-second cold start went.

3. **Ops without RDP.** ``/status``, ``/pipelines``, ``/run``, ``/logs``,
   ``/kill``, ``/uploads`` are answered locally from the filesystem and Task
   Scheduler. No model involved, so they are instant and they still work when
   every API key on the box has expired.

Everything is fail-closed on ``ALLOWED_USER_IDS`` and every long operation
runs in its own task, so one slow agent turn never blocks the poller.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    from telegram import Update, constants
    from telegram.error import TelegramError
    from telegram.ext import (Application, ApplicationBuilder, ApplicationHandlerStop,
                              CommandHandler, ContextTypes, MessageHandler, TypeHandler,
                              filters)
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"python-telegram-bot missing: {exc}\n")
    raise SystemExit(1)

LOG = logging.getLogger("milo.bot")

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT_DIR = Path(__file__).resolve().parents[1]
STARTED_AT = time.time()

#: Telegram hard limit is 4096; leave room for the header we prepend.
CHUNK = 3800

HELP_TEXT = textwrap.dedent("""
*Milo - remote control*

_talk_
just type - instant chat (fast path)
`/do <task>` - full agent w/ tools, keeps this chat's session
`/agent on|off` - route plain text to the agent session instead
`/new` - reset this chat's agent session

_ops_
`/status` - daemons, uptime, disk
`/pipelines` - last shorts + ranking run
`/run shorts|ranking [n]` - sweep now
`/kill shorts|ranking` - stop a run
`/logs shorts|ranking|bot [n]` - tail a log
`/uploads` - what posted today

_memory_
`/mem save <title> | <body>`, `/mem list`, `/recall <q>`, `/vault <path>`

`/ping` `/whoami` `/help`
""").strip()


# -- env ----------------------------------------------------------------------


def _load_env_files() -> None:
    """Populate ``os.environ`` from every ``.env`` Milo might own.

    Scheduled tasks start with a minimal environment, so a bot that trusts
    the ambient env exits silently at boot ("ALLOWED_USER_IDS empty") and
    looks like Telegram's fault. Existing values always win, so a task
    definition can still override a file.
    """
    candidates = [
        Path(os.environ["MILO_HOME"]) / ".env" if os.environ.get("MILO_HOME") else None,
        Path(os.environ["LOCALAPPDATA"]) / "milo" / ".env" if os.environ.get("LOCALAPPDATA") else None,
        Path.home() / ".milo" / ".env",
        REPO_ROOT / ".env",
        BOT_DIR / ".env",
    ]
    for path in candidates:
        if not path:
            continue
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)) or default)
    except ValueError:
        return default


def env_flag(name: str, default: bool = False) -> bool:
    return env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def state_dir() -> Path:
    """Milo's state directory - shared with miloctl so both bots agree."""
    raw = env("MILO_HOME")
    if raw:
        return Path(raw).expanduser()
    local = env("LOCALAPPDATA")
    if local:
        return Path(local) / "milo"
    return Path.home() / ".milo"


def workdir() -> Path:
    for candidate in (env("OPENCODE_WORKDIR"), env("MILO_WORKSPACE"), str(REPO_ROOT)):
        if candidate and Path(candidate).expanduser().is_dir():
            return Path(candidate).expanduser()
    return Path.cwd()


def allowed_users() -> set:
    out = set()
    raw = env("ALLOWED_USER_IDS") or env("TELEGRAM_CHAT_ID")
    for part in re.split(r"[,\s]+", raw):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


# -- persona for the fast path ------------------------------------------------

_PERSONA_CACHE: Dict[str, Tuple[float, str]] = {}
_PERSONA_FALLBACK = (
    "You are Milo, Allan's assistant and chief of stuff. Direct, warm, zero "
    "corporate filler. Short answers for short questions. You run on a Windows "
    "VPS and drive Allan's YouTube pipelines (shorts, ranking, POV) plus his "
    "vault and memory. If a task needs tools, files, git or the pipelines, say "
    "so and tell him to send it with /do so the full agent handles it."
)


def persona_prompt() -> str:
    """Milo's real persona, read from whatever ``milo sync`` last wrote.

    Cached for 5 minutes: this is on the hot path of every fast reply, and a
    ``milo sync`` mid-day should still land without a bot restart.
    """
    hit = _PERSONA_CACHE.get("p")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    limit = env_int("MILO_PERSONA_CHARS", 6000)
    for path in (
        state_dir() / "persona" / "MILO.md",
        Path.home() / ".config" / "opencode" / "AGENTS.md",
        Path(env("APPDATA", str(Path.home()))) / "opencode" / "AGENTS.md",
        REPO_ROOT / "AGENTS.md",
    ):
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    text = text[:limit]
                    _PERSONA_CACHE["p"] = (time.time(), text)
                    return text
        except OSError:
            continue
    _PERSONA_CACHE["p"] = (time.time(), _PERSONA_FALLBACK)
    return _PERSONA_FALLBACK


# -- local memory -------------------------------------------------------------


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
        scope TEXT, kind TEXT, created_at INTEGER NOT NULL, topic_key TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)")
    conn.commit()
    return conn


# -- session store (the actual bug fix) ---------------------------------------


class SessionStore:
    """chat_id -> opencode session id, on disk.

    Shared with ``miloctl.bot`` (same file, same shape) so it does not matter
    which of the two bots is running: the thread survives either.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        try:
            self.data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def get(self, chat_id: Any) -> str:
        return self.data.get(str(chat_id), "")

    async def set(self, chat_id: Any, session: str) -> None:
        if not session or self.data.get(str(chat_id)) == session:
            return
        async with self._lock:
            self.data[str(chat_id)] = session
            self._flush()

    async def drop(self, chat_id: Any) -> None:
        async with self._lock:
            self.data.pop(str(chat_id), None)
            self._flush()

    def _flush(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            LOG.warning("could not persist sessions: %s", exc)


# -- opencode (agent path) ----------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*(\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


def opencode_bin() -> str:
    return env("OPENCODE_BIN") or shutil.which("opencode") or "opencode"


def agent_timeout() -> int:
    return max(30, env_int("OPENCODE_TIMEOUT_SEC", 600))


def build_argv(prompt: str, session: str, *, want_json: bool) -> List[str]:
    """argv for one opencode turn.

    Flags go *before* the positional prompt - the old code appended
    ``--session`` after it, which is exactly the kind of thing that parses as
    part of the message and starts a brand new session anyway.

    ``--attach`` points at the long-lived ``opencode serve`` daemon so a turn
    reuses already-booted MCP servers instead of paying cold start per message.
    """
    argv = [opencode_bin(), "run", "--agent", env("MILO_AGENT", "milo") or "milo", "--auto"]
    model = env("OPENCODE_MODEL")
    if model:
        argv += ["--model", model]
    server = env("OPENCODE_SERVER_URL")
    if server:
        argv += ["--attach", server]
    if session:
        argv += ["--session", session]
    elif want_json:
        # Only a JSON run reports the session id we need to keep the thread.
        argv += ["--format", "json", "--title", "telegram"]
    argv.append(prompt)
    return argv


def parse_json_events(raw: str) -> Tuple[str, str]:
    """``(text, session_id)`` out of ``--format json`` output."""
    session = ""
    parts: List[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        session = evt.get("sessionID") or evt.get("sessionId") or session
        if evt.get("type") == "text":
            chunk = ((evt.get("part") or {}).get("text") or evt.get("text") or "").strip()
            if chunk:
                parts.append(chunk)
    return "\n\n".join(parts).strip(), session


async def run_opencode(prompt: str, session: str) -> Tuple[int, str, str]:
    """One agent turn -> ``(rc, text, session_id)``."""
    want_json = not session
    argv = build_argv(prompt, session, want_json=want_json)
    LOG.info("opencode turn (session=%s attach=%s)", session or "new",
             env("OPENCODE_SERVER_URL") or "-")
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(workdir()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=agent_timeout())
    except FileNotFoundError as exc:
        return 127, f"opencode not found: {exc}. Set OPENCODE_BIN to the full path.", session
    except asyncio.TimeoutError:
        if proc:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        return 124, f"agent timed out after {agent_timeout()}s", session
    except (OSError, RuntimeError) as exc:
        return 126, str(exc), session

    rc = proc.returncode or 0
    stdout = strip_ansi(out.decode("utf-8", "replace"))
    stderr = strip_ansi(err.decode("utf-8", "replace"))
    if want_json:
        text, new_session = parse_json_events(stdout)
        return rc, (text or stdout or stderr).strip(), new_session or session
    return rc, (stdout or stderr).strip(), session


async def agent_turn(store: SessionStore, chat_id: Any, prompt: str) -> str:
    """Agent turn that keeps this chat's thread alive across messages."""
    session = store.get(chat_id)
    rc, text, new_session = await run_opencode(prompt, session)
    if rc != 0 and session:
        # A stale session id (pruned storage, upgraded CLI) must not look like
        # a broken bot: drop it and take one cold turn instead.
        LOG.warning("session %s unusable (rc=%s); retrying cold", session, rc)
        await store.drop(chat_id)
        rc, text, new_session = await run_opencode(prompt, "")
    if new_session:
        await store.set(chat_id, new_session)
    if rc != 0:
        return f"agent failed (rc={rc})\n{text[:1200] or 'no output'}"
    return text or "(the agent said nothing)"


# -- fast path ----------------------------------------------------------------

HISTORY: Dict[str, Deque[Dict[str, str]]] = defaultdict(lambda: deque(maxlen=12))


async def fast_reply(chat_id: Any, text: str) -> Optional[str]:
    """Chat completion against an OpenAI-compatible endpoint. ``None`` = fall back."""
    key = env("NVIDIA_API_KEY") or env("MILO_FAST_API_KEY") or env("OPENAI_API_KEY")
    if not key:
        return None
    try:
        import httpx
    except ImportError:
        LOG.warning("httpx missing - fast path disabled (pip install httpx)")
        return None

    base = env("MILO_FAST_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    model = env("MILO_FAST_MODEL", "nvidia/nvidia-nemotron-nano-9b-v2")
    history = HISTORY[str(chat_id)]
    messages = [{"role": "system", "content":
                 persona_prompt() +
                 "\n\nYou are answering on Telegram. Keep it tight and natural. "
                 "Plain text only, no markdown tables, no code dumps unless asked. "
                 "You have no tools on this path: if the ask needs files, git, the "
                 "vault or the pipelines, tell Allan to send it with /do."}]
    messages += list(history)
    messages.append({"role": "user", "content": text})

    try:
        temperature = float(env("MILO_FAST_TEMPERATURE", "0.6") or 0.6)
    except ValueError:
        temperature = 0.6
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": env_int("MILO_FAST_MAX_TOKENS", 700),
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=env_int("MILO_FAST_TIMEOUT_SEC", 45)) as client:
            resp = await client.post(f"{base}/chat/completions", json=payload,
                                     headers={"Authorization": f"Bearer {key}",
                                              "Accept": "application/json"})
        if resp.status_code >= 400:
            LOG.warning("fast path HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        reply_text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        reply_text = reply_text.strip()
    except Exception as exc:  # network, JSON, shape - all fall back
        LOG.warning("fast path failed (%s); falling back to agent", exc)
        return None
    if not reply_text:
        return None
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply_text})
    return reply_text


# -- local ops (no model, always instant) -------------------------------------

PIPELINES: Dict[str, Dict[str, Any]] = {
    "shorts": {
        "label": "YouTube Shorts",
        "dir": REPO_ROOT / "artisan" / "youtube-shorts-pipeline",
        "task": "MiloShortsPipeline",
        "log": "pipeline.log",
    },
    "ranking": {
        "label": "Ranking Shorts",
        "dir": REPO_ROOT / "artisan" / "ranking-shorts-pipeline",
        "task": "MiloRankingPipeline",
        "log": "ranking.log",
    },
}

TASKS = ["MiloTelegramBot", "MiloOpencodeServer", "MiloShortsPipeline",
         "MiloRankingPipeline", "MiloRoutines", "MiloDaemonWatchdog"]


def runs_dir() -> Path:
    return state_dir() / "pipeline_runs"


def _sh(argv: List[str], timeout: int = 25) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 1, str(exc)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def task_report() -> str:
    if os.name != "nt":
        return "not Windows - no Task Scheduler here."
    lines = []
    for name in TASKS:
        rc, out = _sh(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
        if rc != 0:
            lines.append(f"x {name}: not registered")
            continue
        state = re.search(r"Status:\s*(.+)", out)
        last = re.search(r"Last Run Time:\s*(.+)", out)
        result = re.search(r"Last Result:\s*(.+)", out)
        nxt = re.search(r"Next Run Time:\s*(.+)", out)
        state_s = state.group(1).strip() if state else "?"
        result_s = result.group(1).strip() if result else "?"
        mark = "ok" if state_s.lower() in {"ready", "running"} else "x"
        if result_s not in {"0", "267009", "267011", "?"}:
            mark = "!"
        lines.append(f"[{mark}] {name}: {state_s} | last {last.group(1).strip() if last else '-'} "
                     f"| rc {result_s} | next {nxt.group(1).strip() if nxt else '-'}")
    return "\n".join(lines)


def load_run(key: str) -> Dict[str, Any]:
    try:
        return json.loads((runs_dir() / f"{key}-last.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def render_run(key: str) -> str:
    meta = PIPELINES[key]
    run = load_run(key)
    if not run:
        return f"{meta['label']}: no run recorded yet."
    status = run.get("status", "?")
    icon = {"ok": "OK", "failed": "FAILED", "timeout": "TIMEOUT",
            "skipped": "SKIPPED", "running": "RUNNING"}.get(status, status)
    uploads = run.get("uploads") or []
    lines = [f"*{meta['label']}* - {icon}",
             f"started {run.get('started', '?')} · {run.get('duration', '?')}",
             f"uploads: {len(uploads)}"]
    lines += [f"  - {u}" for u in uploads[:8]]
    errors = run.get("errors") or []
    if errors:
        lines.append("errors:")
        lines += [f"  ! {e[:180]}" for e in errors[:4]]
    return "\n".join(lines)


def tail_log(key: str, count: int) -> str:
    if key == "bot":
        path = BOT_DIR / "bot.log"
    else:
        meta = PIPELINES.get(key)
        if not meta:
            return "unknown log. try shorts | ranking | bot"
        logs = Path(meta["dir"]) / "data" / "logs"
        candidates = sorted(logs.glob("daemon-*.log")) if logs.is_dir() else []
        path = candidates[-1] if candidates else logs / str(meta["log"])
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"no log at {path} ({exc})"
    return "\n".join(lines[-count:]) or "(empty)"


def disk_report() -> str:
    try:
        total, used, free = shutil.disk_usage(str(REPO_ROOT))
    except OSError:
        return "disk: unknown"
    gb = 1024 ** 3
    return f"disk: {free / gb:.1f} GB free of {total / gb:.1f} GB ({used / total:.0%} used)"


def runner_argv(key: str, videos: Optional[int]) -> List[str]:
    argv = [sys.executable, str(REPO_ROOT / "scripts" / "daemons" / "pipeline_runner.py"), key]
    if videos:
        argv += ["--videos", str(videos)]
    return argv + ["--notify"]


# -- handlers -----------------------------------------------------------------


async def reply(update: Update, text: str, markdown: bool = False) -> None:
    msg = update.effective_message
    if msg is None:
        return
    text = (text or "").strip() or "(nothing)"
    for i in range(0, len(text), CHUNK):
        piece = text[i:i + CHUNK]
        try:
            if markdown:
                await msg.reply_text(piece, parse_mode=constants.ParseMode.MARKDOWN,
                                     disable_web_page_preview=True)
            else:
                await msg.reply_text(piece, disable_web_page_preview=True)
        except TelegramError:
            with contextlib.suppress(TelegramError):
                await msg.reply_text(piece, disable_web_page_preview=True)


@contextlib.asynccontextmanager
async def typing(ctx: ContextTypes.DEFAULT_TYPE, chat_id: Any):
    """Keep the 'typing...' bubble alive so a slow turn never looks dead."""
    async def beat() -> None:
        while True:
            with contextlib.suppress(Exception):
                await ctx.bot.send_chat_action(chat_id, constants.ChatAction.TYPING)
            await asyncio.sleep(4)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, "Milo online. Talk normally for instant answers, /do for real work, "
                        "/help for the rest.")


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, HELP_TEXT, markdown=True)


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    store: SessionStore = ctx.application.bot_data["sessions"]
    chat_id = update.effective_chat.id if update.effective_chat else "?"
    up = int(time.time() - STARTED_AT)
    info = [
        f"pong · up {up // 3600}h{(up % 3600) // 60}m",
        f"fast path: {'on' if (env('NVIDIA_API_KEY') or env('MILO_FAST_API_KEY')) else 'OFF (no key)'}"
        f" · {env('MILO_FAST_MODEL', 'nvidia/nvidia-nemotron-nano-9b-v2')}",
        f"opencode: {opencode_bin()}",
        f"attach: {env('OPENCODE_SERVER_URL') or '(cold start - set OPENCODE_SERVER_URL)'}",
        f"agent: {env('MILO_AGENT', 'milo')} · timeout {agent_timeout()}s",
        f"workdir: {workdir()}",
        f"session: {store.get(chat_id) or '(none yet)'}",
        f"agent mode: {'on' if ctx.application.bot_data['agent_mode'].get(str(chat_id)) else 'off'}",
    ]
    await reply(update, "\n".join(info))


async def cmd_whoami(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user, chat = update.effective_user, update.effective_chat
    await reply(update, f"user id: {user.id if user else '?'}\nchat id: {chat.id if chat else '?'}")


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await ctx.application.bot_data["sessions"].drop(chat_id)
    HISTORY.pop(str(chat_id), None)
    await reply(update, "fresh thread. next message starts a new session.")


async def cmd_agent_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    modes = ctx.application.bot_data["agent_mode"]
    arg = ctx.args[0].lower() if ctx.args else ""
    if arg in {"on", "1", "true"}:
        modes[chat_id] = True
    elif arg in {"off", "0", "false"}:
        modes[chat_id] = False
    else:
        modes[chat_id] = not modes.get(chat_id, False)
    await reply(update, "agent mode ON - every message now runs in your opencode session "
                        "(slower, full tools)." if modes[chat_id] else
                        "agent mode OFF - plain text is instant chat again. /do still reaches "
                        "the agent.")


async def cmd_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = " ".join(ctx.args or []).strip()
    if not prompt:
        await reply(update, "Usage: /do <what you want done>")
        return
    await handle_agent(update, ctx, prompt)


async def handle_agent(update: Update, ctx: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    chat_id = update.effective_chat.id
    store: SessionStore = ctx.application.bot_data["sessions"]
    locks: Dict[str, asyncio.Lock] = ctx.application.bot_data["locks"]
    lock = locks.setdefault(str(chat_id), asyncio.Lock())
    if lock.locked():
        await reply(update, "still working on the last one - queued behind it.")
    async with lock:
        async with typing(ctx, chat_id):
            out = await agent_turn(store, chat_id, prompt)
    await reply(update, out)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    chat_id = update.effective_chat.id
    if ctx.application.bot_data["agent_mode"].get(str(chat_id)):
        await handle_agent(update, ctx, text)
        return
    async with typing(ctx, chat_id):
        fast = await fast_reply(chat_id, text)
    if fast:
        await reply(update, fast)
        return
    # No key or the endpoint blew up: never leave a message unanswered.
    await handle_agent(update, ctx,
                       "Respond as Milo; keep it tight and natural for chat.\n\n" + text)


# -- ops ----------------------------------------------------------------------


async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    up = int(time.time() - STARTED_AT)
    body = [f"*Milo VPS* · bot up {up // 3600}h{(up % 3600) // 60}m", "",
            task_report(), "", disk_report()]
    await reply(update, "\n".join(body), markdown=True)


async def cmd_pipelines(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, "\n\n".join(render_run(k) for k in PIPELINES), markdown=True)


async def cmd_uploads(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    today = time.strftime("%Y-%m-%d")
    lines = [f"*Uploads {today}*"]
    total = 0
    for key, meta in PIPELINES.items():
        run = load_run(key)
        urls = run.get("uploads") or []
        if not str(run.get("started", "")).startswith(today):
            urls = []
        total += len(urls)
        lines.append(f"\n{meta['label']}: {len(urls)}")
        lines += [f"  - {u}" for u in urls[:10]]
    lines.append(f"\ntotal today: {total}")
    await reply(update, "\n".join(lines), markdown=True)


async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = [a.lower() for a in (ctx.args or [])]
    if not args or args[0] not in PIPELINES:
        await reply(update, "Usage: /run shorts [n] | /run ranking [n]")
        return
    key = args[0]
    videos = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    kwargs: Dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                              "stdin": subprocess.DEVNULL, "cwd": str(REPO_ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # detached, new group
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(runner_argv(key, videos), **kwargs)
    except OSError as exc:
        await reply(update, f"could not start {key}: {exc}")
        return
    await reply(update, f"{PIPELINES[key]['label']} sweep started"
                        f"{f' ({videos} videos)' if videos else ''}. "
                        f"I'll post the report here when it finishes.")


async def cmd_kill(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = [a.lower() for a in (ctx.args or [])]
    if not args or args[0] not in PIPELINES:
        await reply(update, "Usage: /kill shorts | /kill ranking")
        return
    key = args[0]
    lock = Path(PIPELINES[key]["dir"]) / "data" / f"{key}.lock"
    killed: List[str] = []
    pid = 0
    try:
        pid = int(json.loads(lock.read_text(encoding="utf-8")).get("pid") or 0)
    except Exception:
        pid = 0
    if pid:
        rc, out = _sh(["taskkill", "/PID", str(pid), "/T", "/F"] if os.name == "nt"
                      else ["kill", "-9", str(pid)])
        killed.append(f"pid {pid}: {'stopped' if rc == 0 else out[:120]}")
    if os.name == "nt":
        rc, _ = _sh(["schtasks", "/End", "/TN", PIPELINES[key]["task"]])
        if rc == 0:
            killed.append(f"task {PIPELINES[key]['task']} ended")
    await reply(update, "\n".join(killed) or f"nothing running for {key}.")


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    key = args[0].lower() if args else "bot"
    count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 25
    await reply(update, tail_log(key, min(count, 120)))


# -- memory / vault -----------------------------------------------------------


async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    conn: sqlite3.Connection = ctx.application.bot_data["db"]
    if not args:
        await reply(update, "Usage: /mem save <title> | <content>\n/mem list")
        return
    if args[0].lower() == "save":
        joined = " ".join(args[1:])
        if "|" not in joined:
            await reply(update, "Split title and body with |.")
            return
        title, content = (s.strip() for s in joined.split("|", 1))
        conn.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex[:12], title, content, "personal", "note",
                      int(time.time()), None))
        conn.commit()
        await reply(update, "saved.")
    elif args[0].lower() == "list":
        rows = conn.execute("SELECT title, substr(content,1,140) FROM memories "
                            "ORDER BY created_at DESC LIMIT 10").fetchall()
        await reply(update, "\n\n".join(f"{t}\n{b}" for t, b in rows) or "nothing saved locally.")
    else:
        await reply(update, "Unknown /mem command.")


async def cmd_recall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(ctx.args or []).strip()
    if not query:
        await reply(update, "Usage: /recall <query>")
        return
    like = f"%{query}%"
    rows = ctx.application.bot_data["db"].execute(
        "SELECT title, content FROM memories WHERE title LIKE ? OR content LIKE ? "
        "ORDER BY created_at DESC LIMIT 5", (like, like)).fetchall()
    await reply(update, "\n\n".join(f"{t}\n{c[:600]}" for t, c in rows) or "no matches.")


async def cmd_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    relative = " ".join(ctx.args or []).strip()
    if not relative:
        await reply(update, "Usage: /vault <relative-path-in-vault>")
        return
    root = Path(env("MILO_VAULT_DIR", str(Path.home() / "vault"))).expanduser().resolve()
    candidate = (root / relative.lstrip("/\\")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        await reply(update, "Refusing access outside the vault.")
        return
    if not candidate.is_file() or candidate.is_symlink():
        await reply(update, "Not a regular vault file.")
        return
    try:
        await reply(update, candidate.read_text(encoding="utf-8", errors="replace")[:CHUNK])
    except OSError as exc:
        await reply(update, f"Read failed: {exc}")


# -- wiring -------------------------------------------------------------------


def make_application() -> Application:
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required (put it in .env)")
    allowed = allowed_users()
    if not allowed:
        # Fail closed, loudly. An empty allowlist used to mean "deny everything
        # in silence", which is indistinguishable from a dead bot.
        raise SystemExit("ALLOWED_USER_IDS is empty - the bot would deny every message. "
                         "Set it in .env (get your id from @userinfobot).")

    app = (ApplicationBuilder()
           .token(token)
           .concurrent_updates(True)   # one slow turn must not block the poller
           .build())
    app.bot_data["db"] = ensure_db(
        Path(env("MILO_DB_PATH") or str(state_dir() / "memory.db")).expanduser())
    app.bot_data["sessions"] = SessionStore(state_dir() / "telegram_sessions.json")
    app.bot_data["locks"] = {}
    app.bot_data["agent_mode"] = {}

    async def enforce_auth(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or user.is_bot or user.id not in allowed:
            LOG.warning("denied update from %s", getattr(user, "id", "unknown"))
            raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, enforce_auth, block=True), group=-1)
    for name, handler in [
        ("start", cmd_start), ("help", cmd_help), ("ping", cmd_ping),
        ("whoami", cmd_whoami), ("new", cmd_new), ("agent", cmd_agent_mode),
        ("do", cmd_do), ("oc", cmd_do), ("opencode", cmd_do), ("milo", cmd_do),
        ("status", cmd_status), ("pipelines", cmd_pipelines), ("uploads", cmd_uploads),
        ("run", cmd_run), ("kill", cmd_kill), ("logs", cmd_logs),
        ("mem", cmd_mem), ("recall", cmd_recall), ("vault", cmd_vault),
    ]:
        app.add_handler(CommandHandler(name, handler, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=False))
    LOG.info("allowlist: %s", ", ".join(str(a) for a in sorted(allowed)))
    return app


def setup_logging(level: str) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s :: %(message)s")
    root = logging.getLogger()
    root.setLevel(level.upper())
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        from logging.handlers import RotatingFileHandler
        fileh = RotatingFileHandler(BOT_DIR / "bot.log", maxBytes=5_000_000,
                                    backupCount=3, encoding="utf-8")
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError:
        pass
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)


def main() -> None:
    _load_env_files()
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook", action="store_true")
    parser.add_argument("--port", type=int, default=env_int("PORT", 8080))
    parser.add_argument("--log-level", default=env("LOG_LEVEL", "INFO"))
    args = parser.parse_args()
    setup_logging(args.log_level)

    LOG.info("Milo Telegram bot starting (repo=%s state=%s)", REPO_ROOT, state_dir())
    app = make_application()
    if args.webhook:
        app.run_webhook(listen=env("WEBHOOK_LISTEN", "127.0.0.1"), port=args.port,
                        url_path=env("WEBHOOK_PATH", "/milo") or "/milo",
                        webhook_url=env("WEBHOOK_URL"), drop_pending_updates=True)
    else:
        # drop_pending_updates: after a crash-restart the backlog is stale, and
        # replaying it makes the bot answer questions from an hour ago.
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
