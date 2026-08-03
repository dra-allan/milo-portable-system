"""The Telegram bot — Milo's inbound channel.

``channels.py`` is one-way: Milo talks, Allan reads. This is the other
direction, and it is the surface that actually gets used, because it is the
one that works from a phone on the bus.

Why not python-telegram-bot
---------------------------
Hermes drives Telegram through ``python-telegram-bot`` — async, batteries
included, and a hard dependency chain. Milo's pyproject keeps ``dependencies``
deliberately empty so that ``pip install .`` succeeds on a Termux phone with no
compiler. The Bot API is plain HTTPS + JSON, so long polling is perfectly
reachable from ``urllib``. The bot therefore runs with **zero** third-party
packages: ``python-telegram-bot`` stays an optional extra nobody needs.

Ported from Hermes, because each one is a bug it already paid for
-----------------------------------------------------------------
* **Fail closed** (Hermes #24457). An empty allowlist denies everyone. The
  tempting default — "no allowlist configured, so allow all" — turns a bot
  token leak into a stranger with shell access to Allan's machine.
* **Bots are not users** (Hermes #32188). ``from_user.is_bot`` is rejected
  regardless of the allowlist, so another bot in a shared group cannot drive
  this one, and two Milos can't talk each other into a loop.
* **Channel posts have no ``from_user``.** Authorizing only ``from_user``
  means a channel post sails straight past the allowlist. The sender chat is
  authorized instead, and an unidentifiable message is denied.
* **409 Conflict is fatal, not transient.** Telegram allows exactly one
  ``getUpdates`` poller per token. A second one makes both flap forever,
  each stealing updates from the other. We stop with an explanation rather
  than fight a war we cannot win.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from . import channels, env, ui

__all__ = ["Authorizer", "TelegramBot", "run"]

#: Seconds Telegram holds an empty getUpdates open before replying. Long
#: polling is what keeps a phone-side bot responsive without hammering the API.
POLL_TIMEOUT = 25

#: Cap on the reconnect backoff. Beyond a minute the bot feels dead.
MAX_BACKOFF = 60


class ConflictError(RuntimeError):
    """Another process is polling this bot token."""


# ── authorization ─────────────────────────────────────────────────────────────


@dataclass
class Authorizer:
    """Decides whether a Telegram update is allowed to drive Milo.

    Kept as a separate, dependency-free object so the policy can be tested
    directly against plain dicts — no network, no bot, no Telegram library.
    That matters: this is the only code in Milo where a mistake hands a
    stranger the agent.
    """

    allowed: Set[str] = field(default_factory=set)
    allow_bots: bool = False

    @classmethod
    def from_env(cls) -> Authorizer:
        """Build the allowlist from ``.env``.

        ``ALLOWED_USER_IDS`` is the real control. ``TELEGRAM_CHAT_ID`` is
        accepted as an implicit fallback *only* when it is positive: a positive
        chat id in a DM is the owner's own user id, so requiring Allan to paste
        the same number into two variables is pointless friction. Negative ids
        are groups and channels — never a user identity — so they are ignored
        here rather than silently widening the allowlist to a whole group.
        """
        allowed = {
            part.strip()
            for part in env.get("ALLOWED_USER_IDS").replace(",", " ").split()
            if part.strip()
        }
        if not allowed:
            chat = env.get("TELEGRAM_CHAT_ID").strip()
            if chat.lstrip("-").isdigit() and not chat.startswith("-"):
                allowed = {chat}
        return cls(allowed=allowed,
                   allow_bots=env.get("TELEGRAM_ALLOW_BOTS").strip().lower()
                   in {"1", "true", "yes"})

    def check(self, message: Dict[str, Any]) -> Tuple[bool, str]:
        """``(allowed, reason)`` for one Telegram message object."""
        if not self.allowed:
            # Fail closed. Without this, anyone who discovers the bot could
            # talk to it the moment a token leaks.
            return False, ("no allowlist configured — set ALLOWED_USER_IDS "
                           "(or TELEGRAM_CHAT_ID) to your Telegram user id")

        user = message.get("from") or {}
        user_id = str(user.get("id") or "").strip()

        if user_id and user.get("is_bot") and not self.allow_bots:
            return False, f"bot accounts are not allowed (id {user_id})"

        if not user_id:
            # Channel posts carry no "from". Authorize the sending chat so a
            # channel cannot inject work by simply having no user attached.
            sender = message.get("sender_chat") or {}
            user_id = str(sender.get("id") or "").strip()
            if not user_id:
                return False, "message has no identifiable sender"

        if "*" in self.allowed:
            return True, ""
        if user_id in self.allowed:
            return True, ""
        return False, f"user {user_id} is not on the allowlist"


# ── the bot ───────────────────────────────────────────────────────────────────


class TelegramBot:
    """A long-polling Telegram bot built on the standard library."""

    def __init__(self, token: str = "", *, authorizer: Optional[Authorizer] = None,
                 poll_timeout: int = POLL_TIMEOUT) -> None:
        self.token = (token or env.get("TELEGRAM_BOT_TOKEN")).strip()
        self.auth = authorizer or Authorizer.from_env()
        self.poll_timeout = poll_timeout
        self.offset = 0
        self.running = False
        self._me: Dict[str, Any] = {}

    # -- transport ------------------------------------------------------------

    def call(self, method: str, params: Optional[dict] = None,
             *, timeout: Optional[int] = None) -> Dict[str, Any]:
        """One Bot API call. Raises :class:`ConflictError` on 409."""
        url = channels.api_url(method, self.token)
        body = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "milo/2.0"},
        )
        try:
            with urllib.request.urlopen(
                req, timeout=timeout or (self.poll_timeout + 10)
            ) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            if exc.code == 409:
                raise ConflictError(channels._redact(raw or "conflict")) from None
            if exc.code == 401:
                raise RuntimeError("Telegram rejected the bot token (401)") from None
            return {"ok": False, "description": channels._redact(f"HTTP {exc.code} {raw}"[:300])}

    def me(self) -> Dict[str, Any]:
        if not self._me:
            self._me = (self.call("getMe", timeout=15) or {}).get("result") or {}
        return self._me

    def send(self, chat_id: Any, text: str,
             reply_to: Optional[int] = None) -> None:
        """Reply, chunked to Telegram's 4096-character ceiling."""
        for piece in channels.chunk(text or "…", 4096):
            params: Dict[str, Any] = {
                "chat_id": chat_id, "text": piece,
                "disable_web_page_preview": True,
            }
            if reply_to:
                params["reply_to_message_id"] = reply_to
                # A reply to a message deleted mid-flight would otherwise fail
                # the whole send; Telegram drops the linkage instead.
                params["allow_sending_without_reply"] = True
            try:
                self.call("sendMessage", params, timeout=20)
            except Exception:
                # Losing a reply must never kill the poll loop.
                pass
            reply_to = None  # only the first chunk quotes the original

    # -- update plumbing ------------------------------------------------------

    @staticmethod
    def extract(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The message inside an update, whatever shape it arrived in."""
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            msg = update.get(key)
            if isinstance(msg, dict):
                return msg
        return None

    def poll_once(self) -> List[Dict[str, Any]]:
        resp = self.call("getUpdates", {
            "offset": self.offset,
            "timeout": self.poll_timeout,
            # Media needs a download path we do not have yet; asking only for
            # what we can act on keeps unread photos from blocking the queue.
            "allowed_updates": ["message", "edited_message", "channel_post"],
        })
        if not resp.get("ok", False):
            return []
        updates = resp.get("result") or []
        for upd in updates:
            self.offset = max(self.offset, int(upd.get("update_id", 0)) + 1)
        return updates

    def handle(self, update: Dict[str, Any]) -> None:
        message = self.extract(update)
        if not message:
            return
        chat_id = ((message.get("chat") or {}).get("id"))
        text = (message.get("text") or message.get("caption") or "").strip()

        ok, reason = self.auth.check(message)
        if not ok:
            ui.warn(f"denied: {reason}")
            # Answer only when we know who to answer *and* the allowlist exists.
            # Replying to strangers turns the bot into a probe oracle that
            # confirms the token is live.
            if chat_id and self.auth.allowed:
                self.send(chat_id, "Not authorised.")
            return
        if not text:
            return

        reply = self.respond(text, message)
        if reply:
            self.send(chat_id, reply, reply_to=message.get("message_id"))

    # -- behaviour ------------------------------------------------------------

    def respond(self, text: str, message: Dict[str, Any]) -> str:
        """Turn an inbound message into Milo's reply."""
        if text.startswith("/"):
            raw = text.split(maxsplit=1)
            # Group commands arrive as "/cmd@MiloBot" — strip the mention.
            cmd = raw[0][1:].split("@", 1)[0].lower()
            rest = raw[1].strip() if len(raw) > 1 else ""
            handler = getattr(self, f"do_{cmd}", None)
            if handler:
                try:
                    return handler(rest, message)
                except Exception as exc:
                    return channels._redact(f"that failed: {type(exc).__name__}: {exc}")
            return f"unknown command /{cmd} — try /help"
        return self.ask_agent(text)

    def do_start(self, rest: str, message: Dict[str, Any]) -> str:
        from . import naming

        return (f"{naming.display_name()} here. Talk to me normally, "
                f"or use /help for the shortcuts.")

    def do_help(self, rest: str, message: Dict[str, Any]) -> str:
        return (
            "/remember <text> — save something durable\n"
            "/recall <query> — search memory\n"
            "/status — health check\n"
            "/whoami — your Telegram id\n"
            "/help — this\n\n"
            "Anything else goes to the agent."
        )

    def do_whoami(self, rest: str, message: Dict[str, Any]) -> str:
        user = message.get("from") or message.get("sender_chat") or {}
        return (f"id: {user.get('id')}\n"
                f"chat: {(message.get('chat') or {}).get('id')}")

    def do_remember(self, rest: str, message: Dict[str, Any]) -> str:
        if not rest:
            return "remember what? /remember the copier code is 4471"
        from . import memory

        mem = memory.remember(rest, category="note", tags=["telegram"])
        return f"saved [{getattr(mem, 'id', '?')}]"

    def do_recall(self, rest: str, message: Dict[str, Any]) -> str:
        if not rest:
            return "recall what?"
        from . import memory

        hits = memory.recall(rest, limit=8)
        if not hits:
            return "nothing on that."
        return "\n\n".join(f"• {getattr(h, 'content', '')}".strip() for h in hits)

    def do_status(self, rest: str, message: Dict[str, Any]) -> str:
        lines = [f"{c.label}: {'ready' if c.configured() else 'not configured'}"
                 for c in channels.all_channels()]
        return "channels\n" + "\n".join(lines)

    def ask_agent(self, text: str) -> str:
        """Hand the message to whichever agent runtime is installed."""
        from . import harness

        runnable = [h for h in harness.detect_installed() if h.which()]
        if not runnable:
            return ("No agent runtime on this machine, so I can only do the "
                    "built-in commands — /help. (Memory still works.)")
        code, out = runnable[0].run(text)
        out = (out or "").strip()
        if code != 0 and not out:
            return "the agent exited without saying anything"
        return out or "(no output)"

    # -- loop -----------------------------------------------------------------

    def run_forever(self, *, max_iterations: int = 0) -> int:
        """Poll until interrupted. ``max_iterations`` bounds it for tests."""
        if not self.token:
            ui.err("TELEGRAM_BOT_TOKEN is not set")
            ui.say(ui.dim("  set it:  milo install --only TELEGRAM_BOT_TOKEN"))
            ui.say(ui.dim("  get one: message @BotFather on Telegram"))
            return 1
        if not self.auth.allowed:
            # Refuse to start rather than run a bot that denies every message
            # and looks broken.
            ui.err("no allowlist — the bot would deny every message")
            ui.say(ui.dim("  set it:  milo install --only ALLOWED_USER_IDS"))
            ui.say(ui.dim("  your id: message @userinfobot on Telegram"))
            return 1

        who = self.me().get("username")
        ui.ok(f"bot online as @{who}" if who else "bot online")
        ui.say(ui.dim(f"  allowlist: {', '.join(sorted(self.auth.allowed))}"))

        self.running = True
        failures = 0
        iterations = 0
        try:
            while self.running:
                if max_iterations and iterations >= max_iterations:
                    break
                iterations += 1
                try:
                    for update in self.poll_once():
                        self.handle(update)
                    failures = 0
                except ConflictError:
                    ui.err("another Milo bot is already polling this token")
                    ui.say(ui.dim("  stop the other process first — only one poller per token"))
                    return 1
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    failures += 1
                    delay = min(2 ** min(failures, 6), MAX_BACKOFF)
                    ui.warn(channels._redact(f"poll failed ({exc}); retrying in {delay}s"))
                    time.sleep(delay)
        except KeyboardInterrupt:
            ui.say()
            ui.info("bot stopped")
        return 0


def run(**kwargs: Any) -> int:
    return TelegramBot().run_forever(**kwargs)
