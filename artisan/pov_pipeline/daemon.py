#!/usr/bin/env python3
"""
daemon.py - the thing that makes the pipeline autonomous.
=========================================================

Two modes, both reachable from ``run_pov_pipeline.py``::

    python run_pov_pipeline.py --once      # one item, end to end (dev)
    python run_pov_pipeline.py --daemon    # loop forever (VPS)

What one pass does
------------------
1. Take the highest-scoring ``queued`` row from the sqlite queue. If the
   queue is empty, run discovery first and take the best of what lands.
2. Build (or reuse) the project folder: scrape the transcript unless
   ``00_SOURCE_SCRIPT.txt`` is already there.
3. Agents -> TTS -> images -> thumbnail -> assemble -> upload, notifying at
   every boundary.
4. Mark the queue row ``done``, or ``failed`` / ``needs_review`` with the
   reason, and move on. **A single bad project never stops the daemon.**

Bounds
------
* ``cadence.videos_per_day`` (default 1) caps how many NEW pipelines start
  per calendar day. The count lives in the ``pipeline_runs`` table, so a
  restart cannot reset it.
* ``cadence.posting_window`` (e.g. ``"09:00-21:00"``) and
  ``cadence.timezone`` gate when work may start. Outside the window the
  daemon sleeps and logs a heartbeat.
* One project at a time. No parallelism anywhere.

Shutdown
--------
SIGTERM and SIGINT set a stop flag that is checked between stages. The
current step finishes (killing a half-written ffmpeg render helps nobody),
the log is flushed, and the process exits 0.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import discovery
import povconfig
from discovery import PovDB
from povconfig import eprint, log_line

Notify = Callable[[str, str], None]

DEFAULT_INTERVAL_MIN = 30
DEFAULT_WINDOW = "09:00-21:00"
DEFAULT_VIDEOS_PER_DAY = 1

# Set by the signal handlers. Checked between stages and between ticks.
_STOP = False


def _handle_stop(signum, _frame) -> None:
    global _STOP
    _STOP = True
    name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    log_line("daemon.signal", f"{name} received - finishing the current step")


def install_signal_handlers() -> None:
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle_stop)
        except (ValueError, OSError):
            pass  # non-main thread, or a platform without it


def stopping() -> bool:
    return _STOP


# ---------------------------------------------------------------------------
# Posting window
# ---------------------------------------------------------------------------


def _tz(name: str | None):
    """zoneinfo for ``name``, or None for local time.

    An unresolved ``{{POV_TIMEZONE}}`` placeholder arrives here as None and
    the daemon simply uses the machine's local clock.
    """
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(name))
    except Exception as exc:
        eprint(f"[daemon] unknown timezone {name!r} ({exc}); using local time")
        return None


def in_posting_window(window: str, tz_name: str | None = None,
                      now: datetime | None = None) -> bool:
    """Is ``now`` inside ``"HH:MM-HH:MM"``? Windows may wrap past midnight."""
    text = (window or DEFAULT_WINDOW).strip()
    if not text or text in ("*", "24/7", "always"):
        return True
    try:
        start_s, _, end_s = text.partition("-")
        sh, sm = (int(x) for x in start_s.strip().split(":"))
        eh, em = (int(x) for x in end_s.strip().split(":"))
    except (ValueError, AttributeError):
        eprint(f"[daemon] bad posting_window {window!r}; treating as always open")
        return True

    current = now or datetime.now(_tz(tz_name))
    minutes = current.hour * 60 + current.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start <= end:
        return start <= minutes <= end
    return minutes >= start or minutes <= end     # wraps midnight


# ---------------------------------------------------------------------------
# One item, end to end
# ---------------------------------------------------------------------------


@dataclass
class PassResult:
    ok: bool = False
    acted: bool = False           # did we actually start a pipeline?
    project: str = ""
    url: str = ""
    reason: str = ""


def _pipeline():
    """Import the orchestrator lazily (it imports this module too)."""
    sys.path.insert(0, str(povconfig.HERE))
    import run_pov_pipeline as rp  # noqa: PLC0415  (deliberate late import)

    return rp


def ensure_project(item: dict, *, notify: Notify | None = None) -> Path | None:
    """Project folder for a queue item, scraping the transcript if needed.

    Resume-safe: an existing folder with a non-empty ``00_SOURCE_SCRIPT.txt``
    is reused as-is, so a re-run after a crash costs nothing.
    """
    rp = _pipeline()
    url = item.get("url") or ""
    video_id = item.get("video_id") or discovery.extract_video_id(url)
    root = povconfig.projects_dir()
    root.mkdir(parents=True, exist_ok=True)

    existing = sorted(p for p in root.glob(f"{video_id}_*") if p.is_dir()) if video_id else []
    project_dir = existing[0] if existing else root / rp.make_project_name(url)
    project_dir.mkdir(parents=True, exist_ok=True)

    source = project_dir / "00_SOURCE_SCRIPT.txt"
    if source.exists() and source.stat().st_size > 0:
        print(f"[daemon] reusing transcript in {project_dir.name}")
        return project_dir

    if rp.scrape_transcript(url, project_dir) is None:
        return None
    return project_dir


def process_one(item: dict, *, db: PovDB, notify: Notify | None = None,
                privacy: str | None = None, published_at: str | None = None,
                channel: str = "explaination", skip_upload: bool = False,
                dry_run_upload: bool = False,
                agent_opts: dict | None = None) -> PassResult:
    """Take one queue item all the way to a published video. Never raises."""
    import uploader

    rp = _pipeline()
    agent_opts = dict(agent_opts or {})
    video_id = item.get("video_id") or ""
    title = (item.get("title") or "")[:70]

    def _notify(event: str, message: str) -> None:
        if notify is None:
            return
        try:
            notify(event, message)
        except Exception as exc:
            eprint(f"[notify] {type(exc).__name__}: {exc}")

    log_line("daemon.pick", f"{video_id} (score {item.get('score')}) {title}")
    db.mark(video_id, "processing", bump_attempts=True)

    project_dir = ensure_project(item, notify=notify)
    if project_dir is None:
        reason = "transcript scrape failed"
        db.mark(video_id, "failed", reason=reason)
        log_line("daemon.fail", f"{video_id}: {reason}", level="error")
        _notify("agent.failed", f"POV {video_id}: {reason}")
        return PassResult(ok=False, acted=True, reason=reason)

    project = project_dir.name
    db.mark(video_id, "processing", project=project)
    run_id = db.start_run(video_id, project)

    # Everything the source video knows about itself, for the uploader and
    # the notifier. Additive - write_manifest never drops existing keys.
    try:
        from agent_runner import write_manifest

        write_manifest(project_dir, agents=rp.PIPELINE_AGENTS, stage="queued",
                       status="RUNNING", source_url=item.get("url", ""),
                       extra={"video_id": video_id,
                              "channel_id": item.get("channel_id", ""),
                              "niche": item.get("niche", ""),
                              "score": item.get("score", 0)})
    except Exception as exc:
        eprint(f"[daemon] manifest seed skipped: {type(exc).__name__}: {exc}")

    def _stop_here(stage: str) -> PassResult:
        db.mark(video_id, "queued", project=project,
                reason=f"stopped before {stage}")
        db.finish_run(run_id, "stopped", f"before {stage}")
        log_line("daemon.stopped", f"{project}: stopped before {stage}")
        return PassResult(ok=False, acted=True, project=project,
                          reason=f"stopped before {stage}")

    def _failed(stage: str, reason: str, event: str = "agent.failed") -> PassResult:
        db.mark(video_id, "failed", project=project, reason=f"{stage}: {reason}")
        db.finish_run(run_id, "failed", f"{stage}: {reason}")
        log_line("daemon.fail", f"{project} {stage}: {reason}", level="error")
        _notify(event, f"POV {project}: {stage} failed - {reason}")
        return PassResult(ok=False, acted=True, project=project, reason=reason)

    # -- agents (owns the script gate + NEEDS_REVIEW parking) --------------
    if stopping():
        return _stop_here("agents")
    if not rp.run_agents(project_dir, notify=notify, **agent_opts):
        db.mark(video_id, "needs_review", project=project,
                reason="agent chain parked the project")
        db.finish_run(run_id, "needs_review", "agent chain")
        return PassResult(ok=False, acted=True, project=project,
                          reason="agent chain needs review")

    # -- tts ---------------------------------------------------------------
    if stopping():
        return _stop_here("tts")
    if not rp.run_tts(project_dir):
        return _failed("tts", "Gemini TTS did not complete")

    # -- images ------------------------------------------------------------
    if stopping():
        return _stop_here("images")
    profiles = agent_opts.pop("flow_profiles", "") if agent_opts else ""
    browser_profile = agent_opts.pop("flow_browser_profile", "") if agent_opts else ""
    if not rp.run_flow_images(project_dir, profiles=profiles or "",
                              browser_profile=browser_profile or ""):
        # The Chrome bridge is the known VPS risk: fail loudly, never silently.
        return _failed("images",
                       "Google Flow image generation incomplete (is the Chrome "
                       "Browser Bridge up? try --check-profiles)",
                       event="images.failed")
    _notify("images.done", f"POV {project}: all segment images generated")

    # -- thumbnail ---------------------------------------------------------
    if stopping():
        return _stop_here("thumb")
    if not rp.run_thumbnail(project_dir, browser_profile=browser_profile or ""):
        # Non-fatal: the upload stage warns and posts without a thumbnail.
        log_line("daemon.warn", f"{project}: thumbnail generation failed",
                 level="error")

    # -- assemble ----------------------------------------------------------
    if stopping():
        return _stop_here("assemble")
    if not rp.run_assembler(project_dir):
        return _failed("assemble", "ffmpeg assembly did not complete")
    _notify("video.assembled", f"POV {project}: video assembled")

    if skip_upload:
        db.mark(video_id, "assembled", project=project)
        db.finish_run(run_id, "assembled", "upload skipped")
        log_line("daemon.done", f"{project}: assembled (upload skipped)")
        return PassResult(ok=True, acted=True, project=project,
                          reason="upload skipped")

    # -- upload ------------------------------------------------------------
    if stopping():
        return _stop_here("upload")
    result = uploader.upload_project(
        project_dir, channel=channel,
        privacy=privacy or "unlisted", published_at=published_at,
        dry_run=dry_run_upload, notify=notify)
    if not result.ok:
        return _failed("upload", result.reason or "unknown", event="upload.failed")

    db.mark(video_id, "done", project=project)
    db.finish_run(run_id, "done", result.url)
    log_line("daemon.done", f"{project}: {result.url or 'dry run'}")
    return PassResult(ok=True, acted=True, project=project, url=result.url)


# ---------------------------------------------------------------------------
# Passes and the loop
# ---------------------------------------------------------------------------


def run_once(cfg: dict | None = None, *, notify: Notify | None = None,
             db: PovDB | None = None, discover_if_empty: bool = True,
             ignore_window: bool = False, **kwargs) -> PassResult:
    """One pass: pick the best queued item and process it end to end."""
    cfg = cfg or povconfig.load_config()
    cadence = cfg.get("cadence") or {}
    store = db or PovDB()
    owns_db = db is None

    try:
        window = str(cadence.get("posting_window") or DEFAULT_WINDOW)
        tz_name = cadence.get("timezone")
        if not ignore_window and not in_posting_window(window, tz_name):
            log_line("daemon.idle", f"outside the posting window ({window})")
            return PassResult(ok=True, acted=False, reason="outside window")

        cap = int(cadence.get("videos_per_day") or DEFAULT_VIDEOS_PER_DAY)
        started = store.runs_today()
        if not ignore_window and started >= cap:
            log_line("daemon.idle",
                     f"daily cap reached ({started}/{cap} videos today)")
            return PassResult(ok=True, acted=False, reason="daily cap")

        item = store.next_item()
        if not item and discover_if_empty:
            log_line("daemon.discover", "queue empty - running discovery")
            discovery.discover(cfg, db=store, notify=notify)
            item = store.next_item()
        if not item:
            log_line("daemon.idle", "queue empty after discovery")
            if notify:
                notify("queue.empty", "POV queue is empty - nothing to process")
            return PassResult(ok=True, acted=False, reason="queue empty")

        defaults = cfg.get("defaults") or {}
        kwargs.setdefault("privacy", defaults.get("privacy") or "unlisted")
        kwargs.setdefault("published_at", defaults.get("published_at"))
        kwargs.setdefault("channel", defaults.get("upload_channel") or "explaination")
        return process_one(item, db=store, notify=notify, **kwargs)
    finally:
        if owns_db:
            store.close()


def run_daemon(cfg: dict | None = None, *, notify: Notify | None = None,
               interval_minutes: int | None = None, **kwargs) -> int:
    """Loop until SIGTERM. Returns the process exit code (0 on clean stop)."""
    cfg = cfg or povconfig.load_config()
    cadence = cfg.get("cadence") or {}
    interval = int(interval_minutes or cadence.get("daemon_interval_minutes")
                   or DEFAULT_INTERVAL_MIN)
    window = str(cadence.get("posting_window") or DEFAULT_WINDOW)
    cap = int(cadence.get("videos_per_day") or DEFAULT_VIDEOS_PER_DAY)

    install_signal_handlers()
    log_line("daemon.started",
             f"interval={interval}min window={window} cap={cap}/day "
             f"projects={povconfig.projects_dir()}")
    if notify:
        notify("daemon.started",
               f"POV daemon up: every {interval}min, window {window}, "
               f"{cap} video(s)/day")

    store = PovDB()
    tick = 0
    try:
        while not stopping():
            tick += 1
            counts = store.counts()
            log_line("daemon.heartbeat",
                     f"tick {tick} | queue "
                     + (", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                        or "empty")
                     + f" | {store.runs_today()}/{cap} today", echo=False)
            try:
                run_once(cfg, notify=notify, db=store, **kwargs)
            except Exception as exc:  # a bug must not end the daemon
                reason = f"{type(exc).__name__}: {exc}"
                log_line("daemon.fatal", reason, level="error")
                if notify:
                    notify("daemon.fatal", f"POV daemon error: {reason}")

            # Sleep in short slices so a SIGTERM is honoured promptly.
            deadline = time.time() + interval * 60
            while not stopping() and time.time() < deadline:
                time.sleep(min(5.0, max(0.5, deadline - time.time())))
    finally:
        store.close()
        log_line("daemon.stopped", f"clean shutdown after {tick} tick(s)")
        if notify:
            notify("daemon.stopped", f"POV daemon stopped after {tick} tick(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="daemon", description="POV pipeline daemon")
    ap.add_argument("--once", action="store_true", help="single pass, then exit")
    ap.add_argument("--daemon", action="store_true", help="loop (VPS mode)")
    ap.add_argument("--interval", type=int, default=None,
                    help="minutes between ticks (default: config)")
    ap.add_argument("--ignore-window", action="store_true",
                    help="--once only: run even outside the posting window")
    ap.add_argument("--skip-upload", action="store_true")
    ap.add_argument("--dry-run-upload", action="store_true")
    args = ap.parse_args(argv)

    from notify import make_notifier

    notifier = make_notifier()
    cfg = povconfig.load_config()
    opts = dict(skip_upload=args.skip_upload, dry_run_upload=args.dry_run_upload)

    if args.daemon:
        return run_daemon(cfg, notify=notifier, interval_minutes=args.interval,
                          **opts)
    result = run_once(cfg, notify=notifier, ignore_window=args.ignore_window,
                      **opts)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
