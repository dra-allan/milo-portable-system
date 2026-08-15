"""SQLite state.

Three things have to be remembered across runs or the pipeline degrades into
re-posting itself:

* **which clips have been used** - by source URL *and* by perceptual hash. URL
  alone is not enough, because the same clip is reuploaded under a dozen
  different URLs across TikTok and YouTube, which is exactly how a channel ends
  up publishing the same moment twice.
* **which topic ran last** - so ``--mode auto`` rotates instead of hammering
  the first entry in the YAML.
* **what was built and uploaded** - for the caps and for post-hoc debugging of
  a bad video.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .utils import setup_logger

logger = setup_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS used_clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url    TEXT UNIQUE NOT NULL,
    topic         TEXT NOT NULL,
    phash         TEXT,
    title         TEXT,
    used_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_used_clips_phash ON used_clips(phash);

CREATE TABLE IF NOT EXISTS rejected_clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url    TEXT UNIQUE NOT NULL,
    topic         TEXT,
    reason        TEXT,
    rejected_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS builds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic         TEXT NOT NULL,
    title         TEXT NOT NULL,
    local_path    TEXT,
    plan_json     TEXT,
    status        TEXT NOT NULL DEFAULT 'built',
    youtube_id    TEXT,
    channel       TEXT,
    created_at    REAL NOT NULL,
    uploaded_at   REAL
);

CREATE TABLE IF NOT EXISTS topic_runs (
    topic         TEXT PRIMARY KEY,
    last_run      REAL NOT NULL,
    runs          INTEGER NOT NULL DEFAULT 0
);
"""


class RankingDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn) -> None:
        """Add columns added after the original schema shipped."""
        cols = {row['name'] for row in conn.execute(
            'PRAGMA table_info(builds)').fetchall()}
        if 'channel' not in cols:
            conn.execute('ALTER TABLE builds ADD COLUMN channel TEXT')

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- clip bookkeeping ----------------------------------------------
    def is_used(self, source_url: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT 1 FROM used_clips WHERE source_url = ?',
                (source_url,)).fetchone()
            return row is not None

    def is_rejected(self, source_url: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                'SELECT 1 FROM rejected_clips WHERE source_url = ?',
                (source_url,)).fetchone()
            return row is not None

    def mark_used(self, source_url: str, topic: str,
                  phash: Optional[str] = None,
                  title: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                'INSERT OR IGNORE INTO used_clips '
                '(source_url, topic, phash, title, used_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (source_url, topic, phash, title, time.time()))

    def mark_rejected(self, source_url: str, topic: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO rejected_clips '
                '(source_url, topic, reason, rejected_at) VALUES (?, ?, ?, ?)',
                (source_url, topic, reason, time.time()))

    def known_hashes(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT phash FROM used_clips WHERE phash IS NOT NULL'
            ).fetchall()
        return [r['phash'] for r in rows]

    # -- builds ---------------------------------------------------------
    def record_build(self, topic: str, title: str, local_path: str,
                     plan: Dict) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                'INSERT INTO builds (topic, title, local_path, plan_json, '
                'status, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (topic, title, local_path, json.dumps(plan, default=str),
                 'built', time.time()))
            return int(cur.lastrowid)

    def mark_uploaded(self, build_id: int, youtube_id: str,
                      channel: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE builds SET status='uploaded', youtube_id=?, "
                'uploaded_at=?, channel=COALESCE(?, channel) WHERE id=?',
                (youtube_id, time.time(), channel, build_id))

    def mark_failed(self, build_id: int, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE builds SET status=? WHERE id=?",
                (f'failed:{reason[:80]}', build_id))

    def pending_builds(self, limit: int = 10) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM builds WHERE status='built' "
                'ORDER BY created_at ASC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]

    def pending_builds_count(self) -> int:
        """Number of built-but-unpublished videos (the ready pool)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM builds WHERE status='built'"
            ).fetchone()
        return int(row['n'] if row else 0)

    def build_row(self, build_id: int) -> Optional[Dict]:
        """Full row for one build, or None if it no longer exists."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM builds WHERE id=?', (build_id,)).fetchone()
        return dict(row) if row else None

    def _day_start(self) -> float:
        """Start of the current local day as an epoch timestamp.

        The caps reset at this fixed boundary (local midnight) instead of a
        sliding 24h window, so a daily run at the same wall-clock time always
        sees a fresh budget rather than yesterday's uploads still counting.
        """
        now = datetime.now()
        return datetime(now.year, now.month, now.day).timestamp()

    def uploads_since(self, seconds: float) -> int:
        cutoff = max(time.time() - seconds, self._day_start())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM builds WHERE status='uploaded' "
                'AND uploaded_at >= ?', (cutoff,)).fetchone()
        return int(row['n'] if row else 0)

    def uploaded_count_for_channel_since(self, channel: str,
                                         seconds: float) -> int:
        """Uploads to one channel since the cap boundary.

        This is the per-channel cap primitive the upload policy expects. The
        channel is recorded on the build row at upload time; builds uploaded
        before the column shipped have NULL there and are not counted, which
        only ever makes the current window look emptier than it was. The
        boundary is start-of-local-day, so the cap resets every midnight.
        """
        cutoff = max(time.time() - seconds, self._day_start())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM builds WHERE status='uploaded' "
                'AND uploaded_at >= ? AND channel = ?',
                (cutoff, channel)).fetchone()
        return int(row['n'] if row else 0)

    # -- topic rotation -------------------------------------------------
    def touch_topic(self, topic: str) -> None:
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO topic_runs (topic, last_run, runs) '
                'VALUES (?, ?, 1) ON CONFLICT(topic) DO UPDATE SET '
                'last_run=excluded.last_run, runs=runs+1',
                (topic, time.time()))

    def next_topic(self, candidates: List[str]) -> Optional[str]:
        """Least-recently-run topic. Never-run topics come first."""
        if not candidates:
            return None
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT topic, last_run FROM topic_runs').fetchall()
        seen = {r['topic']: r['last_run'] for r in rows}
        unrun = [t for t in candidates if t not in seen]
        if unrun:
            return unrun[0]
        return min(candidates, key=lambda t: seen.get(t, 0.0))
