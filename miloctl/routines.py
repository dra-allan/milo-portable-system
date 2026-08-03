"""
routines.py — Milo doing things while you're asleep.
====================================================

A **routine** is a prompt plus a schedule. Milo wakes up, runs it through
whichever agent runtime is installed, and writes the result somewhere useful
(the vault, memory, Telegram, or just a log).

This is Hermes' scheduled-routines idea, rebuilt with two hard constraints
that Hermes doesn't have to care about:

**No daemon.** Hermes runs a managed cron service. Milo has to survive a
laptop that gets closed, a phone that kills background processes, and a
machine change on a Tuesday. So routines are stored as plain data and driven
by whatever scheduler the OS already has — Task Scheduler, systemd timers,
crontab, or Termux:Boot. ``milo routines tick`` is the single entry point all
of them call, and it works fine if you just run it by hand.

**Catch-up, not drift.** A missed run isn't silently skipped. A routine that
should have fired while the laptop was shut records the miss and (unless it's
marked ``skip_missed``) runs once on the next tick. The daily backup happening
late is always better than not happening.

Schedules are deliberately small — five forms, all readable::

    every 15m / every 2h / every 3d      interval
    daily at 07:30                        once a day, local time
    weekly on mon at 09:00                once a week
    monthly on 1 at 08:00                 day-of-month
    manual                                never automatic; run it yourself

Cron expressions are accepted too (``cron 0 7 * * 1-5``) for the cases the
plain forms can't express.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import paths
from .naming import canonical

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


# ── schedule parsing ──────────────────────────────────────────────────────────


class ScheduleError(ValueError):
    """A schedule string we refuse to guess at."""


def _parse_hhmm(text: str) -> Tuple[int, int]:
    text = text.strip()
    if ":" not in text:
        # "7" and "0730" both mean something obvious.
        if text.isdigit() and len(text) == 4:
            return int(text[:2]), int(text[2:])
        if text.isdigit():
            return int(text), 0
        raise ScheduleError(f"bad time {text!r} — use HH:MM")
    hh, _, mm = text.partition(":")
    try:
        h, m = int(hh), int(mm)
    except ValueError as exc:
        raise ScheduleError(f"bad time {text!r} — use HH:MM") from exc
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ScheduleError(f"time out of range: {text!r}")
    return h, m


def parse_schedule(text: str) -> Dict[str, Any]:
    """Turn a human schedule string into a normalised dict.

    Raises :class:`ScheduleError` rather than silently accepting something
    that would never fire — a routine that never runs is worse than an error
    at the moment you typed it.
    """
    raw = " ".join((text or "").strip().lower().split())
    if not raw or raw in ("manual", "never", "off"):
        return {"kind": "manual"}

    parts = raw.split()

    if parts[0] == "cron":
        expr = " ".join(parts[1:])
        if len(expr.split()) != 5:
            raise ScheduleError("cron needs 5 fields: minute hour dom month dow")
        return {"kind": "cron", "expr": expr}

    if parts[0] == "every":
        if len(parts) < 2:
            raise ScheduleError("every what? try 'every 30m'")
        token = "".join(parts[1:])
        # "every 30 minutes" -> "30minutes"; take digits then first letter.
        digits = "".join(c for c in token if c.isdigit())
        letters = "".join(c for c in token if c.isalpha())
        if not digits:
            raise ScheduleError(f"no interval in {text!r}")
        unit = (letters[:1] or "m")
        if unit not in _UNIT_SECONDS:
            raise ScheduleError(f"unknown unit {letters!r} — use s/m/h/d/w")
        seconds = int(digits) * _UNIT_SECONDS[unit]
        if seconds < 60:
            raise ScheduleError("minimum interval is 60s")
        return {"kind": "interval", "seconds": seconds}

    if parts[0] == "daily":
        at = parts[parts.index("at") + 1] if "at" in parts else "08:00"
        h, m = _parse_hhmm(at)
        return {"kind": "daily", "hour": h, "minute": m}

    if parts[0] == "weekly":
        day = "mon"
        for p in parts:
            if p[:3] in _DAYS:
                day = p[:3]
                break
        at = parts[parts.index("at") + 1] if "at" in parts else "09:00"
        h, m = _parse_hhmm(at)
        return {"kind": "weekly", "day": _DAYS.index(day), "hour": h, "minute": m}

    if parts[0] == "monthly":
        dom = 1
        for p in parts[1:]:
            if p.isdigit():
                dom = max(1, min(28, int(p)))  # 28 so every month has it
                break
        at = parts[parts.index("at") + 1] if "at" in parts else "08:00"
        h, m = _parse_hhmm(at)
        return {"kind": "monthly", "day": dom, "hour": h, "minute": m}

    if parts[0] == "hourly":
        return {"kind": "interval", "seconds": 3600}

    raise ScheduleError(
        f"can't read schedule {text!r}. Try: 'every 30m', 'daily at 07:30', "
        f"'weekly on mon at 09:00', 'monthly on 1 at 08:00', or 'manual'"
    )


def describe_schedule(spec: Dict[str, Any]) -> str:
    """Render a parsed schedule back to something a human recognises."""
    kind = spec.get("kind")
    if kind == "manual":
        return "manual"
    if kind == "interval":
        s = int(spec.get("seconds", 0))
        for unit, label in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
            if s % label == 0 and s >= label:
                return f"every {s // label}{unit}"
        return f"every {s}s"
    if kind == "daily":
        return f"daily at {spec.get('hour', 0):02d}:{spec.get('minute', 0):02d}"
    if kind == "weekly":
        return (f"weekly on {_DAYS[int(spec.get('day', 0)) % 7]} at "
                f"{spec.get('hour', 0):02d}:{spec.get('minute', 0):02d}")
    if kind == "monthly":
        return (f"monthly on {spec.get('day', 1)} at "
                f"{spec.get('hour', 0):02d}:{spec.get('minute', 0):02d}")
    if kind == "cron":
        return f"cron {spec.get('expr', '')}"
    return "unknown"


def _cron_matches(expr: str, when: datetime) -> bool:
    """Minimal 5-field cron matcher: ``*``, ``a,b``, ``a-b``, ``*/n``."""
    fields = expr.split()
    if len(fields) != 5:
        return False
    values = [when.minute, when.hour, when.day, when.month, when.weekday()]
    # cron weekday is 0=Sunday; python weekday() is 0=Monday.
    values[4] = (when.weekday() + 1) % 7
    for field_text, value in zip(fields, values):
        if not _cron_field_matches(field_text, value):
            return False
    return True


def _cron_field_matches(text: str, value: int) -> bool:
    for part in text.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            step = int(step_text) if step_text.isdigit() else 1
        if part in ("*", ""):
            if value % step == 0:
                return True
            continue
        if "-" in part:
            lo_text, _, hi_text = part.partition("-")
            if not (lo_text.isdigit() and hi_text.isdigit()):
                continue
            lo, hi = int(lo_text), int(hi_text)
            if lo <= value <= hi and (value - lo) % step == 0:
                return True
            continue
        if part.isdigit() and int(part) == value:
            return True
    return False


def next_due(spec: Dict[str, Any], after: Optional[float] = None) -> Optional[float]:
    """Epoch seconds of the next fire time, or ``None`` for manual routines."""
    now = after if after is not None else time.time()
    kind = spec.get("kind")

    if kind in (None, "manual"):
        return None
    if kind == "interval":
        return now + int(spec.get("seconds", 3600))

    base = datetime.fromtimestamp(now)

    if kind == "daily":
        target = base.replace(hour=int(spec.get("hour", 8)),
                              minute=int(spec.get("minute", 0)),
                              second=0, microsecond=0)
        if target <= base:
            target += timedelta(days=1)
        return target.timestamp()

    if kind == "weekly":
        want = int(spec.get("day", 0)) % 7
        target = base.replace(hour=int(spec.get("hour", 9)),
                              minute=int(spec.get("minute", 0)),
                              second=0, microsecond=0)
        delta = (want - target.weekday()) % 7
        target += timedelta(days=delta)
        if target <= base:
            target += timedelta(days=7)
        return target.timestamp()

    if kind == "monthly":
        dom = int(spec.get("day", 1))
        target = base.replace(day=min(dom, 28), hour=int(spec.get("hour", 8)),
                              minute=int(spec.get("minute", 0)),
                              second=0, microsecond=0)
        if target <= base:
            month = target.month + 1
            year = target.year + (month > 12)
            target = target.replace(year=year, month=(month - 1) % 12 + 1)
        return target.timestamp()

    if kind == "cron":
        expr = str(spec.get("expr", ""))
        probe = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(60 * 24 * 366):  # scan up to a year of minutes
            if _cron_matches(expr, probe):
                return probe.timestamp()
            probe += timedelta(minutes=1)
        return None

    return None


# ── the routine ───────────────────────────────────────────────────────────────


@dataclass
class Routine:
    """One scheduled unit of work."""

    name: str
    prompt: str = ""
    #: Shell command to run instead of a prompt. Used by the built-ins so
    #: 'daily backup' doesn't need a model, or an API key, or a network.
    command: str = ""
    schedule: Dict[str, Any] = field(default_factory=lambda: {"kind": "manual"})
    enabled: bool = True
    harness: str = ""            # "" = first available
    model: str = ""
    timeout: int = 900
    #: Where the output goes: log | vault | memory | telegram (comma-joined)
    output: str = "log"
    #: A run that was missed while the machine was off fires once on return,
    #: unless this is set. Backups want catch-up; a 7am briefing at 4pm does not.
    skip_missed: bool = False
    tags: List[str] = field(default_factory=list)
    builtin: bool = False
    created_at: float = field(default_factory=time.time)
    last_run: float = 0.0
    last_status: str = ""
    last_output: str = ""
    next_run: float = 0.0
    runs: int = 0
    failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Routine":
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in fields})

    @property
    def schedule_label(self) -> str:
        return describe_schedule(self.schedule)

    def due(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        if not self.enabled or self.schedule.get("kind") == "manual":
            return False
        if not self.next_run:
            # Never scheduled. Interval routines fire immediately so adding
            # 'every 6h' doesn't mean waiting 6h to find out it's broken.
            return self.schedule.get("kind") == "interval"
        return now >= self.next_run

    def reschedule(self, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        nxt = next_due(self.schedule, now)
        self.next_run = nxt or 0.0
        return self.next_run

    def status_line(self) -> str:
        when = ("never" if not self.next_run
                else datetime.fromtimestamp(self.next_run).strftime("%a %d %b %H:%M"))
        state = "on" if self.enabled else "off"
        return f"{self.name:<22} {self.schedule_label:<22} {state:<4} next {when}"


# ── built-ins ─────────────────────────────────────────────────────────────────
#
# These ship enabled-by-default because they are the maintenance that keeps the
# system portable. They are plain shell commands on purpose: no model, no API
# key, no network beyond git. If everything else is broken, the backup still
# runs.

def _builtin_specs() -> List[Dict[str, Any]]:
    return [
        {
            "name": "daily-backup",
            "command": "milo backup -m 'routine: daily backup'",
            "schedule": "daily at 23:30",
            "output": "log",
            "skip_missed": False,   # a late backup still counts
            "tags": ["maintenance", "critical"],
        },
        {
            "name": "frequent-backup",
            "command": "milo backup -m 'routine: frequent backup'",
            "schedule": "every 15m",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance", "frequent"],
        },
        {
            "name": "curate-skills",
            "command": "milo curate --if-due",
            "schedule": "daily at 04:00",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance"],
        },
        {
            "name": "memory-hygiene",
            "command": "milo memory expire",
            "schedule": "weekly on sun at 03:00",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance"],
        },
        {
            "name": "memory-compress",
            "command": "milo memory compress",
            "schedule": "weekly on sun at 04:00",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance"],
        },
        {
            "name": "memory-reflect",
            "command": "milo memory reflect",
            "schedule": "weekly on sun at 05:00",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance"],
        },
        {
            "name": "vault-sync",
            "command": "milo vault sync",
            "schedule": "every 6h",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance"],
        },
        {
            "name": "frequent-vault-sync",
            "command": "milo vault sync",
            "schedule": "every 15m",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance", "frequent"],
        },
        {
            "name": "persona-refresh",
            "command": "milo sync",
            "schedule": "daily at 05:00",
            "output": "log",
            "skip_missed": True,
            "tags": ["maintenance"],
        },
        {
            "name": "morning-briefing",
            "prompt": (
                "Good morning. Give Allan a short briefing for today:\n"
                "1. What was decided or left unfinished yesterday "
                "(check memory and the vault daily note).\n"
                "2. Anything time-sensitive you know about.\n"
                "3. One thing worth his attention that he hasn't asked about.\n"
                "Be brief. No preamble, no filler. If there is nothing "
                "meaningful to report, say so in one line."
            ),
            "schedule": "daily at 07:00",
            "output": "vault,telegram",
            "skip_missed": True,    # a 7am briefing at 4pm is noise
            "tags": ["briefing"],
        },
    ]


# ── the store ─────────────────────────────────────────────────────────────────


class RoutineStore:
    """Load, run and persist routines. Plain JSON so it diffs in git."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else paths.cron_file()
        self.routines: Dict[str, Routine] = {}
        self.load()

    # -- persistence --

    def load(self) -> "RoutineStore":
        self.routines = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            except (OSError, json.JSONDecodeError):
                data = {}
            for raw in data.get("routines", []):
                try:
                    r = Routine.from_dict(raw)
                except TypeError:
                    continue
                self.routines[r.name] = r
        return self

    def save(self) -> Path:
        paths.ensure(self.path.parent)
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "routines": [r.to_dict() for r in sorted(
                self.routines.values(), key=lambda r: r.name)],
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.path

    # -- crud --

    def get(self, name: str) -> Optional[Routine]:
        name = canonical(name)
        if name in self.routines:
            return self.routines[name]
        # tolerate close spellings, same as everywhere else in Milo
        from .naming import match_command
        hit = match_command(name, list(self.routines))
        return self.routines.get(hit) if hit else None

    def add(
        self,
        name: str,
        *,
        prompt: str = "",
        command: str = "",
        schedule: str = "manual",
        output: str = "log",
        harness: str = "",
        model: str = "",
        tags: Optional[Sequence[str]] = None,
        skip_missed: bool = False,
        timeout: int = 900,
        builtin: bool = False,
        overwrite: bool = False,
    ) -> Routine:
        name = canonical(name)
        if not name:
            raise ValueError("a routine needs a name")
        if not prompt and not command:
            raise ValueError("a routine needs either a prompt or a command")
        if name in self.routines and not overwrite:
            raise ValueError(f"{name!r} already exists — use --force to replace")

        r = Routine(
            name=name, prompt=prompt.strip(), command=command.strip(),
            schedule=parse_schedule(schedule), output=output,
            harness=harness, model=model, tags=list(tags or []),
            skip_missed=skip_missed, timeout=timeout, builtin=builtin,
        )
        r.reschedule()
        self.routines[name] = r
        self.save()
        return r

    def remove(self, name: str) -> bool:
        r = self.get(name)
        if not r:
            return False
        del self.routines[r.name]
        self.save()
        return True

    def set_enabled(self, name: str, enabled: bool) -> Optional[Routine]:
        r = self.get(name)
        if not r:
            return None
        r.enabled = enabled
        if enabled and not r.next_run:
            r.reschedule()
        self.save()
        return r

    def set_schedule(self, name: str, schedule: str) -> Optional[Routine]:
        r = self.get(name)
        if not r:
            return None
        r.schedule = parse_schedule(schedule)
        r.reschedule()
        self.save()
        return r

    def all(self, include_disabled: bool = True) -> List[Routine]:
        rows = sorted(self.routines.values(), key=lambda r: (not r.enabled, r.name))
        return rows if include_disabled else [r for r in rows if r.enabled]

    def install_builtins(self, overwrite: bool = False) -> List[str]:
        """Add the maintenance routines. Idempotent; never clobbers edits."""
        added: List[str] = []
        for spec in _builtin_specs():
            name = spec["name"]
            if name in self.routines and not overwrite:
                continue
            spec = dict(spec)
            spec["builtin"] = True
            spec["overwrite"] = True
            self.add(spec.pop("name"), **spec)
            added.append(name)
        return added

    # -- execution --

    def due(self, now: Optional[float] = None) -> List[Routine]:
        now = now if now is not None else time.time()
        return [r for r in self.all(include_disabled=False) if r.due(now)]

    def run(self, name: str, *, dry_run: bool = False) -> Dict[str, Any]:
        r = self.get(name)
        if not r:
            return {"routine": name, "status": "missing",
                    "output": f"no routine named {name!r}"}
        return self._execute(r, dry_run=dry_run)

    def tick(self, *, dry_run: bool = False, limit: int = 10) -> List[Dict[str, Any]]:
        """Run everything that's due. The one entry point every OS scheduler calls."""
        results: List[Dict[str, Any]] = []
        now = time.time()
        for r in self.due(now)[:limit]:
            # A routine that says "don't bother if you missed it" gets its
            # clock advanced without running.
            if r.skip_missed and r.next_run and (now - r.next_run) > _grace(r):
                r.last_status = "skipped"
                r.reschedule(now)
                self.save()
                results.append({"routine": r.name, "status": "skipped",
                                "output": "missed its window"})
                continue
            results.append(self._execute(r, dry_run=dry_run))
        return results

    def _execute(self, r: Routine, *, dry_run: bool = False) -> Dict[str, Any]:
        started = time.time()
        if dry_run:
            return {"routine": r.name, "status": "dry-run",
                    "output": r.command or r.prompt[:200]}

        if r.command:
            code, out = _run_command(r.command, timeout=r.timeout)
        else:
            code, out = _run_prompt(r, timeout=r.timeout)

        r.last_run = started
        r.runs += 1
        r.last_status = "ok" if code == 0 else f"failed ({code})"
        r.last_output = out[-4000:]
        if code != 0:
            r.failures += 1
        r.reschedule(time.time())
        self.save()

        _write_log(r, code, out, started)
        if code == 0 and out.strip():
            _deliver(r, out)

        return {"routine": r.name, "status": r.last_status,
                "seconds": round(time.time() - started, 1),
                "output": out[:2000]}

    def stats(self) -> Dict[str, Any]:
        rows = self.all()
        return {
            "total": len(rows),
            "enabled": sum(1 for r in rows if r.enabled),
            "builtin": sum(1 for r in rows if r.builtin),
            "runs": sum(r.runs for r in rows),
            "failures": sum(r.failures for r in rows),
            "next": min((r.next_run for r in rows if r.next_run), default=0),
            "file": str(self.path),
        }


