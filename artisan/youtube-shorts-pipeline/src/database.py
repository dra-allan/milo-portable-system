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
    title TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_processed_videos_id
    ON processed_videos(youtube_video_id);
CREATE INDEX IF NOT EXISTS idx_generated_shorts_source
    ON generated_shorts(source_video_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_generated_shorts_segment
    ON generated_shorts(source_video_id, segment_index);
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
        ):
            if column not in existing:
                try:
                    conn.execute(f"ALTER TABLE generated_shorts ADD COLUMN {column} {ddl}")
                    logger.info("Migrated generated_shorts: added %s", column)
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
                            youtube_short_id: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """UPDATE generated_shorts
                       SET youtube_short_id = ?, uploaded_at = CURRENT_TIMESTAMP
                       WHERE source_video_id = ? AND segment_index = ?""",
                    (youtube_short_id, source_video_id, int(segment_index)),
                )
        except Exception as exc:
            logger.warning("Could not mark short uploaded: %s", exc)

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
