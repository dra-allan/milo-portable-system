"""
memory.py — Milo's single brain.
================================

The old system had three memory stores that never spoke to each other:

* ``~/.engram/engram.db``            (hot tier, MCP)
* ``~/.milo/milo-bot.sqlite``        (Telegram bot fallback)
* ``agent-memory-store.json``        (AgentMemory / Supabase mirror)

Save something in one, and the other two never learned it. This module
replaces all three with a single SQLite file at ``$MILO_HOME/state/memory.db``
that every surface writes to: CLI, Telegram, OpenCode MCP, Claude Code hooks.

Design notes (borrowed from Hermes' curated-memory model)
---------------------------------------------------------
* **FTS5 full-text search** with a graceful LIKE fallback when the Python
  build lacks FTS5 (some Termux builds do).
* **Strength & decay** — every recall bumps ``access_count`` and refreshes
  ``last_accessed``. ``score()`` blends importance, recency and access count
  so hot memories float and stale trivia sinks.
* **Supersession, not deletion** — updating a memory writes a new row and
  marks the old ``is_latest = 0``. Nothing is ever silently lost.
* **Entities and relations** — a lightweight knowledge graph so Milo can
  answer "what do I know about X" rather than only doing keyword search.
* **Git-friendly export** — ``export_jsonl()`` produces a stable, sorted,
  line-oriented dump that diffs cleanly, so the whole brain is versioned in
  the repo and survives losing the machine.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths

SCHEMA_VERSION = 1

# ── Categories ────────────────────────────────────────────────────────────────

CATEGORIES = (
    "fact",        # durable truth about the world or the user's setup
    "preference",  # how Allan likes things done
    "decision",    # a choice made, with rationale
    "context",     # situational, expires naturally
    "task",        # open loop
    "person",      # someone Milo should know
    "project",     # project-level state
    "skill",       # pointer to a learned procedure
    "insight",     # something Milo worked out itself
)

DEFAULT_CATEGORY = "fact"

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    title         TEXT,
    category      TEXT NOT NULL DEFAULT 'fact',
    project       TEXT NOT NULL DEFAULT 'milo',
    tags          TEXT NOT NULL DEFAULT '[]',
    importance    INTEGER NOT NULL DEFAULT 3,
    source        TEXT NOT NULL DEFAULT 'cli',
    origin        TEXT,
    content_hash  TEXT NOT NULL,
    supersedes    TEXT,
    is_latest     INTEGER NOT NULL DEFAULT 1,
    pinned        INTEGER NOT NULL DEFAULT 0,
    archived      INTEGER NOT NULL DEFAULT 0,
    access_count  INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    last_accessed REAL,
    expires_at    REAL,
    extra         TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_mem_latest   ON memories(is_latest, archived);
CREATE INDEX IF NOT EXISTS idx_mem_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_mem_project  ON memories(project);
CREATE INDEX IF NOT EXISTS idx_mem_created  ON memories(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_hash_latest
    ON memories(content_hash) WHERE is_latest = 1;

CREATE TABLE IF NOT EXISTS entities (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'thing',
    summary    TEXT,
    aliases    TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_name ON entities(name COLLATE NOCASE, kind);

CREATE TABLE IF NOT EXISTS relations (
    id         TEXT PRIMARY KEY,
    subject    TEXT NOT NULL,
    predicate  TEXT NOT NULL,
    object     TEXT NOT NULL,
    memory_id  TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON relations(subject COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_rel_object  ON relations(object COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS nudges (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    fired_at   REAL,
    resolved   INTEGER NOT NULL DEFAULT 0
);
"""

_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, content, tags,
    content='memories', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content, tags)
    VALUES (new.rowid, COALESCE(new.title,''), new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, COALESCE(old.title,''), old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, COALESCE(old.title,''), old.content, old.tags);
    INSERT INTO memories_fts(rowid, title, content, tags)
    VALUES (new.rowid, COALESCE(new.title,''), new.content, new.tags);
