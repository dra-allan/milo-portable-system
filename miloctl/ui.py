"""
ui.py — terminal output helpers.
================================

Zero dependencies. Colours degrade to plain text when the terminal can't
handle them (Windows cmd without VT, piped output, ``NO_COLOR=1``).
"""

from __future__ import annotations

import os
import sys
import shutil
import time
from typing import Iterable, Optional, Sequence

# ── Colour support ────────────────────────────────────────────────────────────


def _supports_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("MILO_FORCE_COLOR"):
        return True
    if sys.stdout is None or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Windows 10+ terminals honour VT sequences once enabled.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            return "WT_SESSION" in os.environ or "TERM" in os.environ
    return True


COLOUR = _supports_colour()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOUR else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def red(t: str) -> str:
    return _c("31", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def blue(t: str) -> str:
    return _c("34", t)


def magenta(t: str) -> str:
    return _c("35", t)


def cyan(t: str) -> str:
    return _c("36", t)


# ── Symbols (ASCII fallback for consoles without UTF-8) ───────────────────────


def _unicode_ok() -> bool:
    if os.environ.get("MILO_ASCII"):
        return False
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in enc


UNI = _unicode_ok()

SYM = {
    "ok": "✓" if UNI else "[ok]",
    "warn": "!" if not UNI else "▲",
    "err": "✗" if UNI else "[x]",
    "info": "·" if UNI else "-",
    "arrow": "→" if UNI else "->",
    "bullet": "•" if UNI else "*",
}

# ── Output primitives ─────────────────────────────────────────────────────────

_QUIET = bool(os.environ.get("MILO_QUIET"))
_VERBOSE = bool(os.environ.get("MILO_VERBOSE"))


def set_quiet(value: bool) -> None:
    global _QUIET
    _QUIET = value


def set_verbose(value: bool) -> None:
    global _VERBOSE
    _VERBOSE = value


def say(msg: str = "") -> None:
    if not _QUIET:
        print(msg)


def ok(msg: str) -> None:
    say(f"  {green(SYM['ok'])} {msg}")


def warn(msg: str) -> None:
    say(f"  {yellow(SYM['warn'])} {msg}")


def err(msg: str) -> None:
    print(f"  {red(SYM['err'])} {msg}", file=sys.stderr)


def info(msg: str) -> None:
    say(f"  {dim(SYM['info'])} {msg}")


def debug(msg: str) -> None:
    if _VERBOSE:
        say(f"  {dim('debug: ' + msg)}")


def step(msg: str) -> None:
    say(f"\n{cyan(SYM['arrow'])} {bold(msg)}")


def banner(title: str, subtitle: str = "") -> None:
    width = min(shutil.get_terminal_size((78, 24)).columns, 78)
    line = "─" * width if UNI else "-" * width
    say()
    say(dim(line))
    say(f" {bold(title)}" + (f"  {dim(subtitle)}" if subtitle else ""))
    say(dim(line))


def kv(key: str, value: str, width: int = 18) -> None:
    say(f"  {dim(key.ljust(width))} {value}")


def table(rows: Sequence[Sequence[str]], headers: Optional[Sequence[str]] = None) -> None:
    """Minimal left-aligned table."""
    data = [list(map(str, r)) for r in rows]
    if not data and not headers:
        return
    cols = len(headers) if headers else (len(data[0]) if data else 0)
    widths = [0] * cols
    if headers:
        for i, h in enumerate(headers):
            widths[i] = len(str(h))
    for r in data:
        for i in range(min(cols, len(r))):
            widths[i] = max(widths[i], len(r[i]))
    if headers:
        say("  " + dim("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))))
    for r in data:
        say("  " + "  ".join(
            (r[i] if i < len(r) else "").ljust(widths[i]) for i in range(cols)
        ))


def bullet_list(items: Iterable[str]) -> None:
    for it in items:
        say(f"    {dim(SYM['bullet'])} {it}")


def confirm(prompt: str, default: bool = False) -> bool:
    if os.environ.get("MILO_YES"):
        return True
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


def ask(prompt: str, default: str = "", secret_hint: str = "") -> str:
    """Prompt with optional default. ``secret_hint`` shows a masked preview."""
    shown = secret_hint or default
    hint = f" [{shown}]" if shown else ""
    try:
        val = input(f"  {prompt}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val or default


def choose(prompt: str, options: Sequence[str], default: int = 0) -> str:
    say(f"\n  {bold(prompt)}")
    for i, opt in enumerate(options, 1):
        marker = dim("(default)") if i - 1 == default else ""
        say(f"    {i}. {opt} {marker}")
    try:
        raw = input(f"  Choice [1-{len(options)}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return options[default]
    if not raw:
        return options[default]
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except ValueError:
        pass
    return options[default]


class Spinner:
    """Tiny context-manager spinner; no-ops when output isn't a TTY."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if UNI else "|/-\\"

    def __init__(self, label: str):
        self.label = label
        self._active = COLOUR and sys.stdout is not None and sys.stdout.isatty() and not _QUIET
        self._i = 0
        self._start = 0.0

    def __enter__(self) -> "Spinner":
        self._start = time.time()
        if not self._active:
            info(self.label + "…")
        return self

    def tick(self) -> None:
        if not self._active:
            return
        frame = self.FRAMES[self._i % len(self.FRAMES)]
        self._i += 1
        sys.stdout.write(f"\r  {cyan(frame)} {self.label}…")
        sys.stdout.flush()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._active:
            sys.stdout.write("\r" + " " * (len(self.label) + 8) + "\r")
            sys.stdout.flush()
        elapsed = time.time() - self._start
        if exc_type is None:
            ok(f"{self.label} {dim(f'({elapsed:.1f}s)')}")
        else:
            err(f"{self.label} failed: {exc}")
