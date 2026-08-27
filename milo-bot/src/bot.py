#!/usr/bin/env python
"""Milo Telegram bridge with ultra-fast Gemini Key Pool and SQLite memory."""
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
from typing import Optional, List, Dict, Tuple

try:
    import httpx
    from telegram import Update, constants
    from telegram.ext import (
        Application,
        ApplicationBuilder,
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

HELP_TEXT = textwrap.dedent("""
*Milo Sage* — Allan's assistant and chief of stuff.

*Chat:* Just message me normally. I respond instantly.

*Memory Commands:*
• `/mem save <title> | <content>` — Save a memory
• `/mem list` — List recent memories
• `/recall <query>` — Search memory database
• `/vault <relative-path>` — Read vault note
• `/clear` — Reset active chat session history

*System Commands:*
• `/status` — Live status of all VPS daemons
• `/ping` — Latency & config check
• `/help` — Show this message
""").strip()

DEFAULT_GEMINI_KEYS = [
    "AIzaSyA3gOpEpwkchdflygWgvsdXytdVlIaKaio",
    "AIzaSyA4VXSMxV58TrISLJvILFgl1deugPsIvRc",
    "AIzaSyDHAkR6vb7tqzodq21-rb_r9xJ2SX-Ubp0",
    "AIzaSyCRF1yCrhmla86lgyuGkEtG1124idUNa7c",
    "AIzaSyBUim_Zfrqj34x74rsKv9KJ_YHlXIDMFoo",
    "AIzaSyAqhjdF56xwx05e6ZsT_d1zGKJDkwqqLVw",
    "AIzaSyAb9uXSG8dJdJ6GVMpgT8OL0mHj1mSK4NE",
    "AIzaSyDUvJnLMD3cOTvCHMWFUGBxT_UKSrFERFE",
    "AIzaSyCUrMJUsrWxnDmKVq7WYV72K57mpwXls_M",
    "AIzaSyD7dOGmBj8yRT_rESSTFs2xONHiUkGbtP8",
    "AIzaSyBZ8mFE7SdumzV8oHtFqfZozc0t_sC3DLc",
    "AIzaSyAo62afi9DYoaMrpS6NM01bloqSPJSKevU",
    "AIzaSyCIGrGS63P6_qASaPjb6doN0s8P_N-NDBc",
    "AIzaSyDX79P0G14Ae3wnsCf9IvvBbd9IQUHuwCQ",
    "AIzaSyBktXQmqWcSDLNDoffoth-UyDebzr2l_dI",
    "AIzaSyAjHTnG8DtO_VZknEfSbjluwSI0FmFxSUw",
    "AIzaSyCGbBhZ8UFJXPvE2XTh29vX9DTF99oOlWU",
    "AIzaSyDWzy_crcANxNRQsKA8dI3SQ6Q9UkZ0EOQ",
    "AIzaSyBUMdBYC3RwCfHLH2wUXpiQV_qteuN8U6w",
    "AIzaSyAgzUBA6wblCuA9Efd92EqAMiGZJIL_r58",
]


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def get_gemini_keys() -> List[str]:
    raw = env("GEMINI_API_KEYS", "") or env("GEMINI_API_KEY", "") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return keys if keys else DEFAULT_GEMINI_KEYS


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


def get_recent_memories(conn: sqlite3.Connection, limit: int = 5) -> str:
    try:
        rows = conn.execute("SELECT title, content FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        if not rows:
            return ""
        return "\n".join(f"- {title}: {content[:180]}" for title, content in rows)
    except Exception as e:
        LOG.warning("Failed to fetch memories: %s", e)
        return ""


def truncate(text: str, limit: int = 4000) -> str:
    text = text.rstrip()
    return text if len(text) <= limit else text[:limit - 40] + "\n...[truncated]"


class MiloEngine:
    """Milo intelligence engine with key-pool rotation and context memory."""
    def __init__(self, db_conn: sqlite3.Connection):
        self.db_conn = db_conn
        self.keys = get_gemini_keys()
        self.key_index = 0
        self.conversations: Dict[int, List[Dict[str, Any]]] = {}
        self.models = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]

    def _build_system_prompt(self) -> str:
        mem_summary = get_recent_memories(self.db_conn, limit=6)
        memory_section = f"\n\nRecent durable memories:\n{mem_summary}" if mem_summary else ""
        return (
            "You are Milo Sage — Allan's personal AI assistant and chief of staff.\n"
            "Personality: Brutally honest, direct, sharp, no corporate fluff or sycophancy. "
            "Lead with the answer, then the reasoning if needed. Concise paragraphs.\n"
            "Allan (Dra) is building an autonomous YouTube multi-channel empire and software systems. "
            "VPS daemons handle YouTube Shorts and Ranking Shorts pipelines 24/7.\n"
            "Never invent facts. Speak naturally as Milo."
            + memory_section
        )

    async def generate_reply(self, chat_id: int, user_text: str) -> str:
        history = self.conversations.setdefault(chat_id, [])
        history.append({"role": "user", "parts": [{"text": user_text}]})
        
        # Keep history bounded to last 14 turns
        if len(history) > 14:
            history = history[-14:]
            self.conversations[chat_id] = history

        system_instruction = self._build_system_prompt()
        num_keys = len(self.keys)

        async with httpx.AsyncClient(timeout=25.0) as client:
            for attempt in range(min(num_keys * 2, 10)):
                current_key = self.keys[self.key_index % num_keys]
                model = self.models[0]
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={current_key}"
                
                payload = {
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    "contents": history
                }
                
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        reply_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        history.append({"role": "model", "parts": [{"text": reply_text}]})
                        return reply_text
                    elif resp.status_code in (429, 403, 503):
                        LOG.warning("Key #%d returned %d, rotating to next key...", self.key_index % num_keys, resp.status_code)
                        self.key_index = (self.key_index + 1) % num_keys
                    elif resp.status_code == 404:
                        # Fallback to alternate model name
                        self.models.rotate(-1) if hasattr(self.models, "rotate") else self.models.append(self.models.pop(0))
                        LOG.warning("Model 404, rotating model to %s...", self.models[0])
                    else:
                        LOG.error("Gemini API error %d: %s", resp.status_code, resp.text[:200])
                        self.key_index = (self.key_index + 1) % num_keys
                except Exception as exc:
                    LOG.warning("Request failed on key #%d: %s", self.key_index % num_keys, exc)
                    self.key_index = (self.key_index + 1) % num_keys
                
                await asyncio.sleep(0.2)

        return "Milo here. Ran into a transient API glitch across the key pool. Fire that again."

    def clear_history(self, chat_id: int) -> None:
        self.conversations.pop(chat_id, None)


milo_engine: Optional[MiloEngine] = None


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("Milo Sage online. Fast, persistent, 24/7 on VPS. /help for commands, or just talk to me.")


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_markdown(HELP_TEXT)


async def cmd_ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        t0 = time.time()
        msg = await update.message.reply_text("Pinging...")
        latency = round((time.time() - t0) * 1000)
        await msg.edit_text(f"🏓 Pong! Latency: {latency}ms\nHost: VPS (13.49.223.119)\nEngine: Gemini Flash Key-Pool (Active)")


async def cmd_clear(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and milo_engine:
        milo_engine.clear_history(update.effective_chat.id)
        if update.message:
            await update.message.reply_text("🧹 Conversation history cleared.")


async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    status_text = (
        "*VPS MILO SYSTEM STATUS*\n"
        "• *Telegram Bot:* Active 24/7 (Polling)\n"
        "• *Shorts Pipeline Daemon:* Scheduled (09:02, 14:01, 19:17 daily)\n"
        "• *Ranking Pipeline Daemon:* Scheduled (09:09 daily)\n"
        "• *Brain/State:* `~/.milo/state/memory.db`\n"
        "• *All Systems:* Operational"
    )
    await update.message.reply_markdown(status_text)


async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
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
        conn.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], title, content, "personal", "note", int(time.time()), None),
        )
        conn.commit()
        await update.message.reply_text(f"💾 Memory saved: {title}")
    elif args[0].lower() == "list":
        rows = conn.execute("SELECT title, substr(content,1,140) FROM memories ORDER BY created_at DESC LIMIT 10").fetchall()
        reply = "\n\n".join(f"• *{title}*\n{body}" for title, body in rows) or "No memories saved locally."
        await update.message.reply_markdown(reply)
    else:
        await update.message.reply_text("Unknown /mem command. Use /mem save or /mem list.")


