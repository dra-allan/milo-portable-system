#!/usr/bin/env python
"""Milo Telegram bot - instant chat, persistent agent sessions, VPS control plane.

Why this rewrite
----------------
The old bot spawned ``opencode run <prompt>`` for every single message. That
meant:

* **A brand new session per message.** ``opencode run`` with no ``--session``
  starts a fresh conversation, so Milo forgot the previous line every time and
  re-read the workspace from cold.
* **Minutes per reply.** Each turn paid process start + MCP server cold boot +
  full agent context assembly before the model even saw the question.

Three paths now, picked explicitly so behaviour is never a surprise:

1. **OPS PATH (instant, no LLM).** ``/status``, ``/logs``, ``/run``, ``/report``
   shell straight into Task Scheduler and the log files. Sub-second. This is
   what replaces RDP for routine checks.
2. **FAST PATH (~1-2s).** Plain text goes to an OpenAI-compatible chat
   completion (NVIDIA by default) with a rolling per-chat history. No tools, no
   subprocess.
3. **AGENT PATH (persistent).** ``/ask`` / ``/o`` / a leading ``!`` talk to a
   long-lived ``opencode serve`` process over HTTP, reusing **one session per
   Telegram chat**. The server is already warm, so there is no cold boot, and
   the session id is persisted to disk so a bot restart keeps the thread.

Requires ``opencode serve`` running (see scripts/vps/Install-MiloDaemons.ps1).
If the server is down the agent path says so instead of hanging for 10 minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import socket
import sqlite3
import sys
import textwrap
import time
import uuid
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"httpx missing ({exc}); run: pip install -r requirements.txt\n")
    raise SystemExit(1)

try:
    from telegram import Update, constants
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        ApplicationHandlerStop,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        TypeHandler,
        filters,
    )
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"python-telegram-bot missing ({exc}); pip install -r requirements.txt\n")
    raise SystemExit(1)

LOG = logging.getLogger("milo.bot")
TG_LIMIT = 4000  # under Telegram's 4096 so chunk headers always fit

HELP_TEXT = textwrap.dedent(
    """
    *Milo* - your VPS in your pocket.

    *instant ops (no AI, sub-second)*
    /status - daemons, last runs, disk, last sweeps
    /logs <shorts|ranking|bot|driver> [lines]
    /run <shorts|ranking|driver|routines> - kick a task now
    /report - force the pipeline report
    /ping - bot + opencode server health

    *agent (full tools, keeps context)*
    /ask <text> - or just start a line with !
    /new - reset this chat's thread

    *memory*
    /mem save <title> | <body> - /mem list - /recall <query> - /vault <path>

    Plain text = fast chat. Nothing else needed.
    """
).strip()

FAST_SYSTEM = textwrap.dedent(
    """
    You are Milo, Allan's assistant. You are replying over Telegram from his
    Windows VPS. Be short, direct, and opinionated. No preambles, no bullet
    lists unless asked, no markdown headers. A fragment beats a sentence.

    You are on the FAST path: no tools, no filesystem, no shell, no memory
    beyond this chat. If the answer needs the box (logs, files, git, running a
    pipeline, YouTube state) say exactly that and tell him to use /ask for the
    agent, or the matching ops command (/status, /logs, /run). Never invent log
    output, upload counts, or task states.
    """
).strip()

CANONICAL_TASKS = [
    "Milo-OpencodeServer",
    "Milo-TelegramBot",
    "Milo-ShortsPipeline",
    "Milo-RankingPipeline",
    "Milo-Routines",
    "Milo-Watchdog",
]

RUNNABLE = {
    "shorts": "Milo-ShortsPipeline",
    "ranking": "Milo-RankingPipeline",
    "driver": "Milo-PipelineDriver",
    "report": "Milo-PipelineDriver",
    "routines": "Milo-Routines",
    "watchdog": "Milo-Watchdog",
}


# -- config -------------------------------------------------------------------


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)) or default)
    except ValueError:
        return default


def repo_root() -> Path:
    configured = env("OPENCODE_WORKDIR") or env("MILO_WORKSPACE")
    if configured and Path(configured).is_dir():
        return Path(configured)
    return Path(__file__).resolve().parents[2]


def state_dir() -> Path:
    configured = env("MILO_HOME") or env("MILO_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    local = env("LOCALAPPDATA")
    if local:
        return Path(local) / "milo"
    return Path.home() / ".milo"


def allowed_users() -> set:
    raw = env("ALLOWED_USER_IDS").replace(",", " ")
    out = set()
    for part in raw.split():
        try:
            out.add(int(part))
        except ValueError:
            LOG.warning("ignoring malformed ALLOWED_USER_IDS entry")
    if not out:
        chat = env("TELEGRAM_CHAT_ID")
        if chat.isdigit():
            out.add(int(chat))
    return out


def truncate(text: str, limit: int = TG_LIMIT) -> str:
    text = (text or "").rstrip()
    return text if len(text) <= limit else text[: limit - 40] + "\n...[truncated]"


# -- per-chat opencode sessions ----------------------------------------------


class SessionStore:
    """chat_id -> opencode session id, persisted so restarts keep the thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
        data: Dict[str, Any] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            data = {}
        # tolerate the older {chat: {"session_id": ...}} shape
        self.data: Dict[str, str] = {
            k: (v.get("session_id", "") if isinstance(v, dict) else str(v))
            for k, v in data.items()
        }

    def get(self, chat_id: Any) -> str:
        return self.data.get(str(chat_id), "")

    def set(self, chat_id: Any, session_id: str) -> None:
        self.data[str(chat_id)] = session_id
        self._flush()

    def drop(self, chat_id: Any) -> None:
        self.data.pop(str(chat_id), None)
        self._flush()

    def _flush(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError as exc:
            LOG.warning("could not persist sessions: %s", exc)


class OpencodeClient:
    """Talks to a warm ``opencode serve`` over HTTP. One session per chat."""

    def __init__(self, store: SessionStore) -> None:
        self.base = (env("OPENCODE_SERVER_URL") or "http://127.0.0.1:4096").rstrip("/")
        self.agent = env("MILO_AGENT", "milo") or "milo"
        self.model = env("OPENCODE_MODEL")
        self.store = store
        self.timeout = env_int("OPENCODE_TIMEOUT_SEC", 900)
        headers = {"Content-Type": "application/json"}
        password = env("OPENCODE_SERVER_PASSWORD")
        if password:
            user = env("OPENCODE_SERVER_USERNAME", "opencode") or "opencode"
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        # One pooled client per process: keep-alive is a real chunk of the win.
        self.http = httpx.AsyncClient(
            base_url=self.base,
            headers=headers,
            timeout=httpx.Timeout(connect=5.0, read=self.timeout, write=30.0, pool=5.0),
        )
        self._locks: Dict[str, asyncio.Lock] = {}

    def lock(self, chat_id: Any) -> asyncio.Lock:
        return self._locks.setdefault(str(chat_id), asyncio.Lock())

    async def health(self) -> Tuple[bool, str]:
        try:
            resp = await self.http.get("/global/health", timeout=5.0)
            if resp.status_code == 200:
                return True, f"opencode {(resp.json() or {}).get('version', '?')}"
            return False, f"HTTP {resp.status_code}"
        except Exception as exc:
            return False, type(exc).__name__

    async def ensure_session(self, chat_id: Any) -> str:
        sid = self.store.get(chat_id)
        if sid:
            try:
                resp = await self.http.get(f"/session/{sid}", timeout=10.0)
                if resp.status_code == 200:
                    return sid
                LOG.info("session %s gone (HTTP %s); creating a fresh one", sid, resp.status_code)
            except Exception as exc:
                # A server hiccup is not a dead session. Keep the thread.
                LOG.warning("session probe failed: %s", exc)
                return sid
        resp = await self.http.post("/session", json={"title": f"telegram:{chat_id}"}, timeout=30.0)
        resp.raise_for_status()
        sid = str((resp.json() or {}).get("id") or "")
        if not sid:
            raise RuntimeError("opencode did not return a session id")
        self.store.set(chat_id, sid)
        LOG.info("chat %s -> new opencode session %s", chat_id, sid)
        return sid

    async def ask(self, chat_id: Any, text: str) -> str:
        async with self.lock(chat_id):
            sid = await self.ensure_session(chat_id)
            body: Dict[str, Any] = {
                "agent": self.agent,
                "parts": [{"type": "text", "text": text}],
            }
            if self.model:
                body["model"] = self.model
            resp = await self.http.post(f"/session/{sid}/message", json=body)
            if resp.status_code == 404:
                self.store.drop(chat_id)
                sid = await self.ensure_session(chat_id)
                resp = await self.http.post(f"/session/{sid}/message", json=body)
            resp.raise_for_status()
            return self._render(resp.json() or {})

    @staticmethod
    def _render(payload: Dict[str, Any]) -> str:
        parts = payload.get("parts") or []
        chunks = [
            str(p.get("text") or "").strip()
            for p in parts
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        ]
        out = "\n\n".join(c for c in chunks if c).strip()
        if env("MILO_BOT_SHOW_TOOLS") in {"1", "true", "yes"}:
            tools = [
                str(p.get("tool") or "?")
                for p in parts
                if isinstance(p, dict) and p.get("type") == "tool"
            ]
            if tools:
                out += "\n\n- tools: " + ", ".join(tools[:20])
        return out or "(the agent finished without saying anything)"


# -- fast chat path -----------------------------------------------------------


class FastChat:
    """OpenAI-compatible chat completion. Default provider: NVIDIA NIM."""

    def __init__(self) -> None:
        self.key = env("NVIDIA_API_KEY") or env("FAST_API_KEY") or env("OPENAI_API_KEY")
        self.base = (
            env("FAST_BASE_URL")
            or env("NVIDIA_BASE_URL")
            or "https://integrate.api.nvidia.com/v1"
        ).rstrip("/")
        self.model = (
            env("FAST_MODEL")
            or env("NVIDIA_MODEL")
            or "nvidia/llama-3.3-nemotron-super-49b-v1"
        )
        self.turns = env_int("MILO_FAST_HISTORY_TURNS", 10)
        self.history: Dict[str, Deque[Dict[str, str]]] = {}
        self.http = httpx.AsyncClient(
            base_url=self.base,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(
                connect=5.0, read=env_int("MILO_FAST_TIMEOUT_SEC", 45), write=15.0, pool=5.0
            ),
        )

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def reset(self, chat_id: Any) -> None:
        self.history.pop(str(chat_id), None)

    async def reply(self, chat_id: Any, text: str) -> str:
        hist = self.history.setdefault(str(chat_id), deque(maxlen=self.turns * 2))
        messages = [{"role": "system", "content": FAST_SYSTEM}]
        messages.extend(hist)
        messages.append({"role": "user", "content": text})
        resp = await self.http.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": env_int("MILO_FAST_TEMP_X100", 40) / 100,
                "max_tokens": env_int("MILO_FAST_MAX_TOKENS", 900),
                "stream": False,
            },
        )
        resp.raise_for_status()
        data = resp.json() or {}
        out = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        out = out.strip()
        if out:
            hist.append({"role": "user", "content": text})
            hist.append({"role": "assistant", "content": out})
        return out or "(empty reply)"


