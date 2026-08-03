"""
milo.memory.store — the one brain.

Milo used to have three memories that never spoke to each other:

  * ``~/.engram/engram.db``            hot tier, written by the Engram MCP server
  * ``~/.milo/milo-bot.sqlite``        the Telegram bot's private fallback
  * ``~/.milo/storage/agent-memory-store.json``  the AgentMemory JSON store

An observation saved from Telegram was invisible to OpenCode, and vice
versa. This module replaces all three with a single SQLite database at
``~/.milo/state/brain.sqlite``.

Design
------
* **Pure stdlib.** ``sqlite3`` ships with Python. No server, no daemon.
* **FTS5 full-text search** over titles and content, with a graceful
  fallback to ``LIKE`` on the handful of Python builds compiled without it.
* **Topic keys.** Re-saving with the same ``topic_key`` updates the existing
  observation instead of creating an orphan — how an evolving decision stays
  one record.
* **Everything is exportable.** :meth:`Brain.export_dict` produces a plain
  JSON document that is committed to git on every backup, so the hot tier
  survives losing the machine.

Tiers
-----
=========  =====================================================================
hot        this database — continuous append, no gate
curated    ``MEMORY.md`` / ``USER.md`` — bounded, injected into system prompts
cold       the Obsidian vault — promoted at task boundaries, human-readable
=========  =====================================================================
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from ..paths import MiloPaths, get_paths

__all__ = ["Observation", "Brain", "KINDS", "SCOPES"]


KINDS: Sequence[str] = (
    "decision",
    "bugfix",
    "architecture",
    "discovery",
    "pattern",
    "config",
    "preference",
    "constraint",
    "context",
    "note",
)

SCOPES: Sequence[str] = ("project", "personal", "global")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'note',
    scope        TEXT NOT NULL DEFAULT 'project',
    project      TEXT NOT NULL DEFAULT 'milo',
    tags         TEXT NOT NULL DEFAULT '[]',
    importance   INTEGER NOT NULL DEFAULT 3,
    topic_key    TEXT,
    source       TEXT NOT NULL DEFAULT 'cli',
    session_id   TEXT,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    accessed_at  INTEGER,
    access_count INTEGER NOT NULL DEFAULT 0,
    promoted_to  TEXT,
    archived     INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_topic
    ON observations(project, topic_key) WHERE topic_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_obs_updated  ON observations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_kind     ON observations(kind);
CREATE INDEX IF NOT EXISTS idx_obs_project  ON observations(project);
CREATE INDEX IF NOT EXISTS idx_obs_archived ON observations(archived);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    name           TEXT,
    task           TEXT,
    status         TEXT NOT NULL DEFAULT 'in_progress',
    harness        TEXT,
    host           TEXT,
    cwd            TEXT,
    started_at     INTEGER NOT NULL,
    last_heartbeat INTEGER NOT NULL,
    ended_at       INTEGER,
    summary        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, last_heartbeat DESC);

CREATE TABLE IF NOT EXISTS skill_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    skill      TEXT NOT NULL,
    used_at    INTEGER NOT NULL,
    session_id TEXT,
    outcome    TEXT
);
CREATE INDEX IF NOT EXISTS idx_skill_usage ON skill_usage(skill, used_at DESC);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    title, content, tags,
    content='observations', content_rowid='rowid', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS obs_ai AFTER INSERT ON observations BEGIN
    INSERT INTO observations_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS obs_ad AFTER DELETE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS obs_au AFTER UPDATE ON observations BEGIN
    INSERT INTO observations_fts(observations_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO observations_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;
"""


def _now() -> int:
    return int(time.time())


