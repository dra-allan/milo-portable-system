#!/usr/bin/env python3
"""
discovery.py - curated-channel discovery, dedupe and the work queue.
====================================================================

Mirrors ``artisan/youtube-shorts-pipeline`` (``niches.yaml`` ->
``data/processed_videos.db`` dedupe by ``video_id``) for long-form POV
source material, and owns the queue the daemon (M4) consumes.

API usage and quota
-------------------
The YouTube Data API allows 10,000 units/day. ``search.list`` costs 100 units
per call and is **never used here**. Instead:

===========================  =====  ==========================================
call                         units  purpose
===========================  =====  ==========================================
``channels.list?forHandle``  1      @handle -> channel id + uploads playlist
``playlistItems.list``       1      one page (50) of newest uploads
``videos.list`` (<=50 ids)   1      exact duration, views, publishedAt
===========================  =====  ==========================================

A default run touches ``max_channels_per_run`` (5) channels at
``max_pages_per_channel`` (2) pages each, so:
``5 x (1 + 2) + a few videos.list calls`` is well under 300 units. The quota
guard estimates the spend before the first request and refuses to start when
it exceeds ``api.quota_budget`` (default 500).

Filter order (cheapest first)
-----------------------------
1. ``require_keywords`` - when the list is non-empty the title must contain
   **at least one** (OR match), otherwise the video is rejected.
2. ``negative_keywords`` - **any** match in the title rejects the video.
   The per-niche list and ``global_negative_keywords`` both apply.
3. Dedupe (ledger + live queue + project folders on disk).
4. ``videos.list`` for the survivors only, then the hard filters:
   ``min_duration`` / ``max_duration`` (seconds), ``min_views``,
   ``preferred_upload_days`` (recency window).
5. ``min_score`` on the computed score.
6. ``max_videos`` per channel, per run.

Scoring (deterministic, documented, no magic)
---------------------------------------------
::

    score = 0.35                                        # base: it survived
          + 0.35 * min(keyword_hits, 3) / 3             # topical fit
          + 0.15 * max(0, 1 - age_days / recency_window)  # freshness
          + 0.15 * min(1, log10(max(views, 1)) / 6)     # 1M views -> full marks

Range 0.35-1.00. With the default ``min_score`` of 0.50 a video needs real
keyword hits, or strong views plus recency, to enter the queue.

Storage
-------
One sqlite file, ``<POV_DATA_DIR>/processed_videos.db`` (created on first
run). The queue is a **table**, not a JSON file, so concurrent readers and
the daemon can never half-write it:

``processed_videos``  the dedupe ledger, keyed by ``video_id``
``pov_queue``         ordered work items (``status`` queued/processing/done/failed)
``pipeline_runs``     one row per started pipeline (the M4 daily-cap ledger)
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import povconfig
from povconfig import eprint, log_line

API_ROOT = "https://www.googleapis.com/youtube/v3"
HTTP_TIMEOUT = 20
DEFAULT_MAX_CHANNELS_PER_RUN = 5
DEFAULT_QUOTA_BUDGET = 500
PAGE_SIZE = 50

Notify = Callable[[str, str], None]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """One video that survived discovery, ready to be queued."""

    video_id: str
    url: str
    channel_id: str
    channel_handle: str
    niche: str
    title: str
    score: float = 0.0
    duration_s: int = 0
    views: int = 0
    published_at: str = ""

    def row(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "url": self.url,
            "channel_id": self.channel_id,
            "channel_handle": self.channel_handle,
            "niche": self.niche,
            "title": self.title,
            "score": round(self.score, 4),
            "duration_s": self.duration_s,
            "views": self.views,
            "published_at": self.published_at,
        }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_videos (
    video_id     TEXT PRIMARY KEY,
    url          TEXT,
    channel_id   TEXT,
    channel_handle TEXT,
    niche        TEXT,
    title        TEXT,
    status       TEXT NOT NULL DEFAULT 'seen',
    project      TEXT,
    first_seen   TEXT NOT NULL,
    last_update  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processed_status ON processed_videos(status);

CREATE TABLE IF NOT EXISTS pov_queue (
    video_id     TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    channel_id   TEXT,
    niche        TEXT,
    title        TEXT,
    score        REAL NOT NULL DEFAULT 0,
    enqueued_at  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    attempts     INTEGER NOT NULL DEFAULT 0,
    project      TEXT,
    reason       TEXT,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON pov_queue(status, score DESC);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT,
    project      TEXT,
    day          TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_day ON pipeline_runs(day);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


class PovDB:
    """Thin sqlite wrapper. Same file layout idea as the shorts pipeline."""

    def __init__(self, path: Path | None = None):
        self.path = path or (povconfig.data_dir() / "processed_videos.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "PovDB":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- dedupe ------------------------------------------------------------

    def seen_ids(self) -> set[str]:
        """Every video id the pipeline already knows about (any status)."""
        rows = self.conn.execute("SELECT video_id FROM processed_videos").fetchall()
        queued = self.conn.execute(
            "SELECT video_id FROM pov_queue WHERE status IN "
            "('queued','processing','done')").fetchall()
        return {r["video_id"] for r in rows} | {r["video_id"] for r in queued}

    def note_seen(self, cand: Candidate, status: str = "seen") -> None:
        now = _now()
        self.conn.execute(
            "INSERT INTO processed_videos (video_id, url, channel_id, "
            "channel_handle, niche, title, status, first_seen, last_update) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET "
            "status=excluded.status, last_update=excluded.last_update",
            (cand.video_id, cand.url, cand.channel_id, cand.channel_handle,
             cand.niche, cand.title, status, now, now),
        )
        self.conn.commit()

    # -- queue -------------------------------------------------------------

    def enqueue(self, cand: Candidate) -> bool:
        """Add one candidate. Returns False when it was already known."""
        now = _now()
        try:
            self.conn.execute(
                "INSERT INTO pov_queue (video_id, url, channel_id, niche, title, "
                "score, enqueued_at, status, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'queued',?)",
                (cand.video_id, cand.url, cand.channel_id, cand.niche,
                 cand.title, round(cand.score, 4), now, now),
            )
        except sqlite3.IntegrityError:
            return False
        self.note_seen(cand, status="queued")
        self.conn.commit()
        return True

    def next_item(self) -> dict | None:
        """Highest-scoring queued item, oldest first on a tie."""
        row = self.conn.execute(
            "SELECT * FROM pov_queue WHERE status='queued' "
            "ORDER BY score DESC, enqueued_at ASC LIMIT 1").fetchone()
        return dict(row) if row else None

    def queue(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM pov_queue WHERE status=? "
                "ORDER BY score DESC, enqueued_at ASC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM pov_queue ORDER BY score DESC, enqueued_at ASC "
                "LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) c FROM pov_queue GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    def mark(self, video_id: str, status: str, *, project: str | None = None,
             reason: str = "", bump_attempts: bool = False) -> None:
        """Move a queue row to a new status. Also mirrors it into the ledger."""
        now = _now()
        sets = ["status=?", "updated_at=?"]
        args: list[Any] = [status, now]
        if project is not None:
            sets.append("project=?")
            args.append(project)
        if reason:
            sets.append("reason=?")
            args.append(reason[:500])
        if bump_attempts:
            sets.append("attempts=attempts+1")
        args.append(video_id)
        self.conn.execute(f"UPDATE pov_queue SET {', '.join(sets)} WHERE video_id=?", args)
        self.conn.execute(
            "UPDATE processed_videos SET status=?, last_update=?"
            + (", project=?" if project is not None else "")
            + " WHERE video_id=?",
            ((status, now, project, video_id) if project is not None
             else (status, now, video_id)),
        )
        self.conn.commit()

    def mark_url_processed(self, url: str, project: str) -> None:
        """Manual ``--input <url>`` runs close their queue row, if any."""
        vid = extract_video_id(url)
        if not vid:
            return
        now = _now()
        self.conn.execute(
            "INSERT INTO processed_videos (video_id, url, status, project, "
            "first_seen, last_update) VALUES (?,?,'processing',?,?,?) "
            "ON CONFLICT(video_id) DO UPDATE SET status='processing', "
            "project=excluded.project, last_update=excluded.last_update",
            (vid, url, project, now, now),
        )
        self.conn.execute(
            "UPDATE pov_queue SET status='processing', project=?, updated_at=? "
            "WHERE video_id=?", (project, now, vid))
        self.conn.commit()

    # -- daily cap ---------------------------------------------------------

    def runs_today(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM pipeline_runs WHERE day=?", (_today(),)
        ).fetchone()
        return int(row["c"] if row else 0)

    def start_run(self, video_id: str, project: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO pipeline_runs (video_id, project, day, started_at, status) "
            "VALUES (?,?,?,?, 'running')",
            (video_id, project, _today(), _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, status: str, reason: str = "") -> None:
        self.conn.execute(
            "UPDATE pipeline_runs SET status=?, reason=?, finished_at=? WHERE id=?",
            (status, reason[:500], _now(), run_id),
        )
        self.conn.commit()


# ---------------------------------------------------------------------------
# YouTube Data API (stdlib only)
# ---------------------------------------------------------------------------


class QuotaExceeded(RuntimeError):
    """Raised before spending units when the guard says the run is too big."""


class YouTubeAPI:
    """Minimal YouTube Data API v3 client over urllib. Counts its own units."""

    def __init__(self, api_key: str, *, budget: int = DEFAULT_QUOTA_BUDGET,
                 guard: bool = True):
        self.api_key = api_key
        self.budget = budget
        self.guard = guard
        self.units = 0

    def _get(self, endpoint: str, params: dict[str, Any], cost: int = 1) -> dict:
        if self.guard and self.units + cost > self.budget:
            raise QuotaExceeded(
                f"quota budget {self.budget} units would be exceeded "
                f"(spent {self.units}, next call costs {cost})")
        query = dict(params)
        query["key"] = self.api_key
        url = f"{API_ROOT}/{endpoint}?" + urllib.parse.urlencode(query, doseq=True)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
            raise RuntimeError(f"{endpoint} HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{endpoint} failed: {exc}") from exc
        self.units += cost
        return payload

    def resolve_handle(self, handle: str) -> tuple[str, str] | None:
        """@handle -> (channel_id, uploads_playlist_id), or None."""
        clean = handle.strip()
        if not clean.startswith("@"):
            clean = "@" + clean
        data = self._get("channels", {
            "part": "contentDetails",
            "forHandle": clean,
            "maxResults": 1,
        })
        items = data.get("items") or []
        if not items:
            return None
        uploads = (((items[0].get("contentDetails") or {}).get("relatedPlaylists")
                    or {}).get("uploads"))
        cid = items[0].get("id") or ""
        return (cid, uploads) if uploads else None

    def playlist_page(self, playlist_id: str, page_token: str = "") -> dict:
        params: dict[str, Any] = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": PAGE_SIZE,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("playlistItems", params)

    def video_details(self, video_ids: Sequence[str]) -> dict[str, dict]:
        """One call per 50 ids. Returns ``{video_id: {...}}``."""
        out: dict[str, dict] = {}
        ids = list(video_ids)
        for start in range(0, len(ids), PAGE_SIZE):
            batch = ids[start:start + PAGE_SIZE]
            data = self._get("videos", {
                "part": "contentDetails,statistics,snippet",
                "id": ",".join(batch),
                "maxResults": PAGE_SIZE,
            })
            for item in data.get("items") or []:
                out[item.get("id", "")] = item
        return out


_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?$")


def parse_duration(iso: str) -> int:
    """ISO-8601 duration (``PT12M34S``) -> seconds. Unparseable -> 0."""
    m = _DURATION_RE.match((iso or "").strip())
    if not m:
        return 0
    parts = {k: int(v) for k, v in m.groupdict(default="0").items()}
    return (parts["days"] * 86400 + parts["hours"] * 3600
            + parts["minutes"] * 60 + parts["seconds"])


def parse_published(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


_VIDEO_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def extract_video_id(url: str) -> str:
    m = _VIDEO_ID_RE.search(url or "")
    return m.group(1) if m else ""


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------
# Filtering + scoring
# ---------------------------------------------------------------------------


def title_allowed(title: str, niche: dict) -> tuple[bool, str]:
    """Cheap title gate. Returns ``(allowed, reason_when_rejected)``."""
    low = (title or "").lower()
    negatives = niche.get("negative_keywords") or []
    for bad in negatives:
        if bad and bad in low:
            return False, f"negative keyword: {bad}"
    required = niche.get("require_keywords") or []
    if required and not any(req in low for req in required if req):
        return False, "no require_keywords match"
    return True, ""


def keyword_hits(title: str, niche: dict) -> int:
    low = (title or "").lower()
    return sum(1 for kw in (niche.get("keywords") or []) if kw and kw in low)


def compute_score(title: str, niche: dict, *, views: int,
                  published: datetime | None) -> float:
    """Deterministic 0.35-1.00 score. See the module docstring for the formula."""
    score = 0.35
    score += 0.35 * (min(keyword_hits(title, niche), 3) / 3.0)

    window = float(niche.get("preferred_upload_days") or 60) or 60.0
    if published is not None:
        age_days = (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days
        score += 0.15 * max(0.0, 1.0 - (age_days / window))

    score += 0.15 * min(1.0, math.log10(max(views, 1)) / 6.0)
    return round(min(1.0, score), 4)


def existing_project_ids(projects_root: Path) -> set[str]:
    """Video ids already represented by a folder on disk.

    ``make_project_name()`` names projects ``<video_id>_<YYYYMMDD>``, so the
    11-char prefix before the first underscore is the id. This catches work
    done before the ledger existed, or on another machine.
    """
    found: set[str] = set()
    if not projects_root.is_dir():
        return found
    try:
        for entry in projects_root.iterdir():
            if not entry.is_dir():
                continue
            head = entry.name.split("_", 1)[0]
            if len(head) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", head):
                found.add(head)
    except OSError as exc:
        eprint(f"[discover] could not scan {projects_root}: {exc}")
    return found


# ---------------------------------------------------------------------------
# The discovery run
# ---------------------------------------------------------------------------


def estimate_units(channel_count: int, pages: int) -> int:
    """Units a run will cost: handle resolve + pages + a videos.list per page."""
    return channel_count * (1 + pages) + channel_count * pages


def discover(cfg: dict | None = None, *, niches: Iterable[str] | None = None,
             channels: Iterable[str] | None = None,
             max_channels: int | None = None,
             db: PovDB | None = None,
             notify: Notify | None = None,
             dry_run: bool = False) -> list[Candidate]:
    """Run discovery and append everything that survives to the queue.

    Returns the accepted candidates (already enqueued unless ``dry_run``).
    Never raises for a bad channel: unresolvable handles are logged and
    skipped. A quota-guard refusal or a missing API key returns an empty
    list after logging.
    """
    cfg = cfg or povconfig.load_config()
    api_key = povconfig.youtube_api_key(cfg)
    if not api_key:
        log_line("discover.abort",
                 "no YouTube API key (set YOUTUBE_API_KEY or api.youtube_api_key)",
                 level="error")
        return []

    api_cfg = cfg.get("api") or {}
    pages = int(api_cfg.get("max_pages_per_channel") or 2)
    budget = int(api_cfg.get("quota_budget") or DEFAULT_QUOTA_BUDGET)
    guard = bool(api_cfg.get("quota_guard", True))
    per_run = int(max_channels or api_cfg.get("max_channels_per_run")
                  or DEFAULT_MAX_CHANNELS_PER_RUN)

    selected = cfg.get("niches") or {}
    if niches:
        wanted = {n.strip() for n in niches if n.strip()}
        missing = wanted - set(selected)
        for name in sorted(missing):
            log_line("discover.skip", f"unknown niche: {name}", level="error")
        selected = {k: v for k, v in selected.items() if k in wanted}
    if not selected:
        log_line("discover.abort", "no niches to process", level="error")
        return []

    # Build the channel worklist: round-robin across niches so one big niche
    # cannot starve the others when max_channels_per_run bites.
    handle_filter = {c.strip().lstrip("@").lower() for c in (channels or []) if c.strip()}
    worklist: list[tuple[str, dict, str]] = []
    pools = {name: list(n.get("channels") or []) for name, n in selected.items()}
    while any(pools.values()):
        for name, pool in pools.items():
            if not pool:
                continue
            handle = pool.pop(0)
            if handle_filter and handle.lstrip("@").lower() not in handle_filter:
                continue
            worklist.append((name, selected[name], handle))
    worklist = worklist[:per_run]

    if not worklist:
        log_line("discover.abort", "no channels matched the filters", level="error")
        return []

    estimate = estimate_units(len(worklist), pages)
    if guard and estimate > budget:
        log_line("discover.abort",
                 f"quota guard: estimated {estimate} units > budget {budget}. "
                 f"Lower max_channels_per_run or max_pages_per_channel.",
                 level="error")
        return []

    log_line("discover.start",
             f"{len(worklist)} channel(s) across {len(selected)} niche(s), "
             f"~{estimate} units budgeted (cap {budget})")

    api = YouTubeAPI(api_key, budget=budget, guard=guard)
    store = db or PovDB()
    owns_db = db is None
    projects_root = povconfig.projects_dir()
    already = store.seen_ids() | existing_project_ids(projects_root)

    accepted: list[Candidate] = []
    rejected = 0

    try:
        for niche_name, niche, handle in worklist:
            try:
                resolved = api.resolve_handle(handle)
            except QuotaExceeded as exc:
                log_line("discover.quota", str(exc), level="error")
                break
            except RuntimeError as exc:
                log_line("discover.channel_error", f"{handle}: {exc}", level="error")
                continue
            if not resolved:
                # An unverifiable handle is a config problem, not a crash.
                log_line("discover.handle_unresolved",
                         f"{handle} ({niche_name}) - skipped", level="error")
                continue
            channel_id, uploads = resolved

            # --- cheap pass: titles only -------------------------------
            shortlist: list[Candidate] = []
            token = ""
            for _page in range(max(1, pages)):
                try:
                    data = api.playlist_page(uploads, token)
                except QuotaExceeded as exc:
                    log_line("discover.quota", str(exc), level="error")
                    token = ""
                    break
                except RuntimeError as exc:
                    log_line("discover.page_error", f"{handle}: {exc}", level="error")
                    break
                for item in data.get("items") or []:
                    snippet = item.get("snippet") or {}
                    details = item.get("contentDetails") or {}
                    vid = details.get("videoId") or ""
                    title = snippet.get("title") or ""
                    if not vid or vid in already:
                        continue
                    ok, why = title_allowed(title, niche)
                    if not ok:
                        rejected += 1
                        continue
                    shortlist.append(Candidate(
                        video_id=vid, url=watch_url(vid), channel_id=channel_id,
                        channel_handle=handle, niche=niche_name, title=title,
                    ))
                token = data.get("nextPageToken") or ""
                if not token:
                    break

            if not shortlist:
                log_line("discover.channel", f"{handle}: no title-pass candidates")
                continue

            # --- expensive pass: exact duration / views / recency -------
            try:
                details = api.video_details([c.video_id for c in shortlist])
            except QuotaExceeded as exc:
                log_line("discover.quota", str(exc), level="error")
                break
            except RuntimeError as exc:
                log_line("discover.details_error", f"{handle}: {exc}", level="error")
                continue

            min_dur = int(niche.get("min_duration") or 0)
            max_dur = int(niche.get("max_duration") or 10 ** 9)
            min_views = int(niche.get("min_views") or 0)
            window = int(niche.get("preferred_upload_days") or 60)
            min_score = float(niche.get("min_score") or 0.0)
            cutoff = datetime.now(timezone.utc) - timedelta(days=window)

            kept: list[Candidate] = []
            for cand in shortlist:
                item = details.get(cand.video_id)
                if not item:
                    continue
                content = item.get("contentDetails") or {}
                stats = item.get("statistics") or {}
                snippet = item.get("snippet") or {}
                cand.duration_s = parse_duration(content.get("duration", ""))
                try:
                    cand.views = int(stats.get("viewCount") or 0)
                except (TypeError, ValueError):
                    cand.views = 0
                cand.published_at = snippet.get("publishedAt") or ""
                published = parse_published(cand.published_at)

                if not (min_dur <= cand.duration_s <= max_dur):
                    rejected += 1
                    continue
                if cand.views < min_views:
                    rejected += 1
                    continue
                if published is not None and published < cutoff:
                    rejected += 1
                    continue

                cand.score = compute_score(cand.title, niche, views=cand.views,
                                           published=published)
                if cand.score < min_score:
                    rejected += 1
                    continue
                kept.append(cand)

            kept.sort(key=lambda c: c.score, reverse=True)
            kept = kept[:int(niche.get("max_videos") or 2)]
            for cand in kept:
                if dry_run:
                    accepted.append(cand)
                    already.add(cand.video_id)
                    continue
                if store.enqueue(cand):
                    accepted.append(cand)
                    already.add(cand.video_id)
                    log_line("discover.enqueue",
                             f"{cand.video_id} [{cand.niche}] score={cand.score} "
                             f"{cand.title[:70]}")

        log_line("discover.done",
                 f"{len(accepted)} queued, {rejected} rejected, "
                 f"{api.units} API units spent")
        if notify and accepted:
            try:
                notify("discover.done",
                       f"POV discovery: {len(accepted)} new video(s) queued "
                       f"({api.units} API units)")
            except Exception as exc:
                eprint(f"[notify] {type(exc).__name__}: {exc}")
    finally:
        if owns_db:
            store.close()

    return accepted


def print_summary(accepted: Sequence[Candidate], db: PovDB | None = None) -> None:
    """Human-readable discovery summary."""
    print("\n" + "=" * 60)
    print("  DISCOVERY SUMMARY")
    print("=" * 60)
    if not accepted:
        print("  nothing new (everything already seen, or filters rejected it)")
    for cand in accepted:
        print(f"  {cand.score:.2f}  [{cand.niche}] {cand.video_id}  "
              f"{cand.duration_s // 60}m  {cand.views:,} views")
        print(f"        {cand.title[:80]}")
    store = db or PovDB()
    try:
        counts = store.counts()
        total = sum(counts.values())
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"
        print(f"\n  queue: {total} row(s) ({detail})")
        print(f"  db:    {store.path}")
    finally:
        if db is None:
            store.close()
    print("=" * 60)
