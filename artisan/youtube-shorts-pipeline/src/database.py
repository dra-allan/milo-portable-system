"""SQLite tracking for processed sources and generated clips.

The schema shipped in data/processed_videos.sql but nothing ever wrote to it:
main.py had a bare "TODO: Record in database" and the dedup check the README
describes ("Filters out already processed videos using local SQLite database")
was never implemented. That meant re-running the pipeline reprocessed the same
source video and would re-upload duplicate Shorts.

Also fixed: the original schema declares processed_videos.channel_id and
published_at as NOT NULL, but yt-dlp metadata frequently lacks them, so a
naive INSERT would fail. Columns are defaulted here instead.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .utils import setup_logger
    from .config import config
except ImportError:  # pragma: no cover
    from utils import setup_logger
    from config import config

logger = setup_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_video_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    channel_id TEXT NOT NULL DEFAULT '',
    published_at TIMESTAMP,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    niche TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS generated_shorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    score REAL,
    local_path TEXT,
    youtube_short_id TEXT UNIQUE,
    uploaded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    title TEXT NOT NULL DEFAULT '',
    upload_channel TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued'
);

CREATE INDEX IF NOT EXISTS idx_processed_videos_id
    ON processed_videos(youtube_video_id);
CREATE INDEX IF NOT EXISTS idx_generated_shorts_source
    ON generated_shorts(source_video_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_generated_shorts_segment
    ON generated_shorts(source_video_id, segment_index);
CREATE INDEX IF NOT EXISTS idx_generated_shorts_status
    ON generated_shorts(status);
CREATE INDEX IF NOT EXISTS idx_generated_shorts_created_at
    ON generated_shorts(created_at);

CREATE TABLE IF NOT EXISTS short_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_short_id TEXT UNIQUE NOT NULL,
    source_video_id TEXT NOT NULL DEFAULT '',
    segment_index INTEGER,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    favorites INTEGER NOT NULL DEFAULT 0,
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_short_performance_views
    ON short_performance(views DESC);
CREATE INDEX IF NOT EXISTS idx_short_performance_retrieved
    ON short_performance(retrieved_at);
"""


class PipelineDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else Path(config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        try:
            with self._connect() as conn:
                conn.executescript(SCHEMA)
                self._migrate(conn)
            logger.debug("Database ready at %s", self.db_path)
        except Exception as exc:
            logger.error("Could not initialise database %s: %s", self.db_path, exc)

    @staticmethod
    def _migrate(conn):
        """Add columns that older databases on disk are missing."""
        existing = {row['name'] for row in conn.execute("PRAGMA table_info(generated_shorts)")}
        for column, ddl in (
            ('score', 'REAL'),
            ('local_path', 'TEXT'),
            ('created_at', 'TIMESTAMP'),
            ('upload_channel', "TEXT NOT NULL DEFAULT ''"),
            ('status', "TEXT NOT NULL DEFAULT 'queued'"),
        ):
            if column not in existing:
                try:
                    conn.execute(f"ALTER TABLE generated_shorts ADD COLUMN {column} {ddl}")
                    logger.info("Migrated generated_shorts: added %s", column)
                except sqlite3.OperationalError:
                    pass
        # Add indexes if missing
        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_generated_shorts_status ON generated_shorts(status)",
            "CREATE INDEX IF NOT EXISTS idx_generated_shorts_created_at ON generated_shorts(created_at)",
        ):
            try:
                conn.execute(idx_sql)
            except sqlite3.OperationalError:
                pass

    # ------------------------------------------------------------------
    def is_video_processed(self, video_id: str) -> bool:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM processed_videos WHERE youtube_video_id = ?",
                    (video_id,),
                ).fetchone()
                return row is not None
        except Exception as exc:
            logger.warning("Dedup check failed for %s: %s", video_id, exc)
            return False

    def record_video(self, video_id: str, title: str, niche: str,
                     duration: int = 0, channel_id: str = '',
                     published_at: Optional[str] = None) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO processed_videos
                       (youtube_video_id, title, channel_id, published_at, niche, duration)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(youtube_video_id) DO UPDATE SET
                           title=excluded.title,
                           niche=excluded.niche,
                           duration=excluded.duration,
                           processed_at=CURRENT_TIMESTAMP""",
                    (video_id, title or '', channel_id or '', published_at,
                     niche or '', int(duration or 0)),
                )
        except Exception as exc:
            logger.warning("Could not record video %s: %s", video_id, exc)

    def record_short(self, source_video_id: str, segment_index: int,
                     start_time: float, end_time: float, title: str,
                     local_path: str = '', score: Optional[float] = None,
                     youtube_short_id: Optional[str] = None) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO generated_shorts
                       (source_video_id, segment_index, start_time, end_time,
                        score, local_path, youtube_short_id, uploaded_at, title)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source_video_id, segment_index) DO UPDATE SET
                           start_time=excluded.start_time,
                           end_time=excluded.end_time,
                           score=excluded.score,
                           local_path=excluded.local_path,
                           youtube_short_id=COALESCE(excluded.youtube_short_id,
                                                     generated_shorts.youtube_short_id),
                           uploaded_at=COALESCE(excluded.uploaded_at,
                                                generated_shorts.uploaded_at),
                           title=excluded.title""",
                    (source_video_id, int(segment_index), float(start_time),
                     float(end_time), score, local_path, youtube_short_id,
                     'CURRENT_TIMESTAMP' if youtube_short_id else None, title or ''),
                )
        except Exception as exc:
            logger.warning(
                "Could not record short %s#%s: %s", source_video_id, segment_index, exc
            )

    def mark_short_uploaded(self, source_video_id: str, segment_index: int,
                            youtube_short_id: str, channel: str = '') -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE generated_shorts
                       SET youtube_short_id = ?, uploaded_at = CURRENT_TIMESTAMP,
                           upload_channel = ?
                       WHERE source_video_id = ? AND segment_index = ?""",
                    (youtube_short_id, channel or '', source_video_id, int(segment_index)),
                )
        except Exception as exc:
            logger.warning("Could not mark short uploaded: %s", exc)

    def rendered_segment_indices(self, source_video_id: str) -> set:
        """Which 1-based segment indices already have a generated_short row.

        Used by ``--render-more`` so it can resume from the next unrendered
        clip in a cached plan instead of re-rendering (or skipping) clips that
        already exist on disk.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT segment_index FROM generated_shorts "
                    "WHERE source_video_id = ?",
                    (source_video_id,),
                ).fetchall()
            return {int(r[0]) for r in rows}
        except Exception as exc:
            logger.warning("Could not read rendered segments for %s: %s",
                           source_video_id, exc)
            return set()

    def stats(self) -> Dict[str, int]:
        out = {'processed_videos': 0, 'generated_shorts': 0, 'uploaded_shorts': 0}
        try:
            with self._connect() as conn:
                out['processed_videos'] = conn.execute(
                    "SELECT COUNT(*) FROM processed_videos").fetchone()[0]
                out['generated_shorts'] = conn.execute(
                    "SELECT COUNT(*) FROM generated_shorts").fetchone()[0]
                out['uploaded_shorts'] = conn.execute(
                    "SELECT COUNT(*) FROM generated_shorts "
                    "WHERE youtube_short_id IS NOT NULL").fetchone()[0]
        except Exception as exc:
            logger.warning("Could not read stats: %s", exc)
        return out

    def recent_shorts(self, limit: int = 20) -> List[Dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT source_video_id, segment_index, start_time, end_time,
                              score, local_path, youtube_short_id, title
                       FROM generated_shorts ORDER BY id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Could not read recent shorts: %s", exc)
            return []

    def uploaded_count_for_source_since(self, source_video_id: str,
                                        hours: int = 24) -> int:
        """How many Shorts from a source video were uploaded today.

        Drives the per-source daily cap (Allan's cadence rule: max 3 clips per
        source video per day). Counts rows uploaded since the start of the
        current local day, so the cap resets every day at midnight -- a fixed
        boundary, not a sliding 24h window (a sliding window kept the cap
        looking full because runs fire at the same wall-clock time each day).
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) FROM generated_shorts
                       WHERE source_video_id = ?
                         AND youtube_short_id IS NOT NULL
                         AND uploaded_at IS NOT NULL
                         AND datetime(uploaded_at, 'localtime')
                             >= datetime('now', 'localtime', 'start of day')""",
                    (source_video_id,),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            logger.warning("Could not count uploaded shorts for %s: %s",
                           source_video_id, exc)
            return 0

    def uploaded_count_for_channel_since(self, channel: str,
                                         hours: int = 24) -> int:
        """How many Shorts a channel has published today.

        Drives the per-channel daily cap (Allan's rule: max 5 shorts per
        channel per day). Counts rows where youtube_short_id is set AND
        upload_channel matches, since the start of the current local day. The
        cap therefore resets at every local midnight -- a fixed boundary, so a
        daily 09:00 run always sees a fresh budget instead of yesterday's
        uploads still inside a sliding 24h window.
        """
        if not channel:
            return 0
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) FROM generated_shorts
                       WHERE upload_channel = ?
                         AND youtube_short_id IS NOT NULL
                         AND uploaded_at IS NOT NULL
                         AND datetime(uploaded_at, 'localtime')
                             >= datetime('now', 'localtime', 'start of day')""",
                    (channel,),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            logger.warning("Could not count uploaded shorts for channel %s: %s",
                           channel, exc)
            return 0

    def unuploaded_shorts(self, limit: int = 50) -> List[Dict]:
        """Shorts that were rendered but never uploaded, oldest first.

        This is the "old shorts" side of the new-mixed-with-old upload queue:
        when a run's cap isn't used up by fresh clips, the oldest rendered-but-
        unpublished clips are published to keep the backlog draining.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT g.source_video_id, g.segment_index, g.start_time,
                              g.end_time, g.score, g.local_path, g.title,
                              COALESCE(p.niche, '') AS niche,
                              COALESCE(p.title, '') AS source_title
                       FROM generated_shorts g
                       LEFT JOIN processed_videos p
                              ON p.youtube_video_id = g.source_video_id
                       WHERE g.youtube_short_id IS NULL
                         AND g.local_path IS NOT NULL AND g.local_path != ''
                       ORDER BY g.id ASC LIMIT ?""",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Could not read un-uploaded shorts: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Performance feedback loop: (clip, source, metrics) triples.
    # ------------------------------------------------------------------
    def record_performance(self, youtube_short_id: str, source_video_id: str,
                           segment_index: Optional[int] = None, views: int = 0,
                           likes: int = 0, comments: int = 0,
                           favorites: int = 0) -> None:
        """Upsert one metrics snapshot for an uploaded short.

        This is the row that turns the pipeline into a learning system:
        without it nothing downstream can answer "which clips do better".
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO short_performance
                       (youtube_short_id, source_video_id, segment_index,
                        views, likes, comments, favorites, retrieved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(youtube_short_id) DO UPDATE SET
                           views=excluded.views,
                           likes=excluded.likes,
                           comments=excluded.comments,
                           favorites=excluded.favorites,
                           retrieved_at=CURRENT_TIMESTAMP""",
                    (youtube_short_id, source_video_id or '', segment_index,
                     int(views or 0), int(likes or 0), int(comments or 0),
                     int(favorites or 0)),
                )
        except Exception as exc:
            logger.warning("Could not record performance for %s: %s",
                           youtube_short_id, exc)

    def shorts_needing_stats(self, limit: int = 50,
                             max_age_hours: int = 24) -> List[Dict]:
        """Return uploaded shorts whose stats are missing or stale.

        A short needs a stats fetch when it has a YouTube ID but no row in
        short_performance, or its last fetch is older than max_age_hours.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT g.source_video_id, g.segment_index,
                              g.youtube_short_id, g.title, g.start_time, g.end_time
                       FROM generated_shorts g
                       LEFT JOIN short_performance p
                              ON p.youtube_short_id = g.youtube_short_id
                       WHERE g.youtube_short_id IS NOT NULL
                         AND (p.youtube_short_id IS NULL
                              OR p.retrieved_at < datetime('now', ?))
                       ORDER BY g.id DESC
                       LIMIT ?""",
                    (f'-{int(max_age_hours)} hours', limit),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Could not find shorts needing stats: %s", exc)
            return []

    def performance_report(self, limit: int = 10) -> List[Dict]:
        """Top-performing clips joined with their source metadata.

        This is the first real output of the clip brain: which moments from
        which sources performed, so the analysis layer has something to learn
        from.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT p.youtube_short_id, p.views, p.likes, p.comments,
                              p.favorites, p.retrieved_at,
                              g.source_video_id, g.segment_index,
                              g.start_time, g.end_time, g.title, g.score,
                              COALESCE(v.niche, g.upload_channel, '') AS niche
                       FROM short_performance p
                       JOIN generated_shorts g
                         ON g.youtube_short_id = p.youtube_short_id
                       LEFT JOIN processed_videos v
                         ON v.youtube_video_id = g.source_video_id
                       ORDER BY p.views DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Could not build performance report: %s", exc)
            return []

    def performance_summary(self) -> Dict:
        """Aggregate metrics over every tracked clip."""
        out = {'tracked': 0, 'with_views': 0, 'total_views': 0, 'avg_views': 0.0}
        try:
            with self._connect() as conn:
                out['tracked'] = conn.execute(
                    "SELECT COUNT(*) FROM short_performance").fetchone()[0]
                row = conn.execute(
                    """SELECT COUNT(*) AS n,
                              COALESCE(SUM(views), 0) AS total_views
                       FROM short_performance
                       WHERE views > 0"""
                ).fetchone()
                out['with_views'] = row['n'] if row else 0
                out['total_views'] = row['total_views'] if row else 0
                if out['with_views']:
                    out['avg_views'] = round(out['total_views'] / out['with_views'], 1)
        except Exception as exc:
            logger.warning("Could not read performance summary: %s", exc)
        return out

    def source_performance(self) -> Dict[str, Dict]:
        """Per-source-channel clip performance from the feedback loop.

        Aggregates the latest stats for every uploaded short grouped by the
        source channel it was clipped from (``processed_videos.channel_id``
        holds the configured source handle, e.g. ``@AlexHormozi``). This is
        what lets discovery order sources: winners first, underperformers
        soft-demoted. Shorts still climbing are counted at their last snapshot.
        """
        out: Dict[str, Dict] = {}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT p.channel_id AS source_channel,
                              COUNT(*) AS recorded,
                              COALESCE(AVG(sp.views), 0) AS avg_views,
                              COALESCE(MAX(sp.views), 0) AS max_views,
                              SUM(CASE WHEN sp.views >= 200 THEN 1 ELSE 0 END) AS winners
                       FROM short_performance sp
                       JOIN generated_shorts g
                         ON g.youtube_short_id = sp.youtube_short_id
                       JOIN processed_videos p
                         ON p.youtube_video_id = g.source_video_id
                       WHERE p.channel_id != ''
                       GROUP BY p.channel_id"""
                ).fetchall()
                for r in rows:
                    out[r['source_channel']] = {
                        'recorded': r['recorded'],
                        'avg_views': round(r['avg_views'], 1),
                        'max_views': r['max_views'],
                        'winners': r['winners'],
                    }
        except Exception as exc:
            logger.warning("Could not read source performance: %s", exc)
        return out

    # ------------------------------------------------------------------
    # Queue health and backlog management
    # ------------------------------------------------------------------
    def get_queue_health(self, niche: str) -> Dict:
        """Return health metrics for a niche's upload queue.

        Returns dict with:
        - total_queued: total unuploaded clips
        - distinct_sources: number of distinct source_video_ids
        - eligible_clips: clips not blocked by per-source cap
        - top_source_share: ratio of largest source / total
        - channel_remaining: upload capacity remaining for niche's channels
        - capped_sources: list of source_video_ids currently at cap
        - source_counts: dict of source_video_id -> clip count
        """
        try:
            with self._connect() as conn:
                # Get all queued clips for this niche
                rows = conn.execute(
                    """SELECT g.source_video_id, g.segment_index, g.title,
                              g.created_at, g.status,
                              COALESCE(p.niche, '') AS niche,
                              COALESCE(p.title, '') AS source_title
                       FROM generated_shorts g
                       LEFT JOIN processed_videos p
                              ON p.youtube_video_id = g.source_video_id
                       WHERE g.youtube_short_id IS NULL
                         AND g.local_path IS NOT NULL AND g.local_path != ''
                         AND g.status = 'queued'
                         AND (p.niche = ? OR ? = '')
                       ORDER BY g.id ASC""",
                    (niche, niche),
                ).fetchall()

                clips = [dict(r) for r in rows]
                total = len(clips)
                if total == 0:
                    return {
                        'total_queued': 0,
                        'distinct_sources': 0,
                        'eligible_clips': 0,
                        'top_source_share': 0.0,
                        'channel_remaining': 0,
                        'capped_sources': [],
                        'source_counts': {},
                    }

                # Group by source
                source_counts = {}
                for c in clips:
                    src = c['source_video_id']
                    source_counts[src] = source_counts.get(src, 0) + 1

                distinct_sources = len(source_counts)
                max_source = max(source_counts.values()) if source_counts else 0
                top_source_share = max_source / total if total > 0 else 0.0

                # Check per-source caps (would need runtime config, return raw counts)
                return {
                    'total_queued': total,
                    'distinct_sources': distinct_sources,
                    'eligible_clips': 0,  # Will be filled by caller with cap logic
                    'top_source_share': round(top_source_share, 2),
                    'channel_remaining': 0,  # Will be filled by caller
                    'capped_sources': [],
                    'source_counts': source_counts,
                    'oldest_clip_age_days': 0,
                }
        except Exception as exc:
            logger.warning("Could not get queue health for %s: %s", niche, exc)
            return {
                'total_queued': 0,
                'distinct_sources': 0,
                'eligible_clips': 0,
                'top_source_share': 0.0,
                'channel_remaining': 0,
                'capped_sources': [],
                'source_counts': {},
            }

    def expire_stale_backlog(self, niche: str, ttl_days: int = 7) -> int:
        """Mark backlog clips older than TTL as 'expired'.

        Returns number of clips marked expired.
        """
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """UPDATE generated_shorts
                       SET status = 'expired'
                       WHERE youtube_short_id IS NULL
                         AND status = 'queued'
                         AND created_at < datetime('now', ?)
                         AND source_video_id IN (
                           SELECT g.source_video_id
                           FROM generated_shorts g
                           LEFT JOIN processed_videos p
                                  ON p.youtube_video_id = g.source_video_id
                           WHERE g.youtube_short_id IS NULL
                             AND g.status = 'queued'
                             AND (p.niche = ? OR ? = '') )""",
                    (f'-{int(ttl_days)} days', niche, niche),
                )
                return cursor.rowcount
        except Exception as exc:
            logger.warning("Could not expire stale backlog for %s: %s", niche, exc)
            return 0

    def get_queued_clips_for_upload(self, niche: str, limit: int = 100) -> List[Dict]:
        """Get queued clips for upload, grouped by source with fair ordering.

        Returns clips ordered by round-robin across sources:
        - Group by source_video_id
        - Sort within each source by score/created_at
        - Interleave sources (round-robin)
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT g.source_video_id, g.segment_index, g.start_time,
                              g.end_time, g.score, g.local_path, g.title,
                              g.created_at, g.status,
                              COALESCE(p.niche, '') AS niche,
                              COALESCE(p.title, '') AS source_title
                       FROM generated_shorts g
                       LEFT JOIN processed_videos p
                              ON p.youtube_video_id = g.source_video_id
                       WHERE g.youtube_short_id IS NULL
                         AND g.local_path IS NOT NULL AND g.local_path != ''
                         AND g.status = 'queued'
                         AND (p.niche = ? OR ? = '')
                       ORDER BY g.score DESC, g.created_at ASC""",
                    (niche, niche),
                ).fetchall()

                clips = [dict(r) for r in rows]
                if not clips:
                    return []

                # Group by source and sort within each source
                by_source = {}
                for c in clips:
                    src = c['source_video_id']
                    if src not in by_source:
                        by_source[src] = []
                    by_source[src].append(c)

                # Sort sources by count (ascending for fair distribution)
                sources = sorted(by_source.keys(), key=lambda s: len(by_source[s]))

                # Round-robin interleave
                result = []
                pointers = {s: 0 for s in sources}
                remaining_sources = list(sources)

                while remaining_sources and len(result) < limit:
                    next_remaining = []
                    for src in remaining_sources:
                        idx = pointers[src]
                        if idx < len(by_source[src]):
                            result.append(by_source[src][idx])
                            pointers[src] += 1
                            if pointers[src] < len(by_source[src]):
                                next_remaining.append(src)
                    remaining_sources = next_remaining
                    if len(result) >= limit:
                        break

                return result[:limit]
        except Exception as exc:
            logger.warning("Could not get queued clips for %s: %s", niche, exc)
            return []

    def count_queued_by_source(self, niche: str) -> Dict[str, int]:
        """Return count of queued clips per source for a niche."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT g.source_video_id, COUNT(*) as cnt
                       FROM generated_shorts g
                       LEFT JOIN processed_videos p
                              ON p.youtube_video_id = g.source_video_id
                       WHERE g.youtube_short_id IS NULL
                         AND g.status = 'queued'
                         AND (p.niche = ? OR ? = '')
                       GROUP BY g.source_video_id""",
                    (niche, niche),
                ).fetchall()
                return {r['source_video_id']: r['cnt'] for r in rows}
        except Exception as exc:
            logger.warning("Could not count queued by source for %s: %s", niche, exc)
            return {}

    def update_clip_status(self, source_video_id: str, segment_index: int, status: str) -> bool:
        """Update the status of a queued clip."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE generated_shorts
                       SET status = ?
                       WHERE source_video_id = ? AND segment_index = ?""",
                    (status, source_video_id, int(segment_index)),
                )
                return True
        except Exception as exc:
            logger.warning("Could not update clip status: %s", exc)
            return False

    def get_max_queued_per_source(self, niche: str) -> int:
        """Get the maximum number of queued clips for any single source in a niche."""
        counts = self.count_queued_by_source(niche)
        return max(counts.values()) if counts else 0