def _grace(r: Routine) -> float:
    """How late is 'too late' for a skip_missed routine."""
    kind = r.schedule.get("kind")
    if kind == "interval":
        return max(600.0, float(r.schedule.get("seconds", 3600)))
    return 4 * 3600.0  # a daily/weekly slot is stale after four hours


def milo_argv(rest: Optional[List[str]] = None) -> List[str]:
    """The most reliable way to invoke Milo again from a child process.

    Order matters. A console script sitting next to this interpreter is the
    best answer because it works regardless of cwd. A bare ``milo`` on PATH is
    next. The ``-m`` form is last, and only ever with ``PYTHONPATH`` pointing
    at the package parent — otherwise a routine run from a git checkout dies
    with ``No module named 'miloctl'`` the moment cron changes directory.
    """
    rest = list(rest or [])
    exe = "milo.exe" if os.name == "nt" else "milo"
    beside = Path(sys.executable).parent / exe
    if beside.is_file():
        return [str(beside), *rest]
    found = shutil.which("milo") or shutil.which("mylo")
    if found:
        return [found, *rest]
    return [sys.executable, "-m", "miloctl.cli", *rest]


def _run_command(command: str, *, timeout: int = 900) -> Tuple[int, str]:
    """Run a shell command, preferring this interpreter's own console scripts.

    A routine that says ``milo backup`` must still work when cron runs it with
    a bare PATH and an unrelated cwd, so ``milo``/``mylo`` are resolved through
    :func:`milo_argv` rather than trusted to PATH.
    """
    argv = shlex.split(command, posix=(os.name != "nt"))
    if argv and argv[0].lower() in ("milo", "mylo"):
        argv = milo_argv(argv[1:])

    # Make the ``-m`` fallback importable from anywhere, and keep the child out
    # of any virtualenv confusion by pinning the package parent explicitly.
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    if pkg_parent not in (env.get("PYTHONPATH") or "").split(os.pathsep):
        env["PYTHONPATH"] = os.pathsep.join(
            [p for p in (pkg_parent, env.get("PYTHONPATH", "")) if p])

    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           cwd=str(paths.milo_home()), env=env)
    except FileNotFoundError:
        return 127, f"command not found: {argv[0] if argv else command}"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except (OSError, ValueError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _run_prompt(r: Routine, *, timeout: int = 900) -> Tuple[int, str]:
    from . import harness

    h = harness.get_harness(r.harness) if r.harness else None
    if h is None:
        runnable = [x for x in harness.detect_installed() if x.which()]
        if not runnable:
            return 127, "no agent runtime installed — nothing to run the prompt with"
        h = runnable[0]
    return h.run(r.prompt, model=r.model, timeout=timeout)


def _write_log(r: Routine, code: int, out: str, started: float) -> None:
    log_dir = paths.logs_dir() / "routines"
    try:
        paths.ensure(log_dir)
        stamp = datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / f"{r.name}.log").open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {stamp}  exit={code} ===\n{out.strip()}\n")
    except OSError:
        pass  # a routine must never fail because logging failed


def _deliver(r: Routine, out: str) -> None:
    """Route output to wherever the routine says it belongs."""
    targets = {t.strip() for t in (r.output or "log").split(",") if t.strip()}

    if "vault" in targets:
        try:
            from .vault import vault
            v = vault()
            if v.exists:
                v.append_daily(out.strip(), heading=f"Routine: {r.name}")
        except Exception:
            pass

    if "memory" in targets:
        try:
            from .memory import store
            store().save(out.strip()[:4000], category="note",
                         title=f"routine:{r.name}", tags=["routine", *r.tags])
        except Exception:
            pass

    if "telegram" in targets:
        try:
            from .channels import send_telegram
            send_telegram(out.strip())
        except Exception:
            pass


_STORE: Optional[RoutineStore] = None


def store() -> RoutineStore:
    global _STORE
    if _STORE is None:
        _STORE = RoutineStore()
    return _STORE