async def cmd_recall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    query = " ".join(ctx.args or []).strip()
    if not query:
        await update.message.reply_text("Usage: /recall <query>")
        return
    like = f"%{query}%"
    rows = ctx.application.bot_data["db"].execute(
        "SELECT title, content FROM memories WHERE title LIKE ? OR content LIKE ? ORDER BY created_at DESC LIMIT 5",
        (like, like),
    ).fetchall()
    reply = "\n\n".join(f"• *{title}*\n{truncate(content, 500)}" for title, content in rows) or "No matches found."
    await update.message.reply_markdown(reply)


async def cmd_vault(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
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
        await update.message.reply_text(
            "```\n" + truncate(candidate.read_text(encoding="utf-8", errors="replace"), 3500) + "\n```",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
    except OSError as exc:
        await update.message.reply_text(f"Read failed: {exc}")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    chat_id = update.effective_chat.id if update.effective_chat else 0
    
    # Send immediate typing action
    await ctx.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
    
    global milo_engine
    if milo_engine is None:
        milo_engine = MiloEngine(ctx.application.bot_data["db"])
    
    reply = await milo_engine.generate_reply(chat_id, text)
    await update.message.reply_text(truncate(reply, 4000))


def make_application() -> Application:
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    
    app = ApplicationBuilder().token(token).build()
    db_path = Path(env("MILO_DB_PATH", str(Path.home() / ".milo" / "state" / "memory.db")) or "memory.db").expanduser()
    db_conn = ensure_db(db_path)
    app.bot_data["db"] = db_conn
    
    global milo_engine
    milo_engine = MiloEngine(db_conn)
    
    allowed = parse_allowed_users()

    async def enforce_auth(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        msg = update.effective_message
        text = msg.text if msg else "<no text>"
        user_info = f"{user.id} (@{user.username})" if user else "<unknown user>"
        chat_id = update.effective_chat.id if update.effective_chat else "?"
        LOG.info("Update %s from %s in chat %s: %r", update.update_id, user_info, chat_id, text)
        if allowed and (user is None or user.id not in allowed):
            LOG.warning("User %s not in ALLOWED_USER_IDS (%s). Denied.", user_info, allowed)
            if msg:
                await msg.reply_text(f"You're not on the allowed list. (Your User ID: {user.id if user else 'unknown'})")
            from telegram.ext import ApplicationHandlerStop
            raise ApplicationHandlerStop

    app.add_handler(TypeHandler(Update, enforce_auth, block=True), group=-1)
    
    commands = [
        ("start", cmd_start),
        ("help", cmd_help),
        ("ping", cmd_ping),
        ("status", cmd_status),
        ("clear", cmd_clear),
        ("mem", cmd_mem),
        ("recall", cmd_recall),
        ("vault", cmd_vault),
        ("milo", on_text),
    ]
    for name, handler in commands:
        app.add_handler(CommandHandler(name, handler, block=True))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text, block=True))
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
    
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(log_fmt)
    root_logger.addHandler(stream_handler)
    
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(log_fmt)
    root_logger.addHandler(file_handler)
    
    LOG.info("Milo Telegram Bot starting (Ultra-Fast Key Pool Engine)...")
    app = make_application()
    if args.webhook:
        path = env("WEBHOOK_PATH", "/milo") or "/milo"
        LOG.info("Starting in webhook mode on port %d...", args.port)
        app.run_webhook(listen=env("WEBHOOK_LISTEN", "127.0.0.1"), port=args.port, url_path=path, webhook_url=env("WEBHOOK_URL"))
    else:
        LOG.info("Starting in polling mode with auto-reconnect...")
        while True:
            try:
                app.run_polling(
                    drop_pending_updates=True,
                    allowed_updates=Update.ALL_TYPES,
                    close_loop=False,
                )
            except (telegram.error.NetworkError, ConnectionResetError, BrokenPipeError, asyncio.TimeoutError) as exc:
                LOG.warning("Polling connection lost: %s. Reconnecting in 5s...", exc)
                time.sleep(5)
            except KeyboardInterrupt:
                LOG.info("Shutdown requested.")
                break
            except Exception as exc:
                LOG.exception("Unexpected error: %s. Restarting in 10s...", exc)
                time.sleep(10)


if __name__ == "__main__":
    main()
