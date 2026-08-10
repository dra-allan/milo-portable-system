"""SQLite state for ranking builds, clips, topics and channel upload budgets."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional
from .utils import setup_logger
logger = setup_logger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS used_clips (id INTEGER PRIMARY KEY AUTOINCREMENT, source_url TEXT UNIQUE NOT NULL, topic TEXT NOT NULL, phash TEXT, title TEXT, used_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_used_clips_phash ON used_clips(phash);
CREATE TABLE IF NOT EXISTS rejected_clips (id INTEGER PRIMARY KEY AUTOINCREMENT, source_url TEXT UNIQUE NOT NULL, topic TEXT, reason TEXT, rejected_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS builds (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, title TEXT NOT NULL, local_path TEXT, plan_json TEXT, status TEXT NOT NULL DEFAULT 'built', youtube_id TEXT, upload_channel TEXT, created_at REAL NOT NULL, uploaded_at REAL);
CREATE TABLE IF NOT EXISTS topic_runs (topic TEXT PRIMARY KEY, last_run REAL NOT NULL, runs INTEGER NOT NULL DEFAULT 0);
"""
class RankingDatabase:
    def __init__(self, path: Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.executescript(SCHEMA)
            cols={r['name'] for r in c.execute('PRAGMA table_info(builds)')}
            if 'upload_channel' not in cols: c.execute("ALTER TABLE builds ADD COLUMN upload_channel TEXT")
    @contextmanager
    def _connect(self):
        c=sqlite3.connect(str(self.path),timeout=30); c.row_factory=sqlite3.Row
        try: yield c; c.commit()
        except Exception: c.rollback(); raise
        finally: c.close()
    def is_used(self,url):
        with self._connect() as c: return c.execute('SELECT 1 FROM used_clips WHERE source_url=?',(url,)).fetchone() is not None
    def is_rejected(self,url):
        with self._connect() as c: return c.execute('SELECT 1 FROM rejected_clips WHERE source_url=?',(url,)).fetchone() is not None
    def mark_used(self,url,topic,phash=None,title=None):
        with self._connect() as c: c.execute('INSERT OR IGNORE INTO used_clips(source_url,topic,phash,title,used_at) VALUES(?,?,?,?,?)',(url,topic,phash,title,time.time()))
    def mark_rejected(self,url,topic,reason):
        with self._connect() as c: c.execute('INSERT OR REPLACE INTO rejected_clips(source_url,topic,reason,rejected_at) VALUES(?,?,?,?)',(url,topic,reason,time.time()))
    def known_hashes(self):
        with self._connect() as c: return [r['phash'] for r in c.execute('SELECT phash FROM used_clips WHERE phash IS NOT NULL')]
    def record_build(self,topic,title,local_path,plan):
        with self._connect() as c: return int(c.execute('INSERT INTO builds(topic,title,local_path,plan_json,status,created_at) VALUES(?,?,?,?,?,?)',(topic,title,local_path,json.dumps(plan,default=str),'built',time.time())).lastrowid)
    def mark_uploaded(self,build_id,youtube_id,channel=''):
        with self._connect() as c: c.execute("UPDATE builds SET status='uploaded',youtube_id=?,upload_channel=?,uploaded_at=? WHERE id=?",(youtube_id,channel,time.time(),build_id))
    def mark_failed(self,build_id,reason):
        with self._connect() as c: c.execute('UPDATE builds SET status=? WHERE id=?',(f'failed:{reason[:80]}',build_id))
    def pending_builds(self,limit=100):
        with self._connect() as c: return [dict(r) for r in c.execute("SELECT * FROM builds WHERE status='built' ORDER BY created_at LIMIT ?",(limit,))]
    def uploads_since(self,seconds):
        with self._connect() as c: return int(c.execute("SELECT COUNT(*) n FROM builds WHERE status='uploaded' AND uploaded_at>=?",(time.time()-seconds,)).fetchone()['n'])
    def uploaded_count_for_channel_since(self,channel,seconds=86400):
        if not channel:return 0
        with self._connect() as c: return int(c.execute("SELECT COUNT(*) n FROM builds WHERE status='uploaded' AND upload_channel=? AND uploaded_at>=?",(channel,time.time()-seconds)).fetchone()['n'])
    def touch_topic(self,topic):
        with self._connect() as c: c.execute('INSERT INTO topic_runs(topic,last_run,runs) VALUES(?,?,1) ON CONFLICT(topic) DO UPDATE SET last_run=excluded.last_run,runs=runs+1',(topic,time.time()))
    def next_topic(self,candidates):
        if not candidates:return None
        with self._connect() as c: seen={r['topic']:r['last_run'] for r in c.execute('SELECT topic,last_run FROM topic_runs')}
        return next((x for x in candidates if x not in seen),min(candidates,key=lambda x:seen.get(x,0)))
