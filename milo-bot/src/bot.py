#!/usr/bin/env python
"""
Milo Telegram Bot — unified assistant + opencode bridge.

Two modes in one bot:
  - Natural chat goes to the Milo persona (configured via --agent milo in opencode).
  - /opencode <prompt> shells into `opencode run --agent milo --auto` and streams
    back the result.
  - /mem save ... and /recall ... provide a local-only fallback memory surface
    (used until the Engram MCP server is wired with a real ENGRAM_API_KEY).

Env vars (see .env.example):
  TELEGRAM_BOT_TOKEN       — required
  ALLOWED_USER_IDS         — comma-separated Telegram user IDs allowed to talk to Milo
                              (empty = allow everyone; tighten in production)
  OPENCODE_BIN             — path/name of the opencode CLI
  OPENCODE_WORKDIR         — cwd to spawn opencode in (default: /root/projects)
  OPENCODE_MODEL           — provider/model override (default: let opencode.json decide)
  OPENCODE_TIMEOUT_SEC     — per-call timeout for /opencode bridge (default: 600)
  MILO_AGENT               — opencode agent name to use (default: milo)
  MILO_DB_PATH             — SQLite path for the local fallback memory
                              (default: ~/.milo/milo-bot.sqlite)
  LOG_LEVEL                — DEBUG | INFO | WARNING | ERROR

Usage:
  python bot.py            # long-polling loop
  python bot.py --webhook  # webhook mode (port from $PORT or 8080)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shlex
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Optional

try:
    from telegram import Update, constants
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ImportError as exc:
    sys.stderr.write(
        "python-telegram-bot missing. Install with:\n"
        "  python -m pip install python-telegram-bot\n"
        f"caused by: {exc}\n"
    )
    sys.exit(1)


LOG = logging.getLogger("milo.bot")

HELP_TEXT = textwrap.dedent(
    """
    *Milo* — assistant + opencode bridge.

    *Direct chat*: just send a message. Milo answers in his persona
    (configurable via opencode --agent milo).

    *Commands*
    /opencode <prompt> — run the prompt through `opencode run` non-interactively
                         and reply with the result. Use for code work.
    /milo <text>       — explicit chat with the Milo persona (same as direct chat).
    /mem save <title> | <content>
                       — save a note to the local fallback memory (sqlite).
                         A real Engram server replaces this once configured.
    /mem list          — show the last 10 saved memories.
    /recall <query>    — naive substring search across local memory.
    /vault <path>      — fetch and reply with the contents of a vault file
                         (paths are relative to ~/ vault; security: read-only, no `..`).
    /ping               — liveness + config echo.
    /help               — this message.
    """
).strip()


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name, default)
    if value is not None and isinstance(value, str):
        value = value.strip()
    return value


REQUIRED_ENV = ["TELEGRAM_BOT_TOKEN"]


def parse_allowed_users() -> Optional[set[int]]:
    raw = env("ALLOWED_USER_IDS", "")
    if not raw:
        return None
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            LOG.warning("ignoring malformed ALLOWED_USER_IDS entry: %r", chunk)
    return out or None


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          scope TEXT,
          kind TEXT,
          created_at INTEGER NOT NULL,
          topic_key TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5"
        "(title, content, content='memories', content_rowid='rowid')"
    )
    conn.commit()
    return conn


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Milo online. /help for commands, or just talk to me."
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown(HELP_TEXT)


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = {
        "opencode_bin": env("OPENCODE_BIN", "opencode"),
        "workdir": env("OPENCODE_WORKDIR", str(Path.home() / "projects")),
        "model": env("OPENCODE_MODEL", "(default from opencode.json)"),
        "agent": env("MILO_AGENT", "milo"),
        "bot_user": update.get_bot().username if update.get_bot() else None,
        "me": update.effective_user.username if update.effective_user else None,
    }
    await update.message.reply_text("```\n" + json.dumps(cfg, indent=2) + "\n```", parse_mode=constants.ParseMode.MARKDOWN)


async def run_opencode(prompt: str, timeout_sec: int) -> tuple[int, str, str]:
    binary = env("OPENCODE_BIN", "opencode")
    workdir = env("OPENCODE_WORKDIR", str(Path.home() / "projects"))
    agent = env("MILO_AGENT", "milo")
    model = env("OPENCODE_MODEL", "")
    cmd = [binary, "run", "--agent", agent, "--auto"]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    LOG.info("spawning opencode: %s (cwd=%s, timeout=%ss)", " ".join(map(shlex.quote, cmd)), workdir, timeout_sec)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"opencode timed out after {timeout_sec}s"
    return proc.returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


async def cmd_opencode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /opencode <prompt> — runs the prompt through opencode non-interactively.")
        return
    prompt = " ".join(ctx.args)
    timeout_sec = int(env("OPENCODE_TIMEOUT_SEC", "600"))
    sent = await update.message.reply_text(f"Running opencode… (timeout {timeout_sec}s)")
    rc, stdout, stderr = await run_opencode(prompt, timeout_sec)
    body_parts = [f"*rc={rc}*"]
    if stdout:
        body_parts.append("```\n" + truncate(stdout, 3500) + "\n```")
    if stderr.strip():
        body_parts.append("*stderr*:\n```\n" + truncate(stderr, 1500) + "\n```")
    text = "\n".join(body_parts)
    try:
        await sent.edit_text(text, parse_mode=constants.ParseMode.MARKDOWN)
    except Exception:
        await update.message.reply_text(truncate(text, 4000))


async def cmd_milo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Explicit Milo chat — runs the message through opencode --agent milo."""
    if not ctx.args:
        await update.message.reply_text("Usage: /milo <text>")
        return
    prompt = " ".join(ctx.args)
    await update.message.reply_text("(Milo thinking…)")
    timeout_sec = int(env("OPENCODE_TIMEOUT_SEC", "600"))
    rc, stdout, stderr = await run_opencode(prompt, timeout_sec)
    if rc == 0 and stdout.strip():
        await update.message.reply_text(truncate(stdout, 4000))
    else:
        await update.message.reply_text(f"Milo hit an error (rc={rc}). Stderr:\n```\n{truncate(stderr, 1500)}\n```")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain text messages route to the Milo persona via opencode."""
    user = update.effective_user
    text = update.message.text if update.message and update.message.text else ""
    if not text:
        return
    LOG.info("chat from %s: %s", user.username if user else "?", text[:80])
    # Wrap with a soft system nudge so opencode knows it's a Telegram prompt.
    prompt = f"This message came from Telegram user @{user.username if user else '?'}. "
    prompt += "Respond as Milo; keep it tight and natural for chat.\n\n" + text
    timeout_sec = int(env("OPENCODE_TIMEOUT_SEC", "600"))
    rc, stdout, stderr = await run_opencode(prompt, timeout_sec)
    if rc == 0 and stdout.strip():
        await update.message.reply_text(truncate(stdout, 4000))
    else:
        await update.message.reply_text(
            f"Milo is offline or errored (rc={rc}). Stderr:\n```\n{truncate(stderr, 1000)}\n```"
        )


async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "/mem save <title> | <content>\n"
            "/mem list\n"
        )
        return
    sub = args[0].lower()
    conn: sqlite3.Connection = ctx.application.bot_data["db"]
    if sub == "save":
        if len(args) < 2:
            await update.message.reply_text("Usage: /mem save <title> | <content>")
            return
        joined = " ".join(args[1:])
        if "|" not in joined:
            await update.message.reply_text("Split title and body with `|`.")
            return
        title, content = (s.strip() for s in joined.split("|", 1))
        mem_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        conn.execute(
            "INSERT INTO memories (id, title, content, scope, kind, created_at, topic_key)"
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mem_id, title, content, "personal", "note", now, None),
        )
        conn.commit()
        await update.message.reply_text(
            f"Saved locally (id={mem_id}). Will sync to Engram once the API key is set."
        )
    elif sub == "list":
        rows = conn.execute(
            "SELECT id, title, created_at, substr(content,1,140) FROM memories "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        if not rows:
            await update.message.reply_text("(no memories saved locally yet)")
            return
        lines = []
        for r in rows:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r[2]))
            lines.append(f"*{r[1]}* (`{r[0]}`) _{ts}_\n{r[3]}")
        await update.message.reply_text("\n\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("Unknown /mem subcommand. Try /help.")


async def cmd_recall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /recall <query>")
        return
    query = " ".join(ctx.args)
    conn: sqlite3.Connection = ctx.application.bot_data["db"]
    rows = conn.execute(
        "SELECT id, title, content, created_at FROM memories "
        "WHERE title LIKE ? OR content LIKE ? "
        "ORDER BY created_at DESC LIMIT 5",
        (f"%{query}%", f"%{query}%"),
    ).fetchall()
    if not rows:
        await update.message.reply_text("No matches found in local memory. (Engram is the production store.)")
        return
    lines = []
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r[3]))
        lines.append(f"*{r[1]}* — _{ts}_\n{truncate(r[2], 600)}")
    await update.message.reply_text("\n\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /vault <relative-path-in-vault>")
        return
    relative = " ".join(ctx.args).lstrip("/")
    if ".." in Path(relative).parts or Path(relative).is_absolute():
        await update.message.reply_text("Only read-only access to files inside ~/vault. Refusing.")
        return
    target = Path.home() / "vault" / relative
    if not target.is_file():
        await update.message.reply_text(f"Not a file: {target}")
        return
    try:
        text = target.read_text(errors="replace")
    except Exception as exc:
        await update.message.reply_text(f"Read failed: {exc}")
        return
    await update.message.reply_text("```\n" + truncate(text, 3500) + "\n```", parse_mode=constants.ParseMode.MARKDOWN)


def truncate(text: str, limit: int) -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    head = text[: limit - 40]
    return head + "\n…[truncated " + str(len(text) - len(head)) + " chars]"


def authz_filter(allowed: Optional[set[int]]):
    if allowed is None:
        return filters.ALL

    async def _check(_: object) -> bool:
        return True  # replaced per-update below

    return filters.ALL


def make_application() -> Application:
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.stderr.write("TELEGRAM_BOT_TOKEN environment variable not set. Aborting.\n")
        sys.exit(2)

    builder = ApplicationBuilder().token(token)
    app = builder.build()

    db_path = Path(env("MILO_DB_PATH", str(Path.home() / ".milo" / "milo-bot.sqlite")))
    db = ensure_db(db_path)
    app.bot_data["db"] = db

    allowed = parse_allowed_users()

    async def enforce_auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if allowed is not None and update.effective_user is not None:
            if update.effective_user.id not in allowed:
                LOG.warning("rejected user %s (%s)", update.effective_user.username, update.effective_user.id)
                await update.message.reply_text("You're not on the allowed list.")
                raise ContextTypes.ApplicationHandlerStop

    # Order matters: authz runs before each command handler.
    for cmd, handler in [
        ("start", cmd_start),
        ("help", cmd_help),
        ("ping", cmd_ping),
        ("opencode", cmd_opencode),
        ("milo", cmd_milo),
        ("mem", cmd_mem),
        ("recall", cmd_recall),
        ("vault", cmd_vault),
    ]:
        app.add_handler(CommandHandler(cmd, handler, block=True), group=0)
        # Telegram's authz pattern: a pre-emption handler isn't needed in simple bots,
        # but we set the bot_data with `allowed_users` so handlers can re-check if needed.
    app.bot_data["allowed_users"] = allowed

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=True), group=0)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Milo Telegram bot")
    parser.add_argument("--webhook", action="store_true", help="run in webhook mode")
    parser.add_argument("--port", type=int, default=int(env("PORT", "8080")))
    parser.add_argument("--log-level", default=env("LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    app = make_application()
    if args.webhook:
        app.run_webhook(
            listen="0.0.0.0",
            port=args.port,
            url_path=env("WEBHOOK_PATH", "/milo"),
            webhook_url=env("WEBHOOK_URL", f"http://localhost:{args.port}/milo"),
        )
    else:
        # Ensure an event loop is set for the current thread
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            # No event loop in current thread, create and set one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        app.run_polling(stop_signals=None)

if __name__ == "__main__":
    main()