def _new_id() -> str:
    return "obs_" + uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class Observation:
    """One atomic thing Milo learned."""

    title: str
    content: str = ""
    kind: str = "note"
    scope: str = "project"
    project: str = "milo"
    tags: List[str] = field(default_factory=list)
    importance: int = 3
    topic_key: Optional[str] = None
    source: str = "cli"
    session_id: Optional[str] = None
    id: str = field(default_factory=_new_id)
    created_at: int = field(default_factory=_now)
    updated_at: int = field(default_factory=_now)
    accessed_at: Optional[int] = None
    access_count: int = 0
    promoted_to: Optional[str] = None
    archived: bool = False

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Observation":
        data = dict(row)
        raw_tags = data.pop("tags", "[]")
        try:
            tags = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags)
        except json.JSONDecodeError:
            tags = [t for t in str(raw_tags).split(",") if t]
        data.pop("rowid", None)
        data.pop("rank", None)
        data["archived"] = bool(data.get("archived", 0))
        return cls(tags=tags, **data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def as_markdown(self) -> str:
        """Render for promotion into the vault."""
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.updated_at))
        tags = " ".join(f"#{t}" for t in self.tags) if self.tags else ""
        lines = [
            f"### {self.title}",
            "",
            f"*{self.kind} · importance {self.importance} · {stamp}*"
            + (f" · {tags}" if tags else ""),
            "",
            self.content.strip(),
            "",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------


class Brain:
    """Milo's unified memory. Safe to open from several processes."""

    def __init__(self, db_path: Optional[Path] = None, paths: Optional[MiloPaths] = None):
        self.paths = paths or get_paths()
        self.db_path = Path(db_path) if db_path else self.paths.brain_db
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self.fts_enabled = False
        self._connect()

    # -- lifecycle --------------------------------------------------------

    def _connect(self) -> None:
        conn = sqlite3.connect(
            str(self.db_path), timeout=15.0, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        # WAL lets the bot write while OpenCode reads, without lock storms.
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:  # pragma: no cover - exotic filesystems
            pass
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        try:
            conn.executescript(_FTS_SCHEMA)
            self.fts_enabled = True
        except sqlite3.OperationalError:
            # Python built without FTS5. Search falls back to LIKE.
            self.fts_enabled = False
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._connect()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "Brain":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # -- writing ----------------------------------------------------------

    def save(
        self,
        title: str,
        content: str = "",
        *,
        kind: str = "note",
        scope: str = "project",
        project: str = "milo",
        tags: Optional[Iterable[str]] = None,
        importance: int = 3,
        topic_key: Optional[str] = None,
        source: str = "cli",
        session_id: Optional[str] = None,
    ) -> Observation:
        """Save an observation. Same ``topic_key`` updates rather than duplicates."""
        title = (title or "").strip()
        if not title:
            raise ValueError("observation needs a title")
        kind = kind if kind in KINDS else "note"
        scope = scope if scope in SCOPES else "project"
        importance = max(1, min(5, int(importance or 3)))
        tag_list = sorted({str(t).strip().lstrip("#") for t in (tags or []) if str(t).strip()})
        now = _now()

        with self._tx() as conn:
            existing: Optional[sqlite3.Row] = None
            if topic_key:
                existing = conn.execute(
                    "SELECT * FROM observations WHERE project=? AND topic_key=?",
                    (project, topic_key),
                ).fetchone()
            if existing is None:
                # Exact-duplicate guard: identical title+content in same project.
                existing = conn.execute(
                    "SELECT * FROM observations "
                    "WHERE project=? AND title=? AND content=? AND archived=0",
                    (project, title, content),
                ).fetchone()

            if existing is not None:
                conn.execute(
                    "UPDATE observations SET title=?, content=?, kind=?, scope=?, "
                    "tags=?, importance=?, source=?, session_id=?, updated_at=?, "
                    "archived=0 WHERE id=?",
                    (
                        title, content, kind, scope, json.dumps(tag_list),
                        importance, source, session_id, now, existing["id"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM observations WHERE id=?", (existing["id"],)
                ).fetchone()
                return Observation.from_row(row)

            obs = Observation(
                title=title, content=content, kind=kind, scope=scope,
                project=project, tags=tag_list, importance=importance,
                topic_key=topic_key, source=source, session_id=session_id,
                created_at=now, updated_at=now,
            )
            conn.execute(
                "INSERT INTO observations (id, title, content, kind, scope, project, "
                "tags, importance, topic_key, source, session_id, created_at, "
                "updated_at, access_count, archived) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)",
                (
                    obs.id, obs.title, obs.content, obs.kind, obs.scope, obs.project,
                    json.dumps(obs.tags), obs.importance, obs.topic_key, obs.source,
                    obs.session_id, obs.created_at, obs.updated_at,
                ),
            )
            return obs

    def archive(self, obs_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE observations SET archived=1, updated_at=? WHERE id=?",
                (_now(), obs_id),
            )
        return cur.rowcount > 0

    def mark_promoted(self, obs_id: str, vault_path: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE observations SET promoted_to=?, updated_at=? WHERE id=?",
                (vault_path, _now(), obs_id),
            )

    # -- reading ----------------------------------------------------------

    def get(self, obs_id: str) -> Optional[Observation]:
        row = self.conn.execute(
            "SELECT * FROM observations WHERE id=?", (obs_id,)
        ).fetchone()
        return Observation.from_row(row) if row else None

    def recent(
        self,
        limit: int = 20,
        *,
        project: Optional[str] = None,
        kind: Optional[str] = None,
        min_importance: int = 1,
        include_archived: bool = False,
    ) -> List[Observation]:
        clauses = ["importance >= ?"]
        params: List[Any] = [min_importance]
        if not include_archived:
            clauses.append("archived = 0")
        if project:
            clauses.append("project = ?")
            params.append(project)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        params.append(int(limit))
        rows = self.conn.execute(
            f"SELECT * FROM observations WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [Observation.from_row(r) for r in rows]

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        project: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Observation]:
        """Full-text search. Falls back to ``LIKE`` without FTS5."""
        query = (query or "").strip()
        if not query:
            return self.recent(limit, project=project, include_archived=include_archived)

        rows: List[sqlite3.Row] = []
        if self.fts_enabled:
            try:
                sql = (
                    "SELECT o.* FROM observations_fts f "
                    "JOIN observations o ON o.rowid = f.rowid "
                    "WHERE observations_fts MATCH ?"
                )
                params: List[Any] = [_fts_query(query)]
                if not include_archived:
                    sql += " AND o.archived = 0"
                if project:
                    sql += " AND o.project = ?"
                    params.append(project)
                sql += " ORDER BY bm25(observations_fts), o.importance DESC LIMIT ?"
                params.append(int(limit))
                rows = self.conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                rows = []

        if not rows:
            like = f"%{query}%"
            sql = "SELECT * FROM observations WHERE (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
            params = [like, like, like]
            if not include_archived:
                sql += " AND archived = 0"
            if project:
                sql += " AND project = ?"
                params.append(project)
            sql += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
            params.append(int(limit))
            rows = self.conn.execute(sql, params).fetchall()

        results = [Observation.from_row(r) for r in rows]
        self._touch([o.id for o in results])
        return results

    def _touch(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        now = _now()
        try:
            with self._tx() as conn:
                conn.executemany(
                    "UPDATE observations SET accessed_at=?, access_count=access_count+1 "
                    "WHERE id=?",
                    [(now, i) for i in ids],
                )
        except sqlite3.DatabaseError:  # pragma: no cover - never fail a read
            pass

    def context(self, limit: int = 12, project: Optional[str] = None) -> str:
        """A compact boot digest — the ``mem_context`` equivalent."""
        rows = self.recent(limit, project=project, min_importance=2)
        if not rows:
            return "(no observations yet)"
        out = []
        for obs in rows:
            when = time.strftime("%m-%d", time.localtime(obs.updated_at))
            head = obs.content.strip().splitlines()[0][:160] if obs.content.strip() else ""
            out.append(f"- [{when}] ({obs.kind}) {obs.title}" + (f" — {head}" if head else ""))
        return "\n".join(out)

    def stats(self) -> Dict[str, Any]:
        cur = self.conn
        total = cur.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
        archived = cur.execute(
            "SELECT COUNT(*) c FROM observations WHERE archived=1"
        ).fetchone()["c"]
        promoted = cur.execute(
            "SELECT COUNT(*) c FROM observations WHERE promoted_to IS NOT NULL"
        ).fetchone()["c"]
        by_kind = {
            r["kind"]: r["c"]
            for r in cur.execute(
                "SELECT kind, COUNT(*) c FROM observations WHERE archived=0 "
                "GROUP BY kind ORDER BY c DESC"
            ).fetchall()
        }
        newest = cur.execute(
            "SELECT MAX(updated_at) m FROM observations"
        ).fetchone()["m"]
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return {
            "path": str(self.db_path),
            "size_kb": round(size / 1024, 1),
            "fts5": self.fts_enabled,
            "total": total,
            "active": total - archived,
            "archived": archived,
            "promoted": promoted,
            "by_kind": by_kind,
            "newest": newest,
        }

    # -- sessions / awareness --------------------------------------------

    def session_start(
        self,
        session_id: str,
        task: str = "",
        *,
        name: Optional[str] = None,
        harness: Optional[str] = None,
        host: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = _now()
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO sessions (id, name, task, status, harness, host, cwd, "
                "started_at, last_heartbeat) VALUES (?,?,?,'in_progress',?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET task=excluded.task, "
                "status='in_progress', last_heartbeat=excluded.last_heartbeat",
                (session_id, name or session_id, task, harness, host, cwd, now, now),
            )
        return self.session_get(session_id) or {}

    def session_get(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def session_heartbeat(self, session_id: str, task: Optional[str] = None) -> None:
        with self._tx() as conn:
            if task:
                conn.execute(
                    "UPDATE sessions SET last_heartbeat=?, task=? WHERE id=?",
                    (_now(), task, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET last_heartbeat=? WHERE id=?", (_now(), session_id)
                )

    def session_end(self, session_id: str, summary: str = "") -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE sessions SET status='done', ended_at=?, summary=? WHERE id=?",
                (_now(), summary, session_id),
            )

    def sessions_active(self, stale_after: int = 1800) -> List[Dict[str, Any]]:
        cutoff = _now() - stale_after
        rows = self.conn.execute(
            "SELECT * FROM sessions WHERE status='in_progress' AND last_heartbeat >= ? "
            "ORDER BY last_heartbeat DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def sessions_prune(self, stale_after: int = 1800, keep_done: int = 50) -> int:
        cutoff = _now() - stale_after
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE sessions SET status='stale' "
                "WHERE status='in_progress' AND last_heartbeat < ?",
                (cutoff,),
            )
            removed = cur.rowcount
            conn.execute(
                "DELETE FROM sessions WHERE status IN ('done','stale') AND id NOT IN "
                "(SELECT id FROM sessions WHERE status IN ('done','stale') "
                " ORDER BY COALESCE(ended_at, last_heartbeat) DESC LIMIT ?)",
                (keep_done,),
            )
        return removed

    # -- skill usage ------------------------------------------------------

    def record_skill_use(
        self, skill: str, session_id: Optional[str] = None, outcome: str = ""
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO skill_usage (skill, used_at, session_id, outcome) "
                "VALUES (?,?,?,?)",
                (skill, _now(), session_id, outcome),
            )

    def skill_stats(self) -> Dict[str, Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT skill, COUNT(*) uses, MAX(used_at) last_used "
            "FROM skill_usage GROUP BY skill"
        ).fetchall()
        return {r["skill"]: {"uses": r["uses"], "last_used": r["last_used"]} for r in rows}

    # -- meta -------------------------------------------------------------

    def meta_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, str(value))
            )

    # -- portability ------------------------------------------------------

    def export_dict(self, include_archived: bool = True) -> Dict[str, Any]:
        """Full JSON-serialisable dump. This is what gets committed to git."""
        sql = "SELECT * FROM observations"
        if not include_archived:
            sql += " WHERE archived=0"
        sql += " ORDER BY created_at"
        observations = [
            Observation.from_row(r).to_dict() for r in self.conn.execute(sql).fetchall()
        ]
        sessions = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 200"
            ).fetchall()
        ]
        skills = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM skill_usage ORDER BY used_at DESC LIMIT 2000"
            ).fetchall()
        ]
        return {
            "format": "milo-brain",
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now(),
            "counts": {
                "observations": len(observations),
                "sessions": len(sessions),
                "skill_usage": len(skills),
            },
            "observations": observations,
            "sessions": sessions,
            "skill_usage": skills,
        }

    def export_file(self, path: Path, include_archived: bool = True) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.export_dict(include_archived=include_archived)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def import_dict(self, payload: Mapping[str, Any], *, merge: bool = True) -> Dict[str, int]:
        """Import an export produced by :meth:`export_dict`.

        Merge semantics: an incoming observation wins only if its
        ``updated_at`` is newer. Nothing is ever deleted by an import, so
        restoring an old backup can never destroy newer knowledge.
        """
        added = updated = skipped = 0
        observations = payload.get("observations") or []
        with self._tx() as conn:
            for raw in observations:
                if not isinstance(raw, Mapping) or not raw.get("title"):
                    skipped += 1
                    continue
                obs_id = str(raw.get("id") or _new_id())
                incoming_updated = int(raw.get("updated_at") or raw.get("created_at") or _now())
                existing = conn.execute(
                    "SELECT id, updated_at FROM observations WHERE id=?", (obs_id,)
                ).fetchone()
                tags = raw.get("tags") or []
                if isinstance(tags, str):
                    try:
                        tags = json.loads(tags)
                    except json.JSONDecodeError:
                        tags = [t for t in tags.split(",") if t]
                values = (
                    str(raw.get("title"))[:500],
                    str(raw.get("content") or ""),
                    str(raw.get("kind") or "note"),
                    str(raw.get("scope") or "project"),
                    str(raw.get("project") or "milo"),
                    json.dumps(list(tags)),
                    int(raw.get("importance") or 3),
                    raw.get("topic_key"),
                    str(raw.get("source") or "import"),
                    raw.get("session_id"),
                    int(raw.get("created_at") or incoming_updated),
                    incoming_updated,
                    raw.get("promoted_to"),
                    1 if raw.get("archived") else 0,
                )
                if existing is None:
                    conn.execute(
                        "INSERT INTO observations (title, content, kind, scope, project, "
                        "tags, importance, topic_key, source, session_id, created_at, "
                        "updated_at, promoted_to, archived, id, access_count) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                        (*values, obs_id),
                    )
                    added += 1
                elif not merge or incoming_updated > int(existing["updated_at"] or 0):
                    conn.execute(
                        "UPDATE observations SET title=?, content=?, kind=?, scope=?, "
                        "project=?, tags=?, importance=?, topic_key=?, source=?, "
                        "session_id=?, created_at=?, updated_at=?, promoted_to=?, "
                        "archived=? WHERE id=?",
                        (*values, obs_id),
                    )
                    updated += 1
                else:
                    skipped += 1
        return {"added": added, "updated": updated, "skipped": skipped}

    def import_file(self, path: Path, *, merge: bool = True) -> Dict[str, int]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.import_dict(payload, merge=merge)

    def vacuum(self) -> None:
        try:
            self.conn.execute("VACUUM")
            if self.fts_enabled:
                self.conn.execute(
                    "INSERT INTO observations_fts(observations_fts) VALUES('optimize')"
                )
        except sqlite3.DatabaseError:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# FTS query sanitisation
# ---------------------------------------------------------------------------

_FTS_SPECIAL = re.compile(r'["():*^]')


def _fts_query(raw: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    FTS5 has its own query grammar; unbalanced quotes or a stray ``:`` raise
    ``OperationalError``. We quote each token, which also stops hyphenated
    and dotted terms from being parsed as operators.
    """
    raw = raw[:512]
    if raw.strip().upper().startswith(("NEAR(", "FTS:")):
        return _FTS_SPECIAL.sub(" ", raw)
    tokens = [t for t in re.split(r"\s+", _FTS_SPECIAL.sub(" ", raw)) if t]
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' for t in tokens)
