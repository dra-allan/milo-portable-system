"""SQLite state for the campaign clipper.

What has to survive across runs, and why:

* **which source windows already shipped** - campaign content folders are small
  (often a dozen files) and shared by every clipper on the campaign. Posting
  the same twenty seconds twice is the fastest way to get read as spam, which
  every one of these campaigns rejects explicitly.
* **the submission ledger** - a clip is not finished when it uploads, it is
  finished when the *link* is accepted by the campaign board. Upload and
  submission are separate failure points, so they are separate states:
  built -> validated -> uploaded -> submitted.
* **per-campaign daily counts** - so the caps are enforceable locally without
  scraping the board.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from .utils import setup_logger

logger = setup_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    url           TEXT,
    spec_json     TEXT,
    requirements  TEXT,
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    filename      TEXT,
    local_path    TEXT,
    duration      REAL,
    added_at      REAL NOT NULL,
    UNIQUE(campaign_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS used_windows (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    start_s       REAL NOT NULL,
    end_s         REAL NOT NULL,
    used_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_used_windows_lookup
    ON used_windows(campaign_id, fingerprint);

CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id   TEXT NOT NULL,
    fingerprint   TEXT,
    source_name   TEXT,
    start_s       REAL,
    duration      REAL,
    local_path    TEXT,
    caption       TEXT,
    overlay_text  TEXT,
    plan_json     TEXT,
    report_json   TEXT,
    status        TEXT NOT NULL DEFAULT 'built',
    account       TEXT,
    video_id      TEXT,
    video_url     TEXT,
    created_at    REAL NOT NULL,
    uploaded_at   REAL,
    submitted_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_clips_campaign ON clips(campaign_id);
"""


class ClipperDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- campaigns -------------------------------------------------------
    def upsert_campaign(self, campaign_id: str, name: str, url: str,
                        spec: Optional[Dict] = None,
                        requirements: Optional[str] = None) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO campaigns (id, name, url, spec_json, '
                'requirements, first_seen, last_seen) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET name=excluded.name, '
                'url=excluded.url, '
                'spec_json=COALESCE(excluded.spec_json, spec_json), '
                'requirements=COALESCE(excluded.requirements, requirements), '
                'last_seen=excluded.last_seen',
                (campaign_id, name, url,
                 json.dumps(spec, default=str) if spec else None,
                 requirements, now, now))

    def campaign_row(self, campaign_id: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM campaigns WHERE id=?',
                               (campaign_id,)).fetchone()
        return dict(row) if row else None

    def campaigns(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM campaigns ORDER BY last_seen DESC').fetchall()
        return [dict(r) for r in rows]

    # -- sources ---------------------------------------------------------
    def register_source(self, campaign_id: str, fingerprint: str,
                        filename: str, local_path: str,
                        duration: float) -> None:
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO sources (campaign_id, fingerprint, filename, '
                'local_path, duration, added_at) VALUES (?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(campaign_id, fingerprint) DO UPDATE SET '
                'local_path=excluded.local_path, '
                'duration=excluded.duration',
                (campaign_id, fingerprint, filename, local_path, duration,
                 time.time()))

    def sources(self, campaign_id: str) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT * FROM sources WHERE campaign_id=? '
                'ORDER BY added_at ASC', (campaign_id,)).fetchall()
        return [dict(r) for r in rows]

    # -- window reuse guard ----------------------------------------------
    def used_windows(self, campaign_id: str,
                     fingerprint: str) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT start_s, end_s FROM used_windows '
                'WHERE campaign_id=? AND fingerprint=?',
                (campaign_id, fingerprint)).fetchall()
        return [dict(r) for r in rows]

    def mark_window_used(self, campaign_id: str, fingerprint: str,
                         start_s: float, end_s: float) -> None:
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO used_windows (campaign_id, fingerprint, '
                'start_s, end_s, used_at) VALUES (?, ?, ?, ?, ?)',
                (campaign_id, fingerprint, start_s, end_s, time.time()))

    def window_overlaps(self, campaign_id: str, fingerprint: str,
                        start_s: float, end_s: float,
                        tolerance: float = 1.5) -> bool:
        """True when a window materially overlaps one already published.

        ``tolerance`` lets two clips share a second of tail without counting as
        a repost; more than that and it is visibly the same moment.
        """
        for row in self.used_windows(campaign_id, fingerprint):
            overlap = (min(end_s, row['end_s'])
                       - max(start_s, row['start_s']))
            if overlap > tolerance:
                return True
        return False

    # -- clips ------------------------------------------------------------
    def record_clip(self, campaign_id: str, plan: Dict,
                    local_path: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                'INSERT INTO clips (campaign_id, fingerprint, source_name, '
                'start_s, duration, local_path, caption, overlay_text, '
                'plan_json, status, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (campaign_id, plan.get('fingerprint'),
                 plan.get('source_name'), plan.get('start'),
                 plan.get('duration'), local_path, plan.get('caption'),
                 plan.get('overlay_text'),
                 json.dumps(plan, default=str), 'built', time.time()))
            return int(cur.lastrowid)

    def set_status(self, clip_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute('UPDATE clips SET status=? WHERE id=?',
                         (status, clip_id))

    def record_validation(self, clip_id: int, report: Dict,
                          passed: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                'UPDATE clips SET report_json=?, status=? WHERE id=?',
                (json.dumps(report, default=str),
                 'validated' if passed else 'rejected', clip_id))

    def update_caption(self, clip_id: int, caption: str) -> None:
        with self._connect() as conn:
            conn.execute('UPDATE clips SET caption=? WHERE id=?',
                         (caption, clip_id))

    def mark_uploaded(self, clip_id: int, video_id: str, video_url: str,
                      account: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE clips SET status='uploaded', video_id=?, "
                'video_url=?, account=COALESCE(?, account), uploaded_at=? '
                'WHERE id=?',
                (video_id, video_url, account, time.time(), clip_id))

    def mark_submitted(self, clip_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE clips SET status='submitted', submitted_at=? "
                'WHERE id=?', (time.time(), clip_id))

    def mark_failed(self, clip_id: int, reason: str) -> None:
        with self._connect() as conn:
            conn.execute('UPDATE clips SET status=? WHERE id=?',
                         (f'failed:{reason[:80]}', clip_id))

    def clip_row(self, clip_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM clips WHERE id=?',
                               (clip_id,)).fetchone()
        return dict(row) if row else None

    def clips_by_status(self, status: str, campaign_id: Optional[str] = None,
                        limit: int = 25) -> List[Dict]:
        sql = 'SELECT * FROM clips WHERE status=?'
        args: List = [status]
        if campaign_id:
            sql += ' AND campaign_id=?'
            args.append(campaign_id)
        sql += ' ORDER BY created_at ASC LIMIT ?'
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        return [dict(r) for r in rows]

    def uploads_since(self, seconds: float,
                      campaign_id: Optional[str] = None) -> int:
        cutoff = time.time() - seconds
        sql = ('SELECT COUNT(*) AS n FROM clips WHERE uploaded_at >= ? '
               "AND status IN ('uploaded','submitted')")
        args: List = [cutoff]
        if campaign_id:
            sql += ' AND campaign_id=?'
            args.append(campaign_id)
        with self._connect() as conn:
            row = conn.execute(sql, tuple(args)).fetchone()
        return int(row['n'] if row else 0)

    def stats(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT status, COUNT(*) AS n FROM clips '
                'GROUP BY status').fetchall()
        return {r['status']: int(r['n']) for r in rows}