END;
"""


# ── Record ────────────────────────────────────────────────────────────────────


@dataclass
class Memory:
    id: str
    content: str
    title: str = ""
    category: str = DEFAULT_CATEGORY
    project: str = "milo"
    tags: List[str] = field(default_factory=list)
    importance: int = 3
    source: str = "cli"
    origin: str = ""
    content_hash: str = ""
    supersedes: Optional[str] = None
    is_latest: int = 1
    pinned: int = 0
    archived: int = 0
    access_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_accessed: Optional[float] = None
    expires_at: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    # -- scoring ---------------------------------------------------------------

    def age_days(self, now: Optional[float] = None) -> float:
        now = now or time.time()
        return max(0.0, (now - (self.created_at or now)) / 86400.0)

    def score(self, now: Optional[float] = None) -> float:
        """Blend importance, recency and usage into one ranking number.

        Pinned memories always sort first. Recency uses a 30-day half-life so
        last week's decisions outrank last year's, without ever hitting zero.
        """
        if self.pinned:
            return 1e6
        now = now or time.time()
        recency = math.exp(-self.age_days(now) / 30.0)
        usage = math.log1p(self.access_count) / 3.0
        return (self.importance * 2.0) + (recency * 4.0) + usage

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary_line(self, width: int = 88) -> str:
        head = self.title or self.content.replace("\n", " ")
        if len(head) > width:
            head = head[: width - 1] + "…"
        return head


def _hash(content: str, project: str) -> str:
    norm = " ".join((content or "").split()).lower()
    return hashlib.sha256(f"{project}\x00{norm}".encode("utf-8")).hexdigest()[:32]


def _row_to_memory(row: sqlite3.Row) -> Memory:
    d = dict(row)
    d.pop("rowid", None)
    d.pop("rank", None)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["extra"] = json.loads(d.get("extra") or "{}")
    valid = {f for f in Memory.__dataclass_fields__}  # type: ignore[attr-defined]
    return Memory(**{k: v for k, v in d.items() if k in valid})


# ── Store ─────────────────────────────────────────────────────────────────────


class MemoryStore:
    """Thread-safe-enough SQLite wrapper. One instance per process is fine."""

    def __init__(self, db_path: Optional[Path] = None):
        self.path = Path(db_path or paths.memory_db())
        paths.ensure(self.path.parent)
        self._conn = sqlite3.connect(str(self.path), timeout=15.0,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.fts = False
        self._migrate()

    # -- lifecycle -------------------------------------------------------------

    def _migrate(self) -> None:
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.executescript(_FTS)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False  # FTS5 unavailable — LIKE fallback covers us
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.commit()
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- write -----------------------------------------------------------------

    def save(
        self,
        content: str,
        *,
        title: str = "",
        category: str = DEFAULT_CATEGORY,
        project: str = "milo",
        tags: Optional[Sequence[str]] = None,
        importance: int = 3,
        source: str = "cli",
        origin: str = "",
        pinned: bool = False,
        expires_in_days: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
        supersede: bool = True,
    ) -> Tuple[Memory, bool]:
        """Insert a memory. Returns ``(memory, created)``.

        Exact duplicates (same normalised content + project) are *not*
        re-inserted; the existing row is touched and returned with
        ``created=False``. That makes every writer idempotent, which matters
        because the Telegram bot, cron jobs and the MCP tool all race.
        """
        content = (content or "").strip()
        if not content:
            raise ValueError("refusing to save an empty memory")
        category = category if category in CATEGORIES else DEFAULT_CATEGORY
        importance = max(1, min(5, int(importance)))
        now = time.time()
        chash = _hash(content, project)

        existing = self._conn.execute(
            "SELECT * FROM memories WHERE content_hash=? AND is_latest=1", (chash,)
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE memories SET updated_at=?, importance=MAX(importance, ?), "
                "pinned=MAX(pinned, ?) WHERE id=?",
                (now, importance, 1 if pinned else 0, existing["id"]),
            )
            self._conn.commit()
            return self.get(existing["id"]) or _row_to_memory(existing), False

        supersedes = None
        if supersede and title:
            prev = self._conn.execute(
                "SELECT id FROM memories WHERE title=? AND project=? AND is_latest=1",
                (title, project),
            ).fetchone()
            if prev:
                supersedes = prev["id"]
                self._conn.execute(
                    "UPDATE memories SET is_latest=0, updated_at=? WHERE id=?",
                    (now, supersedes),
                )

        mem = Memory(
            id="mem_" + uuid.uuid4().hex[:16],
            content=content,
            title=title.strip(),
            category=category,
            project=project or "milo",
            tags=sorted({t.strip().lower() for t in (tags or []) if t.strip()}),
            importance=importance,
            source=source,
            origin=origin,
            content_hash=chash,
            supersedes=supersedes,
            pinned=1 if pinned else 0,
            created_at=now,
            updated_at=now,
            expires_at=(now + expires_in_days * 86400) if expires_in_days else None,
            extra=extra or {},
        )
        self._conn.execute(
            "INSERT INTO memories (id, content, title, category, project, tags, "
            "importance, source, origin, content_hash, supersedes, is_latest, "
            "pinned, archived, access_count, created_at, updated_at, "
            "last_accessed, expires_at, extra) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mem.id, mem.content, mem.title, mem.category, mem.project,
                json.dumps(mem.tags), mem.importance, mem.source, mem.origin,
                mem.content_hash, mem.supersedes, 1, mem.pinned, 0, 0,
                mem.created_at, mem.updated_at, None, mem.expires_at,
                json.dumps(mem.extra),
            ),
        )
        self._conn.commit()
        return mem, True

    def update(self, memory_id: str, **changes: Any) -> Optional[Memory]:
        allowed = {
            "content", "title", "category", "project", "importance",
            "pinned", "archived", "expires_at", "origin", "source",
        }
        sets, values = [], []
        for key, val in changes.items():
            if key == "tags":
                sets.append("tags=?")
                values.append(json.dumps(sorted({str(t).lower() for t in val})))
            elif key == "extra":
                sets.append("extra=?")
                values.append(json.dumps(val))
            elif key in allowed:
                sets.append(f"{key}=?")
                values.append(int(val) if key in ("pinned", "archived", "importance") else val)
        if not sets:
            return self.get(memory_id)
        sets.append("updated_at=?")
        values.extend([time.time(), memory_id])
        self._conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id=?", values)
        self._conn.commit()
        return self.get(memory_id)

    def forget(self, memory_id: str, hard: bool = False) -> bool:
        """Archive by default. Hard delete only when explicitly demanded."""
        if hard:
            cur = self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        else:
            cur = self._conn.execute(
                "UPDATE memories SET archived=1, is_latest=0, updated_at=? WHERE id=?",
                (time.time(), memory_id),
            )
        self._conn.commit()
        return cur.rowcount > 0

    def pin(self, memory_id: str, value: bool = True) -> bool:
        cur = self._conn.execute(
            "UPDATE memories SET pinned=?, updated_at=? WHERE id=?",
            (1 if value else 0, time.time(), memory_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # -- read ------------------------------------------------------------------

    def get(self, memory_id: str) -> Optional[Memory]:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id=?", (memory_id,)
        ).fetchone()
        return _row_to_memory(row) if row else None

    def _touch(self, ids: Iterable[str]) -> None:
        ids = list(ids)
        if not ids:
            return
        now = time.time()
        self._conn.executemany(
            "UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id=?",
            [(now, i) for i in ids],
        )
        self._conn.commit()

    def recent(
        self,
        limit: int = 20,
        *,
        category: Optional[str] = None,
        project: Optional[str] = None,
        tag: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Memory]:
        where = ["is_latest=1"]
        args: List[Any] = []
        if not include_archived:
            where.append("archived=0")
        if category:
            where.append("category=?")
            args.append(category)
        if project:
            where.append("project=?")
            args.append(project)
        if tag:
            where.append("tags LIKE ?")
            args.append(f'%"{tag.lower()}"%')
        args.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE {' AND '.join(where)} "
            "ORDER BY pinned DESC, created_at DESC LIMIT ?",
            args,
        ).fetchall()
        return [_row_to_memory(r) for r in rows]

    @staticmethod
    def _fts_query(query: str) -> str:
        """Turn a human query into a safe FTS5 expression (OR of prefixes)."""
        words = [w for w in "".join(
            ch if (ch.isalnum() or ch in "_-") else " " for ch in query
        ).split() if len(w) > 1]
        if not words:
            return ""
        return " OR ".join(f'"{w}"*' for w in words)

    def search(
        self,
        query: str,
        limit: int = 12,
        *,
        category: Optional[str] = None,
        project: Optional[str] = None,
        include_archived: bool = False,
        touch: bool = True,
    ) -> List[Memory]:
        """Full-text search, ranked by relevance blended with :meth:`Memory.score`."""
        query = (query or "").strip()
        if not query:
            return self.recent(limit, category=category, project=project)

        results: List[Memory] = []
        if self.fts:
            expr = self._fts_query(query)
            if expr:
                try:
                    rows = self._conn.execute(
                        "SELECT m.* FROM memories_fts f "
                        "JOIN memories m ON m.rowid = f.rowid "
                        "WHERE memories_fts MATCH ? AND m.is_latest=1 "
                        + ("" if include_archived else "AND m.archived=0 ")
                        + "ORDER BY rank LIMIT ?",
                        (expr, limit * 4),
                    ).fetchall()
                    results = [_row_to_memory(r) for r in rows]
                except sqlite3.OperationalError:
                    results = []

        if not results:  # LIKE fallback
            like = f"%{query.lower()}%"
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE is_latest=1 "
                + ("" if include_archived else "AND archived=0 ")
                + "AND (LOWER(content) LIKE ? OR LOWER(COALESCE(title,'')) LIKE ? "
                  "OR LOWER(tags) LIKE ?) LIMIT ?",
                (like, like, like, limit * 4),
            ).fetchall()
            results = [_row_to_memory(r) for r in rows]

        if category:
            results = [m for m in results if m.category == category]
        if project:
            results = [m for m in results if m.project == project]

        results.sort(key=lambda m: m.score(), reverse=True)
        results = results[:limit]
        if touch:
            self._touch(m.id for m in results)
        return results

    def context(self, query: str = "", budget: int = 12) -> List[Memory]:
        """What Milo should be told at the start of a turn.

        Always includes pinned memories, then fills the remaining budget with
        query-relevant hits, then with recent high-importance items.
        """
        out: List[Memory] = []
        seen: set[str] = set()

        def _add(items: Iterable[Memory]) -> None:
            for m in items:
                if m.id not in seen and len(out) < budget:
                    seen.add(m.id)
                    out.append(m)

        _add(self.recent(budget, include_archived=False) and [
            m for m in self.recent(budget * 3) if m.pinned
        ])
        if query:
            _add(self.search(query, budget, touch=True))
        _add(sorted(self.recent(budget * 2), key=lambda m: m.score(), reverse=True))
        return out[:budget]

    # -- entities & relations --------------------------------------------------

    def upsert_entity(self, name: str, kind: str = "thing", summary: str = "",
                      aliases: Optional[Sequence[str]] = None) -> str:
        now = time.time()
        row = self._conn.execute(
            "SELECT id FROM entities WHERE name=? COLLATE NOCASE AND kind=?",
            (name, kind),
        ).fetchone()
        if row:
            self._conn.execute(
                "UPDATE entities SET summary=COALESCE(NULLIF(?,''), summary), "
                "aliases=?, updated_at=? WHERE id=?",
                (summary, json.dumps(sorted(set(aliases or []))), now, row["id"]),
            )
            self._conn.commit()
            return row["id"]
        eid = "ent_" + uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO entities (id,name,kind,summary,aliases,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (eid, name, kind, summary, json.dumps(sorted(set(aliases or []))), now, now),
        )
        self._conn.commit()
        return eid

    def relate(self, subject: str, predicate: str, obj: str, *,
               memory_id: Optional[str] = None, confidence: float = 1.0) -> str:
        rid = "rel_" + uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO relations (id,subject,predicate,object,memory_id,"
            "confidence,created_at) VALUES (?,?,?,?,?,?,?)",
            (rid, subject, predicate, obj, memory_id, confidence, time.time()),
        )
        self._conn.commit()
        return rid

    def about(self, name: str, limit: int = 20) -> Dict[str, Any]:
        """Everything Milo knows about one thing: entity + relations + memories."""
        ent = self._conn.execute(
            "SELECT * FROM entities WHERE name=? COLLATE NOCASE", (name,)
        ).fetchone()
        rels = self._conn.execute(
            "SELECT * FROM relations WHERE subject=? COLLATE NOCASE "
            "OR object=? COLLATE NOCASE ORDER BY created_at DESC LIMIT ?",
            (name, name, limit),
        ).fetchall()
        return {
            "entity": dict(ent) if ent else None,
            "relations": [dict(r) for r in rels],
            "memories": [m.to_dict() for m in self.search(name, limit)],
        }

    # -- maintenance -----------------------------------------------------------

    def expire(self) -> int:
        """Archive anything past its ``expires_at``. Pinned rows are immune."""
        cur = self._conn.execute(
            "UPDATE memories SET archived=1, is_latest=0 WHERE expires_at IS NOT NULL "
            "AND expires_at < ? AND pinned=0 AND archived=0",
            (time.time(),),
        )
        self._conn.commit()
        return cur.rowcount

    def dedupe(self) -> int:
        """Collapse duplicate content hashes, keeping the most-used copy."""
        rows = self._conn.execute(
            "SELECT content_hash, COUNT(*) c FROM memories WHERE is_latest=1 "
            "GROUP BY content_hash HAVING c > 1"
        ).fetchall()
        removed = 0
        for row in rows:
            dupes = self._conn.execute(
                "SELECT id FROM memories WHERE content_hash=? AND is_latest=1 "
                "ORDER BY pinned DESC, access_count DESC, importance DESC, created_at ASC",
                (row["content_hash"],),
            ).fetchall()
            for extra_row in dupes[1:]:
                self._conn.execute(
                    "UPDATE memories SET is_latest=0, archived=1 WHERE id=?",
                    (extra_row["id"],),
                )
                removed += 1
        self._conn.commit()
        return removed

    def stats(self) -> Dict[str, Any]:
        q = self._conn.execute
        total = q("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        live = q("SELECT COUNT(*) c FROM memories WHERE is_latest=1 AND archived=0"
                 ).fetchone()["c"]
        pinned = q("SELECT COUNT(*) c FROM memories WHERE pinned=1").fetchone()["c"]
        by_cat = {
            r["category"]: r["c"]
            for r in q("SELECT category, COUNT(*) c FROM memories "
                       "WHERE is_latest=1 AND archived=0 GROUP BY category").fetchall()
        }
        by_proj = {
            r["project"]: r["c"]
            for r in q("SELECT project, COUNT(*) c FROM memories "
                       "WHERE is_latest=1 AND archived=0 GROUP BY project").fetchall()
        }
        newest = q("SELECT MAX(created_at) t FROM memories").fetchone()["t"]
        return {
            "path": str(self.path),
            "size_kb": round(self.path.stat().st_size / 1024, 1) if self.path.exists() else 0,
            "fts": self.fts,
            "total_rows": total,
            "live": live,
            "pinned": pinned,
            "archived": total - live,
            "entities": q("SELECT COUNT(*) c FROM entities").fetchone()["c"],
            "relations": q("SELECT COUNT(*) c FROM relations").fetchone()["c"],
            "by_category": by_cat,
            "by_project": by_proj,
            "newest": newest,
        }

    # -- portability -----------------------------------------------------------

    def export_jsonl(self, out_path: Path, include_archived: bool = True) -> int:
        """Stable, sorted JSONL dump. This is what gets committed to git."""
        out_path = Path(out_path)
        paths.ensure(out_path.parent)
        where = "" if include_archived else "WHERE archived=0"
        rows = self._conn.execute(
            f"SELECT * FROM memories {where} ORDER BY created_at ASC, id ASC"
        ).fetchall()
        ents = self._conn.execute("SELECT * FROM entities ORDER BY name ASC").fetchall()
        rels = self._conn.execute(
            "SELECT * FROM relations ORDER BY created_at ASC, id ASC"
        ).fetchall()

        with out_path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({
                "_type": "header",
                "schema_version": SCHEMA_VERSION,
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "counts": {"memories": len(rows), "entities": len(ents),
                           "relations": len(rels)},
            }, sort_keys=True) + "\n")
            for r in rows:
                d = dict(r)
                d["_type"] = "memory"
                fh.write(json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n")
            for r in ents:
                d = dict(r)
                d["_type"] = "entity"
                fh.write(json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n")
            for r in rels:
                d = dict(r)
                d["_type"] = "relation"
                fh.write(json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n")
        return len(rows)

    def import_jsonl(self, in_path: Path, merge: bool = True) -> Dict[str, int]:
        """Restore from a JSONL dump. Merge keeps whatever is already here."""
        in_path = Path(in_path)
        counts = {"memories": 0, "entities": 0, "relations": 0, "skipped": 0}
        if not in_path.is_file():
            return counts
        if not merge:
            self._conn.executescript(
                "DELETE FROM memories; DELETE FROM entities; DELETE FROM relations;"
            )
        for line in in_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = rec.pop("_type", "memory")
            try:
                if kind == "memory":
                    cols = [c for c in rec if c != "rowid"]
                    self._conn.execute(
                        f"INSERT OR IGNORE INTO memories ({','.join(cols)}) "
                        f"VALUES ({','.join('?' * len(cols))})",
                        [rec[c] for c in cols],
                    )
                    counts["memories"] += 1
                elif kind == "entity":
                    cols = list(rec)
                    self._conn.execute(
                        f"INSERT OR IGNORE INTO entities ({','.join(cols)}) "
                        f"VALUES ({','.join('?' * len(cols))})",
                        [rec[c] for c in cols],
                    )
                    counts["entities"] += 1
                elif kind == "relation":
                    cols = list(rec)
                    self._conn.execute(
                        f"INSERT OR IGNORE INTO relations ({','.join(cols)}) "
                        f"VALUES ({','.join('?' * len(cols))})",
                        [rec[c] for c in cols],
                    )
                    counts["relations"] += 1
            except sqlite3.Error:
                counts["skipped"] += 1
        self._conn.commit()
        return counts

    def export_markdown(self, out_path: Path) -> int:
        """Human-readable mirror for the Obsidian vault."""
        out_path = Path(out_path)
        paths.ensure(out_path.parent)
        mems = self.recent(10_000)
        by_cat: Dict[str, List[Memory]] = {}
        for m in mems:
            by_cat.setdefault(m.category, []).append(m)
        lines = [
            "---",
            "tags: [milo, memory, generated]",
            f"generated: {time.strftime('%Y-%m-%d %H:%M')}",
            "---",
            "",
            "# Milo — Memory Snapshot",
            "",
            "> Generated by `milo memory export --markdown`. Do not edit by hand;",
            "> edits are overwritten. Use `milo remember` instead.",
            "",
        ]
        for cat in sorted(by_cat):
            lines.append(f"## {cat.title()}")
            lines.append("")
            for m in sorted(by_cat[cat], key=lambda x: x.score(), reverse=True):
                star = "📌 " if m.pinned else ""
                tagstr = " ".join(f"#{t}" for t in m.tags)
                lines.append(f"- {star}**{m.title or m.summary_line(60)}** — "
                             f"{m.content} {tagstr}".rstrip())
            lines.append("")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return len(mems)


# ── Module-level convenience (used by MCP shim, bot, hooks) ───────────────────

_STORE: Optional[MemoryStore] = None


def store() -> MemoryStore:
    """Process-wide singleton."""
    global _STORE
    if _STORE is None:
        _STORE = MemoryStore()
    return _STORE


def remember(content: str, **kwargs: Any) -> Memory:
    mem, _ = store().save(content, **kwargs)
    return mem


def recall(query: str, limit: int = 10) -> List[Memory]:
    return store().search(query, limit)
