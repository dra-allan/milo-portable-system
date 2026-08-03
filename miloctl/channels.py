"""Outbound communication channels — how Milo reaches Allan.

``routines.py`` has always ended a run with::

    from .channels import send_telegram

...and that module never existed. The import sat inside a bare
``except Exception: pass``, so every routine with ``output: telegram`` — which
includes a *seeded built-in* — quietly delivered nothing. Nobody saw an error
because there was nothing to see. This module is that missing half.

Design notes
------------
The shape here is lifted from Hermes' ``gateway/platform_registry.py``: a
registry of self-describing adapters, so adding a channel never means editing
an ``if/elif`` chain somewhere else. What is *not* lifted is the machinery.
Hermes runs async adapters on ``python-telegram-bot``, ``discord.py`` and
friends; Milo's whole promise is that it installs on a Termux phone with no
compiler, so every adapter here is ``urllib`` and the standard library.

Three behaviours are ported deliberately, because each one is a bug Hermes
already paid for:

* **Chunking.** Every provider has a hard message ceiling (Telegram 4096).
  Over it, the API rejects the *whole* message — so a long routine digest
  vanishes entirely rather than arriving clipped.
* **Redaction.** Provider errors love to echo the request URL back at you,
  and for Telegram the bot token *is* in the URL. Errors go through
  ``env.scrub`` before they are logged or printed.
* **Never raise into a caller.** A routine must not fail because the network
  did. Adapters return a ``Delivery`` describing what happened.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from . import env

__all__ = [
    "Delivery",
    "Channel",
    "register",
    "get",
    "all_channels",
    "configured_channels",
    "send",
    "send_telegram",
    "chunk",
]

#: Timeout for a single outbound HTTP call. Long enough for a slow phone
#: network, short enough that a wedged routine still finishes this decade.
HTTP_TIMEOUT = 20

#: Retry only on transport-level failures and 5xx. A 400 from Telegram means
#: the message is malformed; sending it four more times just wastes the battery.
MAX_ATTEMPTS = 3


# ── result type ───────────────────────────────────────────────────────────────


@dataclass
class Delivery:
    """What happened when we tried to reach a channel.

    ``skipped`` is deliberately distinct from ``ok=False``. A channel the user
    never configured is not a failure — ``milo send`` should not print a scary
    red line because Allan has no Discord. Only a *configured* channel that
    refused counts as an error.
    """

    channel: str
    ok: bool = False
    detail: str = ""
    skipped: bool = False
    parts: int = 0

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        return "sent" if self.ok else "failed"

    def render(self) -> str:
        bits = f"{self.channel}: {self.status}"
        if self.parts > 1:
            bits += f" ({self.parts} parts)"
        if self.detail:
            bits += f" — {self.detail}"
        return bits


# ── chunking ──────────────────────────────────────────────────────────────────


def chunk(text: str, limit: int) -> List[str]:
    """Split ``text`` into pieces that each fit inside ``limit`` characters.

    Splits at the friendliest boundary available — paragraph, then line, then
    word — and only cuts mid-word when a single word genuinely exceeds the
    limit. The alternative (a naive ``text[i:i + limit]``) guillotines code
    blocks and URLs, which is exactly the kind of damage that makes a digest
    unreadable on a phone.
    """
    if limit <= 0:
        return [text] if text else []
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []

    out: List[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        # Prefer a paragraph break, then a newline, then a space. Only accept
        # a boundary in the back half, otherwise we emit a tiny runt chunk and
        # still have to hard-split the remainder anyway.
        cut = -1
        for sep in ("\n\n", "\n", " "):
            found = window.rfind(sep)
            if found > limit // 2:
                cut = found + (len(sep) if sep != " " else 0)
                break
        if cut <= 0:
            cut = limit  # one enormous unbroken token; cut it cleanly
        piece = rest[:cut].rstrip()
        if piece:
            out.append(piece)
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out


# ── http ──────────────────────────────────────────────────────────────────────


def _redact(text: str) -> str:
    """Strip anything secret out of a message we are about to surface.

    ``env.scrub`` masks known values from the .env; the extra regex catches a
    bot token embedded in an api.telegram.org URL, which is the single most
    common way a token escapes into a log file.
    """
    try:
        text = env.scrub(text or "")
    except Exception:
        text = text or ""
    return re.sub(r"/bot\d{6,}:[A-Za-z0-9_-]+", "/bot<redacted>", text)


def _post(url: str, payload: Optional[dict] = None, *,
          data: Optional[bytes] = None,
          headers: Optional[Dict[str, str]] = None,
          method: str = "POST") -> tuple:
    """One HTTP call. Returns ``(ok, detail, body)`` and never raises.

    Retries transport errors and 5xx with a capped exponential backoff — the
    same shape as Hermes' network retry, minus the escalation machinery that
    only makes sense for a long-lived gateway process.
    """
    body_bytes = data
    hdrs = dict(headers or {})
    if payload is not None and body_bytes is None:
        body_bytes = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("User-Agent", "milo/2.0")

    last = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return True, "", raw
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = ""
            last = _redact(f"HTTP {exc.code} {detail}".strip())
            # 4xx is our fault — the payload is wrong. Retrying cannot help.
            if exc.code < 500:
                return False, last, ""
        except urllib.error.URLError as exc:
            last = _redact(f"network error: {exc.reason}")
        except Exception as exc:  # pragma: no cover - defensive
            last = _redact(f"{type(exc).__name__}: {exc}")

        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2 ** (attempt - 1), 8))
    return False, last, ""


# ── channel base ──────────────────────────────────────────────────────────────


@dataclass
class Channel:
    """A place Milo can send a message.

    ``send_fn`` receives ``(channel, already_chunked_piece)`` and returns
    ``(ok, detail)``. Chunking, redaction and the configured/skipped dance are
    handled here so an adapter only has to describe one API call.
    """

    name: str
    label: str
    limit: int
    send_fn: Callable[[Channel, str], tuple]
    #: Env keys that must be non-empty for this channel to be usable.
    requires: Sequence[str] = field(default_factory=tuple)
    hint: str = ""

    def configured(self) -> bool:
        return all(env.get(k).strip() for k in self.requires)

    def missing(self) -> List[str]:
        return [k for k in self.requires if not env.get(k).strip()]

    def deliver(self, text: str) -> Delivery:
        text = (text or "").strip()
        if not text:
            return Delivery(self.name, ok=True, detail="nothing to send", parts=0)
        if not self.configured():
            return Delivery(self.name, skipped=True,
                            detail="not configured: " + ", ".join(self.missing()))

        pieces = chunk(text, self.limit)
        for index, piece in enumerate(pieces, 1):
            # A multi-part message is far easier to read with a counter, and it
            # makes a silently-dropped middle chunk obvious instead of invisible.
            body = piece if len(pieces) == 1 else f"{piece}\n\n({index}/{len(pieces)})"
            ok, detail = self.send_fn(self, body)
            if not ok:
                return Delivery(self.name, ok=False, detail=_redact(detail), parts=index)
        return Delivery(self.name, ok=True, parts=len(pieces))


# ── registry ──────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, Channel] = {}


def register(channel: Channel) -> Channel:
    _REGISTRY[channel.name] = channel
    return channel


def get(name: str) -> Optional[Channel]:
    return _REGISTRY.get((name or "").strip().lower())


def all_channels() -> List[Channel]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def configured_channels() -> List[Channel]:
    return [c for c in all_channels() if c.configured()]


# ── adapters ──────────────────────────────────────────────────────────────────


def api_url(method: str, token: str = "") -> str:
    """Telegram Bot API endpoint. Shared with :mod:`miloctl.bot`."""
    return f"https://api.telegram.org/bot{token or env.get('TELEGRAM_BOT_TOKEN')}/{method}"


def _telegram_send(ch: Channel, text: str) -> tuple:
    token = env.get("TELEGRAM_BOT_TOKEN").strip()
    chat_id = env.get("TELEGRAM_CHAT_ID").strip()
    ok, detail, raw = _post(api_url("sendMessage", token), {
        "chat_id": chat_id,
        "text": text,
        # Deliberately *not* Markdown. Telegram's parser rejects the whole
        # message on one unbalanced asterisk, and agent output is full of
        # stray underscores and half-open code fences. Plain text always
        # arrives; pretty formatting that sometimes 400s is worse than plain.
        "disable_web_page_preview": True,
    })
    if not ok:
        return False, detail
    # Telegram answers 200 with {"ok": false} for logical failures such as
    # "chat not found", so the HTTP status alone is not the answer.
    try:
        body = json.loads(raw)
    except Exception:
        return True, ""
    if not body.get("ok", True):
        return False, str(body.get("description") or "telegram rejected the message")
    return True, ""


def _discord_send(ch: Channel, text: str) -> tuple:
    ok, detail, _ = _post(env.get("DISCORD_WEBHOOK_URL").strip(), {"content": text})
    return ok, detail


def _slack_send(ch: Channel, text: str) -> tuple:
    ok, detail, raw = _post(env.get("SLACK_WEBHOOK_URL").strip(), {"text": text})
    # Slack webhooks answer with a bare "ok" body; anything else is a problem
    # even when the status code is 200.
    if ok and raw and raw.strip().lower() not in {"ok", ""}:
        return False, raw.strip()[:200]
    return ok, detail


def _ntfy_send(ch: Channel, text: str) -> tuple:
    """ntfy.sh — the least-friction push notification on a phone.

    No bot, no app registration: subscribe to a topic and messages arrive.
    Worth having as the fallback when Telegram is the thing that is broken.
    """
    topic = env.get("NTFY_TOPIC").strip()
    server = (env.get("NTFY_SERVER").strip() or "https://ntfy.sh").rstrip("/")
    headers = {"Content-Type": "text/plain; charset=utf-8",
               "Title": "Milo"}
    token = env.get("NTFY_TOKEN").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    ok, detail, _ = _post(f"{server}/{urllib.parse.quote(topic)}",
                          data=text.encode("utf-8"), headers=headers)
    return ok, detail


def _webhook_send(ch: Channel, text: str) -> tuple:
    """Generic JSON webhook — the escape hatch.

    Hermes exposes ``outbound_webhooks`` for the same reason: whatever Allan
    wires up next (n8n, Home Assistant, a Cloudflare Worker) should not need a
    new adapter in this file.
    """
    ok, detail, _ = _post(env.get("MILO_WEBHOOK_URL").strip(),
                          {"text": text, "source": "milo"})
    return ok, detail


def _log_send(ch: Channel, text: str) -> tuple:
    """Always-available local sink.

    Guarantees ``milo send`` does something useful on a fresh machine with no
    secrets at all, and gives routines a delivery target that cannot fail.
    """
    from . import paths

    try:
        paths.ensure(paths.logs_dir())
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(paths.logs_dir() / "channels.log", "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {text}\n")
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


register(Channel(
    name="telegram", label="Telegram", limit=4096, send_fn=_telegram_send,
    requires=("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
    hint="create a bot with @BotFather, get your chat id from @userinfobot",
))
register(Channel(
    name="discord", label="Discord", limit=2000, send_fn=_discord_send,
    requires=("DISCORD_WEBHOOK_URL",),
    hint="Server Settings → Integrations → Webhooks → New Webhook",
))
register(Channel(
    name="slack", label="Slack", limit=3000, send_fn=_slack_send,
    requires=("SLACK_WEBHOOK_URL",),
    hint="https://api.slack.com/messaging/webhooks",
))
register(Channel(
    name="ntfy", label="ntfy", limit=4000, send_fn=_ntfy_send,
    requires=("NTFY_TOPIC",),
    hint="pick any topic name, subscribe to it in the ntfy app",
))
register(Channel(
    name="webhook", label="Webhook", limit=100000, send_fn=_webhook_send,
    requires=("MILO_WEBHOOK_URL",),
    hint="any URL that accepts a JSON POST",
))
register(Channel(
    name="log", label="Local log", limit=100000, send_fn=_log_send,
    requires=(), hint="always available",
))


# ── public helpers ────────────────────────────────────────────────────────────


def send(text: str, channels: Optional[Sequence[str]] = None) -> List[Delivery]:
    """Send ``text`` to the named channels, or to every configured one.

    Bare ``milo send`` reaching everything at once would be a surprising way to
    spam three services, so the no-argument case targets only what is actually
    set up — and an unconfigured channel comes back ``skipped``, not failed.
    """
    if channels:
        targets = []
        for raw in channels:
            for part in str(raw).replace(",", " ").split():
                ch = get(part)
                targets.append(ch if ch else Channel(
                    name=part, label=part, limit=1, send_fn=lambda c, t: (False, "unknown channel"),
                ))
    else:
        targets = [c for c in configured_channels() if c.name != "log"] or [get("log")]
    return [t.deliver(text) for t in targets if t]


def send_telegram(text: str) -> Delivery:
    """The function ``routines.py`` has been importing all along."""
    ch = get("telegram")
    return ch.deliver(text) if ch else Delivery("telegram", ok=False, detail="no adapter")
