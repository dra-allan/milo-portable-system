"""
sessions.py — session awareness, transcript search, and usage insights.
=======================================================================

Replaces ``awareness.cjs`` (Node) with Python, and adds the two things it
never had:

* **Cross-session recall.** Every turn is logged to SQLite with an FTS5
  index, so "what did we decide about the trade copier last month" is a
  query, not an archaeology expedition.
* **Insights.** Token/cost/tool/activity rollups, Hermes-style, so you can
  see what Milo actually spends its time and your money on.

Session awareness still does its original job: multiple OpenCode/Claude Code
windows open at once shouldn't stomp each other, so each registers itself,
heartbeats, and can see what its siblings are doing.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths

STALE_AFTER_S = 30 * 60  # a session with no heartbeat for 30 min is dead

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    task          TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    surface       TEXT NOT NULL DEFAULT 'cli',
    agent         TEXT NOT NULL DEFAULT 'milo',
    model         TEXT,
    cwd           TEXT,
    host          TEXT,
    pid           INTEGER,
    started_at    REAL NOT NULL,
    heartbeat_at  REAL NOT NULL,
    ended_at      REAL,
    turns         INTEGER NOT NULL DEFAULT 0,
    tool_calls    INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0,
    summary       TEXT,
    extra         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sess_status ON sessions(status, heartbeat_at DESC);
CREATE INDEX IF NOT EXISTS idx_sess_start  ON sessions(started_at DESC);

CREATE TABLE IF NOT EXISTS turns (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    tools      TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_session ON turns(session_id, created_at);
"""

