#!/usr/bin/env python
"""pipeline_runner.py - run a posting pipeline unattended, then report it.

Why this exists
===============

``run_pipeline.bat`` and ``run_ranking_pipeline.bat`` are *control panels*:
they print a menu, wait on ``set /p`` and end with ``pause``. Pointed at Task
Scheduler they either hang forever on the prompt or exit before doing work,
which is why "the daemons are registered" and "the daemons post daily" were
two different facts on the VPS.

This is the non-interactive twin. One command per pipeline, no prompts, and
three guarantees the .bat files never gave:

* **One run at a time.** A lock file with a liveness check, so an 08:45 run
  that is still rendering at 09:15 does not get a second copy of itself
  fighting over the same tokens, caps and temp files.
* **A log you can find.** ``<pipeline>/data/logs/daemon-YYYY-MM-DD.log``,
  line-buffered, plus a machine-readable summary at
  ``<state>/pipeline_runs/<key>-last.json`` that the Telegram bot reads for
  ``/pipelines``.
* **A report on Telegram, always.** Success, failure, timeout or skip - Allan
  finds out from his phone, not by opening RDP. Sent with stdlib urllib so it
  works from inside a pipeline venv that has never heard of httpx.

Usage::

    python scripts/daemons/pipeline_runner.py shorts
    python scripts/daemons/pipeline_runner.py ranking --videos 3
    python scripts/daemons/pipeline_runner.py shorts --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]

# -- pipeline definitions -----------------------------------------------------
#
# ``env`` values are applied with setdefault: the pipeline's own config/.env and
# anything already exported always wins. They exist because the interactive .bat
# files set them before every run, so a daemon that omits them silently posts
# with different caps, privacy or channel routing than the menu does.

PIPELINES: Dict[str, Dict[str, Any]] = {
    "shorts": {
        "label": "YouTube Shorts",
        "dir": REPO_ROOT / "artisan" / "youtube-shorts-pipeline",
        "args": lambda a: ["--mode", "once", "--videos", str(a.videos or 1)],
        "env": {
            "SCHEDULE_MAX_VIDEOS": "1",
            "SCHEDULE_MAX_TOTAL": "0",
        },
        "cleanup": ["cleanup_runtime.py"],
        "timeout": 4 * 3600,
    },
    "ranking": {
        "label": "Ranking Shorts",
        "dir": REPO_ROOT / "artisan" / "ranking-shorts-pipeline",
        "args": lambda a: ["--mode", "auto", "--videos", str(a.videos or 3),
                           "--variant", a.variant or "mixed"],
        "env": {
            "AUTO_UPLOAD": "true",
            "UPLOAD_PRIVACY": "public",
            "RANKING_FAST_MODE": "true",
            "RANKING_RENDER_WORKERS": "2",
            "RANKING_REJECT_BUDGET": "20",
            "RANKING_CLEANUP_AFTER_BUILD": "true",
            "RANKING_DELETE_AFTER_UPLOAD": "true",
            "RANKING_CHANNEL_PROFILES": "RankDrop:normal,the other guys:contrast",
            "RANKING_UPLOAD_CHANNEL": "RankDrop",
            "UPLOAD_MAX_PER_DAY": "6",
            "UPLOAD_MAX_PER_CHANNEL": "6",
            "RANKING_UPLOAD_MAX_PER_DAY": "6",
            "RANKING_UPLOAD_MAX_PER_CHANNEL": "6",
            "RANKING_UPLOAD_DELAY_MIN": "45",
            "RANKING_UPLOAD_DELAY_MAX": "180",
            "CONTRAST_SUBJECT": "GUY",
        },
        "cleanup": ["cleanup_runtime.py"],
        "timeout": 4 * 3600,
    },
}

VIDEO_ID = r"[A-Za-z0-9_-]{11}"
UPLOAD_PATTERNS = [
    re.compile(r"https?://(?:www\.)?youtu\.be/(" + VIDEO_ID + ")"),
    re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=(" + VIDEO_ID + ")"),
    re.compile(r"https?://(?:www\.)?youtube\.com/shorts/(" + VIDEO_ID + ")"),
    re.compile(r"(?:uploaded|published|posted)[^\n]{0,40}?\b(" + VIDEO_ID + r")\b", re.I),
]
ERROR_PATTERNS = re.compile(
    r"(traceback \(most recent call last\)|^\s*(error|critical)\b|invalid_grant|"
    r"quotaexceeded|quota exceeded|unplayable|sign in to confirm|403 forbidden|"
    r"failed to upload|upload failed|no such file)", re.I)


# -- env ----------------------------------------------------------------------


def load_env_files(pipeline_dir: Path) -> None:
    """Ambient env first, then every ``.env`` that might hold the tokens.

    Task Scheduler hands a process almost nothing, so a runner that assumes an
    inherited environment is a runner that posts nowhere and reports nothing.
    """
    home = os.environ.get("MILO_HOME")
    local = os.environ.get("LOCALAPPDATA")
    candidates = [
        Path(home) / ".env" if home else None,
        Path(local) / "milo" / ".env" if local else None,
        Path.home() / ".milo" / ".env",
        REPO_ROOT / ".env",
        pipeline_dir / ".env",
        pipeline_dir / "config" / ".env",
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


def state_dir() -> Path:
    raw = os.environ.get("MILO_HOME")
    if raw:
        return Path(raw).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "milo"
    return Path.home() / ".milo"


def python_for(pipeline_dir: Path) -> str:
    """The interpreter that actually has this pipeline's dependencies.

    Pipeline venv first: the repo venv has miloctl, not yt-dlp/ffmpeg bindings,
    and picking the wrong one fails deep inside an import three minutes in.
    """
    names = ("Scripts/python.exe", "bin/python")
    for base in (pipeline_dir / "venv", REPO_ROOT / ".venv", REPO_ROOT / "venv"):
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)
    return sys.executable


# -- single-instance lock -----------------------------------------------------


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return True          # can't tell -> assume alive, never double-run
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_lock(path: Path) -> Tuple[bool, Dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    if existing and pid_alive(int(existing.get("pid") or 0)):
        return False, existing
    path.write_text(json.dumps({"pid": os.getpid(),
                                "started": datetime.now().isoformat(timespec="seconds")},
                               indent=2), encoding="utf-8")
    return True, {}


def release_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# -- telegram -----------------------------------------------------------------


def telegram(text: str) -> bool:
    """Send one message with the stdlib. Never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("[warn] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing - no report sent")
        return False
    ok = True
    for i in range(0, len(text), 3800):
        payload = json.dumps({"chat_id": chat, "text": text[i:i + 3800],
                              "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "milo-runner/1.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    json.loads(resp.read().decode("utf-8", "replace"))
                break
            except Exception as exc:            # noqa: BLE001 - reporting must not raise
                if attempt == 2:
                    print(f"[warn] telegram send failed: {exc}")
                    ok = False
                else:
                    time.sleep(2 * (attempt + 1))
    return ok


# -- output parsing -----------------------------------------------------------


def parse_output(lines: List[str]) -> Tuple[List[str], List[str]]:
    uploads: List[str] = []
    errors: List[str] = []
    for line in lines:
        for pattern in UPLOAD_PATTERNS:
            for vid in pattern.findall(line):
                url = f"https://youtu.be/{vid}"
                if url not in uploads:
                    uploads.append(url)
        if ERROR_PATTERNS.search(line):
            clean = line.strip()
            if clean and clean not in errors:
                errors.append(clean)
    return uploads, errors[-12:]


def human(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


# -- the run ------------------------------------------------------------------


def write_summary(key: str, summary: Dict[str, Any]) -> None:
    out_dir = state_dir() / "pipeline_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        (out_dir / f"{key}-last.json").write_text(json.dumps(summary, indent=2),
                                                  encoding="utf-8")
        with (out_dir / f"{key}-history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary) + "\n")
    except OSError as exc:
        print(f"[warn] could not write summary: {exc}")


def report(meta: Dict[str, Any], summary: Dict[str, Any]) -> str:
    icon = {"ok": "[OK]", "failed": "[FAILED]", "timeout": "[TIMEOUT]",
            "skipped": "[SKIPPED]"}.get(summary["status"], "[?]")
    lines = [f"{icon} {meta['label']} pipeline",
             f"started {summary['started']} · took {summary['duration']}",
             f"exit {summary['exit_code']} · uploads {len(summary['uploads'])}"]
    if summary["uploads"]:
        lines.append("")
        lines += [f"- {u}" for u in summary["uploads"][:10]]
        if len(summary["uploads"]) > 10:
            lines.append(f"...and {len(summary['uploads']) - 10} more")
    if summary["errors"]:
        lines.append("")
        lines.append("problems:")
        lines += [f"! {e[:200]}" for e in summary["errors"][:5]]
    lines.append("")
    lines.append(f"log: {summary['log']}")
    if summary["status"] != "ok":
        lines.append("next: /logs " + summary["key"] + " 40  ·  /run " + summary["key"])
    return "\n".join(lines)


def run(key: str, args: argparse.Namespace) -> int:
    meta = PIPELINES[key]
    pipeline_dir = Path(meta["dir"])
    if not pipeline_dir.is_dir():
        msg = f"[FAILED] {meta['label']}: {pipeline_dir} does not exist on this machine."
        print(msg)
        if args.notify:
            telegram(msg)
        return 2

    load_env_files(pipeline_dir)
    for name, value in meta["env"].items():
        os.environ.setdefault(name, value)
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    data_dir = pipeline_dir / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"daemon-{datetime.now():%Y-%m-%d}.log"
    lock_path = data_dir / f"{key}.lock"

    got_lock, holder = acquire_lock(lock_path)
    if not got_lock:
        summary = {"key": key, "label": meta["label"], "status": "skipped",
                   "started": datetime.now().isoformat(timespec="seconds"),
                   "duration": "0s", "exit_code": 0, "uploads": [],
                   "errors": [f"already running since {holder.get('started', '?')} "
                              f"(pid {holder.get('pid')})"],
                   "log": str(log_path)}
        write_summary(key, summary)
        print(report(meta, summary))
        if args.notify:
            telegram(report(meta, summary))
        return 0

    python = python_for(pipeline_dir)
    argv = [python, "-m", "src.main"] + list(meta["args"](args)) + list(args.extra or [])
    started = datetime.now()
    timeout = args.timeout or meta["timeout"]
    header = (f"\n{'=' * 78}\n{started:%Y-%m-%d %H:%M:%S} START {meta['label']}\n"
              f"cmd: {' '.join(argv)}\ncwd: {pipeline_dir}\ntimeout: {timeout}s\n{'=' * 78}\n")
    print(header.strip())

    if args.dry_run:
        release_lock(lock_path)
        print("[dry-run] nothing executed")
        return 0

    captured: List[str] = []
    rc = 1
    status = "failed"
    try:
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(header)
            log.flush()
            proc = subprocess.Popen(
                argv, cwd=str(pipeline_dir), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1)
            deadline = time.time() + timeout
            assert proc.stdout is not None
            for line in proc.stdout:
                captured.append(line.rstrip())
                log.write(line)
                log.flush()
                sys.stdout.write(line)
                if time.time() > deadline:
                    proc.kill()
                    status = "timeout"
                    break
            rc = proc.wait(timeout=120)
            if status != "timeout":
                status = "ok" if rc == 0 else "failed"
    except FileNotFoundError as exc:
        captured.append(f"ERROR interpreter/module missing: {exc}")
        rc, status = 127, "failed"
    except subprocess.SubprocessError as exc:
        captured.append(f"ERROR {exc}")
        rc, status = 1, "failed"
    finally:
        release_lock(lock_path)

    # Housekeeping only after a clean run: purging runtime files under a failed
    # run destroys the exact artefacts needed to debug it.
    if status == "ok" and not args.no_cleanup:
        for script in meta["cleanup"]:
            path = pipeline_dir / script
            if path.is_file():
                subprocess.run([python, str(path)], cwd=str(pipeline_dir),
                               capture_output=True, text=True, timeout=900, check=False)

    uploads, errors = parse_output(captured)
    if status == "timeout":
        errors.append(f"killed after {timeout}s")
    summary = {
        "key": key, "label": meta["label"], "status": status,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "duration": human((datetime.now() - started).total_seconds()),
        "exit_code": rc, "uploads": uploads, "errors": errors,
        "log": str(log_path), "command": " ".join(argv),
    }
    write_summary(key, summary)
    text = report(meta, summary)
    print("\n" + text)
    if args.notify:
        telegram(text)
    return 0 if status == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Milo posting pipeline unattended.")
    parser.add_argument("pipeline", choices=sorted(PIPELINES))
    parser.add_argument("--videos", type=int, default=0, help="videos this run")
    parser.add_argument("--variant", default="", help="ranking only: normal|contrast|mixed")
    parser.add_argument("--timeout", type=int, default=0, help="seconds (default 4h)")
    parser.add_argument("--notify", dest="notify", action="store_true", default=True)
    parser.add_argument("--no-notify", dest="notify", action="store_false")
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("extra", nargs="*", help="extra args passed to src.main")
    args = parser.parse_args()
    return run(args.pipeline, args)


if __name__ == "__main__":
    raise SystemExit(main())