# -- ops path: Task Scheduler + logs, straight off the box --------------------

PS_STATUS = r"""
$ErrorActionPreference='SilentlyContinue'
$names = @(__NAMES__)
$tasks = foreach ($n in $names) {
  $t = Get-ScheduledTask -TaskName $n
  if ($t) {
    $i = $t | Get-ScheduledTaskInfo
    [pscustomobject]@{ name=$n; state=[string]$t.State; last=[string]$i.LastRunTime; rc=$i.LastTaskResult; next=[string]$i.NextRunTime }
  } else {
    [pscustomobject]@{ name=$n; state='MISSING'; last=''; rc=$null; next='' }
  }
}
$disk = Get-PSDrive C | Select-Object -First 1
[pscustomobject]@{
  tasks  = @($tasks)
  freeGB = [math]::Round($disk.Free/1GB,1)
  usedGB = [math]::Round($disk.Used/1GB,1)
} | ConvertTo-Json -Depth 4 -Compress
"""


async def run_ps(script: str, timeout: int = 45) -> Tuple[int, str, str]:
    exe = "powershell" if os.name == "nt" else "pwsh"
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return 127, "", "powershell not found on this machine"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", "powershell timed out"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def log_targets() -> Dict[str, Path]:
    root, state = repo_root(), state_dir()
    return {
        "bot": Path(__file__).resolve().parents[1] / "bot.log",
        "shorts": state / "logs" / "pipelines" / "shorts-latest.log",
        "ranking": state / "logs" / "pipelines" / "ranking-latest.log",
        "pov": state / "logs" / "pipelines" / "pov-latest.log",
        "driver": state / "logs" / "routines" / "pipeline-driver.log",
        "watchdog": state / "logs" / "watchdog.log",
        "channels": state / "logs" / "channels.log",
        "shorts-raw": root / "artisan" / "youtube-shorts-pipeline" / "data" / "logs" / "pipeline.log",
        "ranking-raw": root / "artisan" / "ranking-shorts-pipeline" / "data" / "logs" / "ranking.log",
    }