_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    content, content='turns', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
"""


@dataclass
class Session:
    id: str
    name: str = ""
    task: str = ""
    status: str = "active"
    surface: str = "cli"
    agent: str = "milo"
    model: str = ""
    cwd: str = ""
    host: str = ""
    pid: int = 0
    started_at: float = 0.0
    heartbeat_at: float = 0.0
    ended_at: Optional[float] = None
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    summary: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def stale(self) -> bool:
        return (self.status == "active"
                and (time.time() - self.heartbeat_at) > STALE_AFTER_S)

    @property
    def duration_s(self) -> float:
        return (self.ended_at or time.time()) - self.started_at

    def age_label(self) -> str:
        secs = time.time() - (self.ended_at or self.heartbeat_at)
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if secs >= size:
                return f"{int(secs // size)}{unit} ago"
        return "just now"


def _row_to_session(row: sqlite3.Row) -> Session:
    d = dict(row)
    d["extra"] = json.loads(d.get("extra") or "{}")
    valid = Session.__dataclass_fields__  # type: ignore[attr-defined]
    return Session(**{k: v for k, v in d.items() if k in valid})


class SessionStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.path = Path(db_path or paths.sessions_db())
        paths.ensure(self.path.parent)
        self._conn = sqlite3.connect(str(self.path), timeout=15.0,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        try:
            self._conn.executescript(_FTS)
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.commit()
            self._conn.close()
        except sqlite3.Error:
            pass

    # ── awareness (the old awareness.cjs) ─────────────────────────────────────

    def start(
        self,
        task: str = "",
        *,
        name: str = "",
        surface: str = "cli",
        agent: str = "milo",
        model: str = "",
        cwd: str = "",
        session_id: str = "",
    ) -> Session:
        now = time.time()
        sid = session_id or os.environ.get("MILO_SESSION_ID") or (
            "ses_" + uuid.uuid4().hex[:16]
        )
        if self.get(sid):
            self.heartbeat(sid, task=task)
            return self.get(sid)  # type: ignore[return-value]
        s = Session(
            id=sid,
            name=name or os.environ.get("MILO_SESSION_NAME", "") or sid[-6:],
            task=task, surface=surface, agent=agent, model=model,
            cwd=cwd or os.getcwd(),
            host=socket.gethostname(), pid=os.getpid(),
            started_at=now, heartbeat_at=now,
        )
        self._conn.execute(
            "INSERT INTO sessions (id,name,task,status,surface,agent,model,cwd,"
            "host,pid,started_at,heartbeat_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.id, s.name, s.task, "active", s.surface, s.agent, s.model, s.cwd,
             s.host, s.pid, s.started_at, s.heartbeat_at),
        )
        self._conn.commit()
        return s

    def heartbeat(self, session_id: str, task: str = "") -> bool:
        sets = ["heartbeat_at=?"]
        args: List[Any] = [time.time()]
        if task:
            sets.append("task=?")
            args.append(task)
        args.append(session_id)
        cur = self._conn.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", args
        )
        self._conn.commit()
        return cur.rowcount > 0

    def finish(self, session_id: str, summary: str = "") -> Optional[Session]:
        self._conn.execute(
            "UPDATE sessions SET status='done', ended_at=?, "
            "summary=COALESCE(NULLIF(?,''), summary) WHERE id=?",
            (time.time(), summary, session_id),
        )
        self._conn.commit()
        return self.get(session_id)

    def get(self, session_id: str) -> Optional[Session]:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None

    def active(self, include_stale: bool = False) -> List[Session]:
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE status='active' ORDER BY heartbeat_at DESC"
        ).fetchall()
        out = [_row_to_session(r) for r in rows]
        return out if include_stale else [s for s in out if not s.stale]

    def recent(self, limit: int = 20) -> List[Session]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_session(r) for r in rows]

    def reap(self) -> int:
        """Mark heartbeat-less sessions dead. Run on every CLI invocation."""
        cutoff = time.time() - STALE_AFTER_S
        cur = self._conn.execute(
            "UPDATE sessions SET status='abandoned', ended_at=heartbeat_at "
            "WHERE status='active' AND heartbeat_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cur.rowcount

    def collisions(self, cwd: Optional[str] = None) -> List[Session]:
        """Other live sessions working in the same directory — check before
        doing anything destructive."""
        cwd = cwd or os.getcwd()
        me = os.environ.get("MILO_SESSION_ID", "")
        return [s for s in self.active()
                if s.cwd == cwd and s.id != me and s.pid != os.getpid()]

    # ── transcript ────────────────────────────────────────────────────────────

    def log_turn(self, session_id: str, role: str, content: str,
                 tools: Optional[Sequence[str]] = None) -> str:
        tid = "turn_" + uuid.uuid4().hex[:14]
        self._conn.execute(
            "INSERT INTO turns (id,session_id,role,content,tools,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (tid, session_id, role, content, json.dumps(list(tools or [])), time.time()),
        )
        self._conn.execute(
            "UPDATE sessions SET turns=turns+1, tool_calls=tool_calls+?, "
            "heartbeat_at=? WHERE id=?",
            (len(tools or []), time.time(), session_id),
        )
        self._conn.commit()

        # Trigger profile extraction periodically (every 5 turns)
        # Check if we should run extraction after this turn
        session = self.get(session_id)
        if session and session.turns % 5 == 0 and session.turns > 0:
            # Run extraction in background to avoid blocking
            try:
                self._trigger_profile_extraction(session_id)
            except Exception:
                # Don't let extraction errors break the session logging
                pass

        return tid

    def _trigger_profile_extraction(self, session_id: str) -> None:
        """Trigger profile extraction for a session in the background."""
        # Get recent transcript for context
        transcript = self.transcript(session_id, limit=10)
        if not transcript:
            return

        # Format transcript excerpt for the extraction prompt
        transcript_excerpt = "\n".join([
            f"{t['role']}: {t['content']}"
            for t in transcript[-5:]  # Last 5 turns for context
        ])

        # Import here to avoid circular dependencies
        from . import profile

        # Run extraction and update profile
        try:
            profile.run_extraction(transcript_excerpt)
        except Exception:
            # Silently fail - don't want to disrupt user experience
            pass

    def record_usage(self, session_id: str, *, input_tokens: int = 0,
                     output_tokens: int = 0, cost_usd: float = 0.0) -> None:
        self._conn.execute(
            "UPDATE sessions SET input_tokens=input_tokens+?, "
            "output_tokens=output_tokens+?, cost_usd=cost_usd+? WHERE id=?",
            (input_tokens, output_tokens, cost_usd, session_id),
        )
        self._conn.commit()

    def transcript(self, session_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Find past turns. This is the cross-session recall Hermes has and
        the old Milo did not."""
        query = (query or "").strip()
        if not query:
            return []
        rows: List[sqlite3.Row] = []
        if self.fts:
            words = [w for w in "".join(
                c if (c.isalnum() or c in "_-") else " " for c in query
            ).split() if len(w) > 1]
            if words:
                expr = " OR ".join(f'"{w}"*' for w in words)
                try:
                    rows = self._conn.execute(
                        "SELECT t.*, s.task AS session_task, s.started_at AS "
                        "session_started FROM turns_fts f "
                        "JOIN turns t ON t.rowid = f.rowid "
                        "JOIN sessions s ON s.id = t.session_id "
                        "WHERE turns_fts MATCH ? ORDER BY rank LIMIT ?",
                        (expr, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
        if not rows:
            like = f"%{query.lower()}%"
            rows = self._conn.execute(
                "SELECT t.*, s.task AS session_task, s.started_at AS session_started "
                "FROM turns t JOIN sessions s ON s.id=t.session_id "
                "WHERE LOWER(t.content) LIKE ? ORDER BY t.created_at DESC LIMIT ?",
                (like, limit),
            ).fetchall()

        out = []
        for r in rows:
            d = dict(r)
            content = d.get("content", "")
            d["excerpt"] = _excerpt(content, query)
            d["when"] = datetime.fromtimestamp(d["created_at"]).strftime("%Y-%m-%d %H:%M")
            out.append(d)
        return out

    # ── insights ──────────────────────────────────────────────────────────────

    def insights(self, days: int = 30) -> Dict[str, Any]:
        since = time.time() - days * 86400
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE started_at >= ?", (since,)
        ).fetchall()
        sessions = [_row_to_session(r) for r in rows]

        by_day: Dict[str, int] = {}
        by_surface: Dict[str, int] = {}
        by_model: Dict[str, int] = {}
        for s in sessions:
            day = datetime.fromtimestamp(s.started_at).strftime("%Y-%m-%d")
            by_day[day] = by_day.get(day, 0) + 1
            by_surface[s.surface] = by_surface.get(s.surface, 0) + 1
            if s.model:
                by_model[s.model] = by_model.get(s.model, 0) + 1

        tool_rows = self._conn.execute(
            "SELECT tools FROM turns WHERE created_at >= ?", (since,)
        ).fetchall()
        tool_counts: Dict[str, int] = {}
        for r in tool_rows:
            for tool in json.loads(r["tools"] or "[]"):
                tool_counts[tool] = tool_counts.get(tool, 0) + 1

        total_secs = sum(s.duration_s for s in sessions)
        return {
            "days": days,
            "sessions": len(sessions),
            "turns": sum(s.turns for s in sessions),
            "tool_calls": sum(s.tool_calls for s in sessions),
            "input_tokens": sum(s.input_tokens for s in sessions),
            "output_tokens": sum(s.output_tokens for s in sessions),
            "cost_usd": round(sum(s.cost_usd for s in sessions), 4),
            "hours": round(total_secs / 3600, 1),
            "busiest_day": max(by_day, key=lambda k: by_day[k]) if by_day else None,
            "by_day": dict(sorted(by_day.items())),
            "by_surface": dict(sorted(by_surface.items(), key=lambda kv: -kv[1])),
            "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1])),
            "top_tools": dict(sorted(tool_counts.items(),
                                     key=lambda kv: -kv[1])[:12]),
        }

    def stats(self) -> Dict[str, Any]:
        q = self._conn.execute
        return {
            "path": str(self.path),
            "size_kb": round(self.path.stat().st_size / 1024, 1)
            if self.path.exists() else 0,
            "fts": self.fts,
            "sessions": q("SELECT COUNT(*) c FROM sessions").fetchone()["c"],
            "active": len(self.active()),
            "turns": q("SELECT COUNT(*) c FROM turns").fetchone()["c"],
        }

    # ── portability ───────────────────────────────────────────────────────────

    def export_jsonl(self, out_path: Path, days: Optional[int] = None) -> int:
        out_path = Path(out_path)
        paths.ensure(out_path.parent)
        where, args = "", []
        if days:
            where = "WHERE started_at >= ?"
            args = [time.time() - days * 86400]
        srows = self._conn.execute(
            f"SELECT * FROM sessions {where} ORDER BY started_at ASC", args
        ).fetchall()
        ids = {r["id"] for r in srows}
        trows = self._conn.execute(
            "SELECT * FROM turns ORDER BY created_at ASC"
        ).fetchall()
        with out_path.open("w", encoding="utf-8", newline="\n") as fh:
            for r in srows:
                fh.write(json.dumps({"_type": "session", **dict(r)},
                                    sort_keys=True, ensure_ascii=False) + "\n")
            for r in trows:
                if r["session_id"] in ids:
                    fh.write(json.dumps({"_type": "turn", **dict(r)},
                                        sort_keys=True, ensure_ascii=False) + "\n")
        return len(srows)

    def import_jsonl(self, in_path: Path) -> Dict[str, int]:
        counts = {"sessions": 0, "turns": 0}
        p = Path(in_path)
        if not p.is_file():
            return counts
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = rec.pop("_type", "")
            table = "sessions" if kind == "session" else "turns" if kind == "turn" else ""
            if not table:
                continue
            cols = list(rec)
            try:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})",
                    [rec[c] for c in cols],
                )
                counts[table] += 1
            except sqlite3.Error:
                pass
        self._conn.commit()
        return counts


def _excerpt(content: str, query: str, width: int = 160) -> str:
    low = content.lower()
    pos = -1
    for word in query.lower().split():
        pos = low.find(word)
        if pos >= 0:
            break
    if pos < 0:
        return content[:width].replace("\n", " ")
    start = max(0, pos - width // 3)
    end = min(len(content), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return prefix + content[start:end].replace("\n", " ") + suffix


_STORE: Optional[SessionStore] = None


def store() -> SessionStore:
    global _STORE
    if _STORE is None:
        _STORE = SessionStore()
    return _STORE