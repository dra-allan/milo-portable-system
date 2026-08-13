#!/usr/bin/env python
"""Milo Telegram bridge with portable paths and fail-closed authorization."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Optional

try:
    from telegram import Update, constants
    from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters
except ImportError as exc:
    sys.stderr.write(f"python-telegram-bot missing: {exc}\n")
    raise SystemExit(1)

LOG = logging.getLogger("milo.bot")
HELP_TEXT = textwrap.dedent("""
*Milo* - assistant + opencode bridge.

Commands: /opencode <prompt>, /milo <text>, /mem save <title> | <content>,
/mem list, /recall <query>, /vault <relative-path>, /ping, /help.
""").strip()


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def parse_allowed_users() -> set[int]:
    raw = env("ALLOWED_USER_IDS", "") or ""
    allowed: set[int] = set()
    for part in raw.split(","):
        if part.strip():
            try:
                allowed.add(int(part.strip()))
            except ValueError:
                LOG.warning("Ignoring malformed ALLOWED_USER_IDS entry")
    return allowed


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.execute("""CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
        scope TEXT, kind TEXT, created_at INTEGER NOT NULL, topic_key TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)")
    conn.commit()
    return conn


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Milo online. /help for commands, or just talk to me.")


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown(HELP_TEXT)


async def cmd_ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = {"opencode_bin": env("OPENCODE_BIN", "opencode"), "workdir": str(workdir()), "agent": env("MILO_AGENT", "milo"), "model": env("OPENCODE_MODEL", "(default)")}
    await update.message.reply_text(json.dumps(cfg, indent=2))


def workdir() -> Path:
    configured = env("OPENCODE_WORKDIR") or env("MILO_WORKSPACE") or str(Path.cwd())
    path = Path(configured).expanduser()
    if not path.is_dir():
        raise RuntimeError(f"opencode workdir does not exist: {path}")
    return path


def timeout_seconds() -> int:
    try:
        return max(1, int(env("OPENCODE_TIMEOUT_SEC", "600") or "600"))
    except ValueError:
        return 600


async def run_opencode(prompt: str) -> tuple[int, str, str]:
    cmd = [env("OPENCODE_BIN", "opencode") or "opencode", "run", "--agent", env("MILO_AGENT", "milo") or "milo", "--auto"]
    model = env("OPENCODE_MODEL", "")
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=workdir(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds())
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except (RuntimeError, OSError) as exc:
        return 126, "", str(exc)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"opencode timed out after {timeout_seconds()}s"


async def run_prompt(update: Update, prompt: str) -> None:
    rc, stdout, stderr = await run_opencode(prompt)
    if rc == 0 and stdout.strip():
        await update.message.reply_text(truncate(stdout, 4000))
    else:
        await update.message.reply_text(f"Milo error (rc={rc}).\n{truncate(stderr, 1200)}")


async def cmd_opencode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /opencode <prompt>")
        return
    await update.message.reply_text(f"Running opencode... (timeout {timeout_seconds()}s)")
    await run_prompt(update, " ".join(ctx.args))


async def cmd_milo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /milo <text>")
        return
    await run_prompt(update, "Respond as Milo; keep it tight and natural for chat.\n\n" + " ".join(ctx.args))


async def on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text if update.message else ""
    if text:
        await run_prompt(update, "Respond as Milo; keep it tight and natural for chat.\n\n" + text)


async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args or []
    if not args:
        await update.message.reply_text("Usage: /mem save <title> | <content>\n/mem list")
        return
    conn = ctx.application.bot_data["db"]
    if args[0].lower() == "save":
        joined = " ".join(args[1:])
        if "|" not in joined:
            await update.message.reply_text("Split title and body with |.")
            return
        title, content = (s.strip() for s in joined.split("|", 1))
        conn.execute("INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex[:12], title, content, "personal", "note", int(time.time()), None))
        conn.commit()
        await update.message.reply_text("Saved locally.")
    elif args[0].lower() == "list":
        rows = conn.execute("SELECT title, substr(content,1,140) FROM memories ORDER BY created_at DESC LIMIT 10").fetchall()
        await update.message.reply_text("\n\n".join(f"{title}\n{body}" for title, body in rows) or "No memories saved locally.")
    else:
        await update.message.reply_text("Unknown /mem command.")


async def cmd_recall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(ctx.args or []).strip()
    if not query:
        await update.message.reply_text("Usage: /recall <query>")
        return
    like = f"%{query}%"
    rows = ctx.application.bot_data["db"].execute("SELECT title, content FROM memories WHERE title LIKE ? OR content LIKE ? ORDER BY created_at DESC LIMIT 5", (like, like)).fetchall()
    await update.message.reply_text("\n\n".join(f"{title}\n{truncate(content, 600)}" for title, content in rows) or "No matches found.")


async def cmd_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    relative = " ".join(ctx.args or []).strip()
    if not relative:
        await update.message.reply_text("Usage: /vault <relative-path-in-vault>")
        return
    root = Path(env("MILO_VAULT_DIR", str(Path.home() / "vault")) or str(Path.home() / "vault")).expanduser().resolve()
    candidate = (root / relative.lstrip("/\\")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        await update.message.reply_text("Refusing access outside the vault.")
        return
    if not candidate.is_file() or candidate.is_symlink():
        await update.message.reply_text("Not a regular vault file.")
        return
    try:
        await update.message.reply_text("```\n" + truncate(candidate.read_text(encoding="utf-8", errors="replace"), 3500) + "\n```", parse_mode=constants.ParseMode.MARKDOWN)
    except OSError as exc:
        await update.message.reply_text(f"Read failed: {exc}")


def truncate(text: str, limit: int) -> str:
    text = text.rstrip()
    return text if len(text) <= limit else text[:limit - 40] + "\n...[truncated]"


def make_application() -> Application:
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    app = ApplicationBuilder().token(token).build()
    db_path = Path(env("MILO_DB_PATH", str(Path.home() / ".milo" / "state" / "memory.db")) or "memory.db").expanduser()
    app.bot_data["db"] = ensure_db(db_path)
    allowed = parse_allowed_users()

    async def enforce_auth(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if allowed and (user is None or user.id not in allowed):
            if update.effective_message:
                await update.effective_message.reply_text("You're not on the allowed list.")
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, enforce_auth, block=True), group=-1)
    for name, handler in [("start", cmd_start), ("help", cmd_help), ("ping", cmd_ping), ("opencode", cmd_opencode), ("milo", cmd_milo), ("mem", cmd_mem), ("recall", cmd_recall), ("vault", cmd_vault)]:
        app.add_handler(CommandHandler(name, handler, block=True))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=True))
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook", action="store_true")
    parser.add_argument("--port", type=int, default=int(env("PORT", "8080") or "8080"))
    parser.add_argument("--log-level", default=env("LOG_LEVEL", "INFO"))
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    app = make_application()
    if args.webhook:
        path = env("WEBHOOK_PATH", "/milo") or "/milo"
        app.run_webhook(listen=env("WEBHOOK_LISTEN", "127.0.0.1"), port=args.port, url_path=path, webhook_url=env("WEBHOOK_URL"))
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
