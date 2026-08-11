#!/usr/bin/env python3
"""
notify.py - one place that tells a human what the pipeline is doing.
====================================================================

Every stage boundary calls the same callable::

    notify(event: str, message: str) -> None

which is exactly the ``Notify`` type ``agent_runner`` already accepts, so the
locked M1 dispatch code needed no changes to gain notifications.

Configuration
-------------
``config/notify.env`` (untracked; copy it from ``notify.env.template``)::

    TELEGRAM_BOT_TOKEN={{TELEGRAM_BOT_TOKEN}}
    TELEGRAM_CHAT_ID={{TELEGRAM_CHAT_ID}}

Values may be literal, or ``{{PLACEHOLDER}}`` referring to an environment
variable of the same name (the deploy pattern: the file stays a template and
the VPS ``.env`` carries the real values).

**If the file is missing, or either value is unresolved, the notifier is a
silent no-op.** It still writes the event to the pipeline log, so nothing is
lost and no stage can ever fail because notifications are not set up.

Events
------
``project.started``, ``agents.done``, ``gate.fail``, ``gate.needs_review``,
``agent.failed``, ``chain.abort``, ``images.done``, ``video.assembled``,
``upload.success`` (carries the URL), ``upload.failed``, ``discover.done``,
``queue.empty``, ``daemon.fatal``.

One message per milestone. Identical messages inside 60 seconds are dropped,
which is what keeps a retry loop from turning into a chat flood.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import povconfig
from povconfig import eprint, log_line, resolve_secret

Notify = Callable[[str, str], None]

TELEGRAM_API = "https://api.telegram.org"
HTTP_TIMEOUT = 10
DUPLICATE_WINDOW_S = 60

# Events that matter enough to interrupt a human. Anything else is logged
# only - this is the "no spam" rule, enforced in one place.
ALERT_EVENTS = {
    "project.started",
    "agents.done",
    "gate.fail",
    "gate.needs_review",
    "agent.failed",
    "chain.abort",
    "images.done",
    "images.failed",
    "video.assembled",
    "upload.success",
    "upload.failed",
    "discover.done",
    "queue.empty",
    "daemon.fatal",
    "daemon.started",
    "daemon.stopped",
    "test",
}

PREFIX = {
    "gate.fail": "WARN",
    "gate.needs_review": "NEEDS REVIEW",
    "agent.failed": "FAILED",
    "chain.abort": "ABORTED",
    "images.failed": "FAILED",
    "upload.failed": "FAILED",
    "daemon.fatal": "FATAL",
    "upload.success": "LIVE",
}


def default_config_path() -> Path:
    """``config/notify.env`` if present, else the committed template."""
    live = povconfig.config_dir() / "notify.env"
    if live.exists():
        return live
    return povconfig.config_dir() / "notify.env.template"


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` file. Comments and blanks ignored."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        eprint(f"[notify] could not read {path}: {exc}")
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_credentials(config_path: Path | None = None) -> tuple[str | None, str | None]:
    """``(bot_token, chat_id)``. Environment wins over the config file.

    An unresolved ``{{PLACEHOLDER}}`` is treated as absent, so shipping the
    template unmodified simply disables notifications.
    """
    values = read_env_file(config_path or default_config_path())
    token = (os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
             or resolve_secret(values.get("TELEGRAM_BOT_TOKEN", "")) or None)
    chat = (os.environ.get("TELEGRAM_CHAT_ID", "").strip()
            or resolve_secret(values.get("TELEGRAM_CHAT_ID", "")) or None)
    return (token or None), (chat or None)


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """POST one sendMessage. Returns success; never raises."""
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": "false",
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        if not body.get("ok"):
            log_line("notify.error", str(body)[:200], level="error", echo=False)
            return False
        return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
        log_line("notify.error", f"HTTP {exc.code}: {detail}", level="error",
                 echo=False)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log_line("notify.error", f"{type(exc).__name__}: {exc}", level="error",
                 echo=False)
    return False


def format_message(event: str, message: str) -> str:
    prefix = PREFIX.get(event)
    head = f"[{prefix}] " if prefix else ""
    return f"{head}{message}\n\n({event})"


def make_notifier(config_path: Path | None = None, *,
                  log_path: Path | None = None) -> Notify:
    """Build the notifier every stage is handed.

    Credentials are read once, at construction. The returned callable:

    * always writes the event to the pipeline log
    * sends a Telegram message when the event is in ``ALERT_EVENTS`` and
      credentials resolved
    * drops an identical event+message seen within 60 seconds
    * never raises, whatever happens
    """
    token, chat_id = load_credentials(config_path)
    configured = bool(token and chat_id)
    if not configured:
        log_line("notify.disabled",
                 "telegram not configured - logging events only", echo=False,
                 path=log_path)
    recent: dict[str, float] = {}

    def _notify(event: str, message: str) -> None:
        try:
            log_line(f"notify:{event}", message, echo=False, path=log_path)
            if not configured or event not in ALERT_EVENTS:
                return
            key = f"{event}|{message}"
            now = time.time()
            if now - recent.get(key, 0.0) < DUPLICATE_WINDOW_S:
                return
            recent[key] = now
            send_telegram(token or "", chat_id or "", format_message(event, message))
        except Exception as exc:  # notifications are never load-bearing
            eprint(f"[notify] {type(exc).__name__}: {exc}")

    return _notify


def null_notifier() -> Notify:
    """A notifier that does nothing at all (used by --no-notify)."""
    def _noop(_event: str, _message: str) -> None:
        return
    return _noop


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="notify",
                                 description="POV pipeline notifications")
    ap.add_argument("--test", action="store_true", help="send a probe message")
    ap.add_argument("--config", default=None, help="path to notify.env")
    ap.add_argument("--message", default="POV pipeline notification test.")
    args = ap.parse_args(argv)

    path = Path(args.config).expanduser() if args.config else default_config_path()
    token, chat = load_credentials(path)
    print(f"  config : {path}{'' if path.exists() else '  (missing)'}")
    print(f"  token  : {'set' if token else 'NOT SET'}")
    print(f"  chat id: {chat or 'NOT SET'}")
    if not args.test:
        return 0
    if not (token and chat):
        eprint("[notify] not configured - nothing sent (this is a no-op, "
               "not an error)")
        return 1
    ok = send_telegram(token, chat, format_message("test", args.message))
    print("  sent   :", "yes" if ok else "no")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