def read_status_files() -> str:
    """Summaries written by scripts/vps/Run-Pipeline.ps1 after every sweep."""
    folder = state_dir() / "pipeline-status"
    lines = []
    for name in ("shorts", "ranking", "pov"):
        path = folder / f"{name}.json"
        if not path.is_file():
            lines.append(f"  {name}: no run recorded yet")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            lines.append(f"  {name}: status file unreadable")
            continue
        age_h = (time.time() - path.stat().st_mtime) / 3600
        mark = "ok" if data.get("exit_code") == 0 else "FAIL"
        stale = "  (STALE)" if age_h > 26 else ""
        lines.append(
            f"  {name}: {mark} - {data.get('finished', '?')} - "
            f"{data.get('duration_min', '?')}min - uploads={data.get('uploads', '?')}{stale}"
        )
    return "\n".join(lines)


def tail_file(path: Path, lines: int) -> str:
    if not path.is_file():
        return f"no log at {path}"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=max(1, min(lines, 200)))) or "(log is empty)"
    except OSError as exc:
        return f"read failed: {exc}"


# -- local memory (kept: it is instant) --------------------------------------


def ensure_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            scope TEXT, kind TEXT, created_at INTEGER NOT NULL, topic_key TEXT)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)")
    conn.commit()
    return conn


# -- typing indicator --------------------------------------------------------


class Typing:
    """Keep 'Milo is typing...' alive so a long agent turn never looks dead."""

    def __init__(self, update: Update) -> None:
        self.chat = update.effective_chat
        self.task: Optional[asyncio.Task] = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.chat.send_action(constants.ChatAction.TYPING)
            except Exception:
                return
            await asyncio.sleep(4)

    async def __aenter__(self) -> "Typing":
        if self.chat:
            self.task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self.task:
            self.task.cancel()


async def send(update: Update, text: str) -> None:
    body = text or "..."
    for i in range(0, min(len(body), TG_LIMIT * 4), TG_LIMIT):
        await update.effective_message.reply_text(body[i : i + TG_LIMIT])


# -- handlers ----------------------------------------------------------------


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await send(update, "Milo online. /help for the map, or just talk.")


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_markdown(HELP_TEXT)


async def cmd_whoami(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await send(update, f"user id: {user.id if user else '?'}\nchat id: {update.effective_chat.id}")


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    oc: OpencodeClient = ctx.application.bot_data["opencode"]
    fast: FastChat = ctx.application.bot_data["fast"]
    started = time.monotonic()
    healthy, detail = await oc.health()
    took = (time.monotonic() - started) * 1000
    sid = oc.store.get(update.effective_chat.id) or "(none yet)"
    await send(
        update,
        "\n".join(
            [
                f"bot: up (pid {os.getpid()})",
                f"opencode server: {'ok' if healthy else 'DOWN'} - {detail} ({took:.0f}ms)",
                f"  {oc.base} - agent={oc.agent} - model={oc.model or '(config default)'}",
                f"fast path: {'ok' if fast.configured else 'NOT CONFIGURED (set NVIDIA_API_KEY)'} - {fast.model}",
                f"this chat's session: {sid}",
                f"workdir: {repo_root()}",
                f"state: {state_dir()}",
            ]
        ),
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    ctx.application.bot_data["opencode"].store.drop(chat_id)
    ctx.application.bot_data["fast"].reset(chat_id)
    await send(update, "fresh thread. next message starts a new session.")


async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    names = ", ".join(f"'{n}'" for n in CANONICAL_TASKS + ["Milo-PipelineDriver"])
    code, out, err = await run_ps(PS_STATUS.replace("__NAMES__", names))
    lines = ["daemons"]
    if code == 0 and out.strip():
        try:
            data = json.loads(out.strip().splitlines()[-1])
            for task in data.get("tasks") or []:
                mark = {"Running": ">", "Ready": "+", "Disabled": "x", "MISSING": "x"}.get(
                    task.get("state"), "?"
                )
                rc = task.get("rc")
                rc_txt = "" if rc in (0, None) else f" rc={rc}"
                lines.append(
                    f"  [{mark}] {task.get('name')} - {task.get('state')}{rc_txt}\n"
                    f"      last {task.get('last') or 'never'} - next {task.get('next') or '-'}"
                )
            lines.append(f"\ndisk C: {data.get('freeGB')}GB free / {data.get('usedGB')}GB used")
        except (json.JSONDecodeError, IndexError):
            lines.append(truncate(out, 1500))
    else:
        lines.append(f"  could not query Task Scheduler: {truncate(err or out, 400)}")
    lines.append("\nlast sweeps")
    lines.append(read_status_files())
    await send(update, "\n".join(lines))


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    targets = log_targets()
    if not args:
        await send(update, "usage: /logs <" + "|".join(sorted(targets)) + "> [lines]")
        return
    key = args[0].lower()
    if key not in targets:
        await send(update, f"unknown log '{key}'. options: {', '.join(sorted(targets))}")
        return
    lines = 40
    if len(args) > 1 and args[1].isdigit():
        lines = int(args[1])
    body = await asyncio.to_thread(tail_file, targets[key], lines)
    await send(update, f"- {key} (last {lines}) -\n{truncate(body, TG_LIMIT - 60)}")


async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if not args or args[0].lower() not in RUNNABLE:
        await send(update, "usage: /run <" + "|".join(sorted(set(RUNNABLE))) + ">")
        return
    task = RUNNABLE[args[0].lower()]
    code, out, err = await run_ps(
        f"try {{ Start-ScheduledTask -TaskName '{task}' -ErrorAction Stop; 'started' }} "
        f"catch {{ 'ERROR: ' + $_.Exception.Message }}"
    )
    reply = (out or err).strip() or f"rc={code}"
    await send(update, f"{task}: {reply}\n(watch it with /logs {args[0].lower()})")


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.args = ["driver"]
    await cmd_run(update, ctx)


async def agent_turn(update: Update, ctx: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    oc: OpencodeClient = ctx.application.bot_data["opencode"]
    healthy, detail = await oc.health()
    if not healthy:
        await send(
            update,
            f"opencode server is not answering ({detail}).\n"
            "on the box: Start-ScheduledTask Milo-OpencodeServer",
        )
        return
    async with Typing(update):
        started = time.monotonic()
        try:
            out = await oc.ask(update.effective_chat.id, prompt)
        except httpx.HTTPStatusError as exc:
            out = f"opencode returned HTTP {exc.response.status_code}: {truncate(exc.response.text, 600)}"
        except Exception as exc:
            out = f"agent call failed: {type(exc).__name__}: {exc}"
    took = time.monotonic() - started
    await send(update, f"{out}\n\n[{took:.1f}s]")


async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = " ".join(ctx.args or []).strip()
    if not prompt:
        await send(update, "usage: /ask <what you want done on the box>")
        return
    await agent_turn(update, ctx, prompt)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    if text.startswith("!"):
        await agent_turn(update, ctx, text[1:].strip())
        return
    fast: FastChat = ctx.application.bot_data["fast"]
    if not fast.configured:
        await agent_turn(update, ctx, text)
        return
    async with Typing(update):
        try:
            out = await fast.reply(update.effective_chat.id, text)
        except Exception as exc:
            LOG.warning("fast path failed (%s); falling back to agent", exc)
            await agent_turn(update, ctx, text)
            return
    await send(update, out)


async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    conn = ctx.application.bot_data["db"]
    if not args:
        await send(update, "usage: /mem save <title> | <content>   or   /mem list")
        return
    if args[0].lower() == "save":
        joined = " ".join(args[1:])
        if "|" not in joined:
            await send(update, "split title and body with |")
            return
        title, content = (s.strip() for s in joined.split("|", 1))
        conn.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:12], title, content, "personal", "note", int(time.time()), None),
        )
        conn.commit()
        await send(update, "saved.")
    elif args[0].lower() == "list":
        rows = conn.execute(
            "SELECT title, substr(content,1,140) FROM memories ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        await send(update, "\n\n".join(f"{t}\n{b}" for t, b in rows) or "nothing saved locally.")
    else:
        await send(update, "unknown /mem subcommand.")


async def cmd_recall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(ctx.args or []).strip()
    if not query:
        await send(update, "usage: /recall <query>")
        return
    like = f"%{query}%"
    rows = ctx.application.bot_data["db"].execute(
        "SELECT title, content FROM memories WHERE title LIKE ? OR content LIKE ? "
        "ORDER BY created_at DESC LIMIT 5",
        (like, like),
    ).fetchall()
    await send(update, "\n\n".join(f"{t}\n{truncate(c, 600)}" for t, c in rows) or "no matches.")


async def cmd_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    relative = " ".join(ctx.args or []).strip()
    if not relative:
        await send(update, "usage: /vault <relative-path-in-vault>")
        return
    root = Path(env("MILO_VAULT_DIR") or str(Path.home() / "vault")).expanduser().resolve()
    candidate = (root / relative.lstrip("/\\")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        await send(update, "refusing access outside the vault.")
        return
    if not candidate.is_file() or candidate.is_symlink():
        await send(update, "not a regular vault file.")
        return
    try:
        await send(update, truncate(candidate.read_text(encoding="utf-8", errors="replace"), 3500))
    except OSError as exc:
        await send(update, f"read failed: {exc}")


# -- wiring ------------------------------------------------------------------


def single_instance_guard() -> Optional[socket.socket]:
    """Refuse to start a second poller - Telegram answers 409 and both flap."""
    port = env_int("MILO_BOT_LOCK_PORT", 47431)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return sock
    except OSError:
        LOG.error("another Milo bot already holds 127.0.0.1:%s - exiting", port)
        return None


def build_app() -> Application:
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    allowed = allowed_users()
    if not allowed:
        raise SystemExit("ALLOWED_USER_IDS (or TELEGRAM_CHAT_ID) required - refusing to run open")

    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)  # a long agent turn must not block fast chat
        .get_updates_read_timeout(35)
        .build()
    )

    store = SessionStore(state_dir() / "telegram_sessions.json")
    app.bot_data["opencode"] = OpencodeClient(store)
    app.bot_data["fast"] = FastChat()
    db_path = Path(env("MILO_DB_PATH") or str(state_dir() / "memory.db")).expanduser()
    app.bot_data["db"] = ensure_db(db_path)

    async def enforce_auth(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user and user.is_bot:
            raise ApplicationHandlerStop
        if not user or user.id not in allowed:
            LOG.warning(
                "denied: user=%s chat=%s",
                getattr(user, "id", None),
                getattr(chat, "id", None),
            )
            raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, enforce_auth, block=True), group=-1)
    for name, handler in [
        ("start", cmd_start),
        ("help", cmd_help),
        ("ping", cmd_ping),
        ("whoami", cmd_whoami),
        ("new", cmd_new),
        ("status", cmd_status),
        ("logs", cmd_logs),
        ("run", cmd_run),
        ("report", cmd_report),
        ("ask", cmd_ask),
        ("o", cmd_ask),
        ("opencode", cmd_ask),
        ("milo", cmd_ask),
        ("mem", cmd_mem),
        ("recall", cmd_recall),
        ("vault", cmd_vault),
    ]:
        app.add_handler(CommandHandler(name, handler, block=False))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=False))
    return app


def setup_logging(level: str) -> None:
    log_file = Path(__file__).resolve().parents[1] / "bot.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s :: %(message)s")
    root = logging.getLogger()
    root.setLevel(level.upper())
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    rotating = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    rotating.setFormatter(fmt)
    root.addHandler(rotating)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def load_dotenv() -> None:
    """Load milo-bot/.env without a dependency. Real environment always wins."""
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default=env("LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true", help="start, verify wiring, exit")
    args = parser.parse_args()

    load_dotenv()
    setup_logging(args.log_level)

    lock = single_instance_guard()
    if lock is None:
        raise SystemExit(1)

    app = build_app()
    LOG.info("Milo bot starting - workdir=%s - state=%s", repo_root(), state_dir())

    if args.once:
        async def probe() -> None:
            me = await app.bot.get_me()
            healthy, detail = await app.bot_data["opencode"].health()
            print(f"[ok] bot online as @{me.username}")
            print(f"[{'ok' if healthy else '!!'}] opencode server: {detail}")
            print(f"[{'ok' if app.bot_data['fast'].configured else '!!'}] fast path key present")

        asyncio.run(probe())
        return

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
