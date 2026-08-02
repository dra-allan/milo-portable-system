"""
migrate.py — pull every legacy memory store into the one brain.
===============================================================

Before this rewrite Milo's memory was scattered across three stores that did
not talk to each other:

======================  ============================================  ==========
Store                   Location                                      Tier
======================  ============================================  ==========
Engram                  ``~/.engram/engram.db`` (SQLite, MCP server)  hot
Telegram bot fallback   ``milo-bot.sqlite`` next to ``bot.py``        hot-ish
AgentMemory             ``agent-memory-store.json`` / Supabase        warm
======================  ============================================  ==========

Observations saved through one never appeared in the others. Worse, Engram was
only ever "backed up" to ``~/.milo/backups/engram/`` — a *local* folder that
was never pushed anywhere, so changing machines lost the hot tier entirely.

This module is the bridge off that cliff. Every importer is:

* **schema-tolerant** — Engram's columns have moved between releases, so we
  introspect the tables and map whatever looks right rather than hardcoding
  one layout;
* **idempotent** — re-running merges instead of duplicating, because
  ``MemoryStore.save()`` deduplicates on normalised content;
* **loud on failure** — if we cannot make sense of a database we say so
  instead of quietly importing zero rows and declaring success.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths
from .memory import MemoryStore, store as default_store

__all__ = [
    "MigrationReport",
    "discover_legacy",
    "import_engram",
    "import_bot_sqlite",
    "import_agentmemory_json",
    "import_engram_json_export",
    "snapshot_sqlite",
    "migrate_all",
]


# ── Report ────────────────────────────────────────────────────────────────────


class MigrationReport:
    """Accumulates what each importer did so the CLI can print one summary."""

    def __init__(self) -> None:
        self.sources: List[Dict[str, Any]] = []

    def add(self, name: str, path: Optional[Path], **counts: Any) -> None:
        self.sources.append({"source": name, "path": str(path) if path else None, **counts})

    @property
    def imported(self) -> int:
        return sum(int(s.get("imported", 0)) for s in self.sources)

    @property
    def skipped(self) -> int:
        return sum(int(s.get("skipped", 0)) for s in self.sources)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "sources": self.sources,
        }

    def render(self) -> str:
        if not self.sources:
            return "No legacy memory stores found — nothing to migrate."
        lines = []
        for s in self.sources:
            bits = [f"{k}={v}" for k, v in s.items() if k not in ("source", "path")]
            lines.append(f"  {s['source']:<22} {' '.join(bits)}")
            if s.get("path"):
                lines.append(f"    {s['path']}")
        lines.append(f"\n  total imported: {self.imported}  (skipped {self.skipped})")
        return "\n".join(lines)


# ── Column mapping ────────────────────────────────────────────────────────────

#: Candidate column names, in priority order, for each field we care about.
_FIELDS: Dict[str, Sequence[str]] = {
    "id": ("id", "uuid", "observation_id", "memory_id"),
    "title": ("title", "name", "summary", "heading", "subject", "topic"),
    "content": ("content", "body", "text", "observation", "detail", "value", "data"),
    "category": ("type", "kind", "category", "obs_type", "entity_type"),
    "project": ("project", "project_name", "workspace", "repo", "scope"),
    "tags": ("tags", "labels", "keywords"),
    "importance": ("importance", "weight", "priority", "strength", "score"),
    "created_at": ("created_at", "created", "timestamp", "ts", "inserted_at", "date"),
    "updated_at": ("updated_at", "updated", "modified_at", "last_updated"),
}

#: Tables we will look inside, most-likely first.
_TABLE_HINTS = (
    "memories", "memory", "observations", "observation", "entities",
    "facts", "notes", "records", "items", "messages", "history",
)


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    except sqlite3.Error:
        return []


def _tables(conn: sqlite3.Connection) -> List[str]:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%'"
        ).fetchall()
    except sqlite3.Error:
        return []
    names = [r[0] for r in rows]
    # Rank the likely ones first, keep the rest as fallbacks.
    ranked = [t for h in _TABLE_HINTS for t in names if t.lower() == h]
    ranked += [t for t in names if t not in ranked]
    return ranked


def _map_columns(cols: Sequence[str]) -> Dict[str, str]:
    lower = {c.lower(): c for c in cols}
    mapping: Dict[str, str] = {}
    for field, candidates in _FIELDS.items():
        for cand in candidates:
            if cand in lower:
                mapping[field] = lower[cand]
                break
    return mapping


def _as_epoch(value: Any) -> float:
    """Best-effort timestamp coercion: epoch s, epoch ms, or ISO-8601."""
    if value in (None, ""):
        return time.time()
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    text = str(value).strip()
    if text.isdigit():
        v = float(text)
        return v / 1000.0 if v > 1e12 else v
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.replace("Z", "").split("+")[0], fmt).timestamp()
        except ValueError:
            continue
    return time.time()


def _as_tags(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(t).strip() for t in value if str(t).strip()]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except json.JSONDecodeError:
            pass
    return [t.strip() for t in text.replace(";", ",").split(",") if t.strip()]


def _as_importance(value: Any, default: int = 3) -> int:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if 0 < n <= 1:          # normalised 0..1 weight
        n = 1 + n * 4
    return max(1, min(5, int(round(n))))


# ── Discovery ─────────────────────────────────────────────────────────────────


def discover_legacy() -> Dict[str, List[Path]]:
    """Find every legacy store we know how to read, on any platform."""
    home = Path.home()
    found: Dict[str, List[Path]] = {
        "engram": [],
        "bot_sqlite": [],
        "agentmemory": [],
        "engram_export": [],
    }

    for cand in (
        paths.engram_dir() / "engram.db",
        home / ".engram" / "engram.db",
        home / ".engram" / "memory.db",
        paths.milo_home() / "engram.db",
    ):
        if cand.is_file() and cand not in found["engram"]:
            found["engram"].append(cand)

    search_roots = [
        paths.milo_home(),
        paths.repos_dir(),
        home / "Desktop",
        home,
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            for pat, key in (
                ("**/milo-bot.sqlite", "bot_sqlite"),
                ("**/milo-bot.db", "bot_sqlite"),
                ("**/agent-memory-store.json", "agentmemory"),
                ("**/agentmemory*.json", "agentmemory"),
            ):
                for hit in list(root.glob(pat))[:20]:
                    if hit.is_file() and hit not in found[key]:
                        found[key].append(hit)
        except (OSError, ValueError):
            continue

    backup_dir = paths.milo_home() / "backups" / "engram"
    if backup_dir.is_dir():
        found["engram_export"] = sorted(backup_dir.glob("*.json"))[-3:]

    return {k: v for k, v in found.items() if v}


# ── Importers ─────────────────────────────────────────────────────────────────


def _import_sqlite(
    db_path: Path,
    brain: MemoryStore,
    *,
    source: str,
    default_category: str,
    limit_per_table: int = 20000,
) -> Dict[str, Any]:
    """Generic schema-tolerant SQLite importer used by Engram and the bot DB."""
    if not db_path.is_file():
        return {"imported": 0, "skipped": 0, "error": "not found"}

    # Read-only, and against a copy — never risk a live MCP server's database.
    tmp = paths.cache_dir() / f"import-{db_path.stem}-{int(time.time())}.db"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(db_path, tmp)
    except OSError as exc:
        return {"imported": 0, "skipped": 0, "error": f"copy failed: {exc}"}

    imported = skipped = 0
    tables_used: List[str] = []
    error: Optional[str] = None
    try:
        conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        for table in _tables(conn):
            cols = _columns(conn, table)
            mapping = _map_columns(cols)
            if "content" not in mapping and "title" not in mapping:
                continue  # nothing text-like in here
            try:
                rows = conn.execute(
                    f'SELECT * FROM "{table}" LIMIT {int(limit_per_table)}'
                ).fetchall()
            except sqlite3.Error:
                continue
            if not rows:
                continue
            tables_used.append(f"{table}({len(rows)})")
            for row in rows:
                d = dict(row)
                content = str(d.get(mapping.get("content", ""), "") or "").strip()
                title = str(d.get(mapping.get("title", ""), "") or "").strip()
                if not content:
                    content, title = title, ""
                if not content:
                    skipped += 1
                    continue
                created = _as_epoch(d.get(mapping.get("created_at", ""), None))
                try:
                    _, created_new = brain.save(
                        content,
                        title=title[:200],
                        category=str(
                            d.get(mapping.get("category", ""), "") or default_category
                        ).strip()[:40] or default_category,
                        project=str(
                            d.get(mapping.get("project", ""), "") or "milo"
                        ).strip()[:60] or "milo",
                        tags=_as_tags(d.get(mapping.get("tags", ""), None)) + [source],
                        importance=_as_importance(
                            d.get(mapping.get("importance", ""), None)
                        ),
                        source=source,
                        origin=f"{db_path.name}:{table}",
                        extra={"legacy_created_at": created, "legacy_table": table},
                    )
                except ValueError:
                    skipped += 1
                    continue
                imported += int(created_new)
                skipped += int(not created_new)
        conn.close()
    except sqlite3.Error as exc:
        error = str(exc)
    finally:
        tmp.unlink(missing_ok=True)

    out: Dict[str, Any] = {
        "imported": imported,
        "skipped": skipped,
        "tables": ", ".join(tables_used) or "none",
    }
    if error:
        out["error"] = error
    if not tables_used and not error:
        out["error"] = "no text-bearing tables recognised"
    return out


def import_engram(
    db_path: Optional[Path] = None, brain: Optional[MemoryStore] = None
) -> Dict[str, Any]:
    """Import the Engram hot tier (``~/.engram/engram.db``)."""
    brain = brain or default_store()
    db_path = db_path or (paths.engram_dir() / "engram.db")
    return _import_sqlite(
        db_path, brain, source="engram", default_category="observation"
    )


def import_bot_sqlite(
    db_path: Path, brain: Optional[MemoryStore] = None
) -> Dict[str, Any]:
    """Import the Telegram bot's private fallback SQLite store."""
    brain = brain or default_store()
    return _import_sqlite(
        db_path, brain, source="telegram-bot", default_category="note"
    )


def import_agentmemory_json(
    json_path: Path, brain: Optional[MemoryStore] = None
) -> Dict[str, Any]:
    """Import an AgentMemory / Supabase JSON dump.

    Accepts a bare list, ``{"memories": [...]}``, ``{"data": [...]}``, or a
    dict-of-dicts keyed by id — all four shapes have appeared in the wild.
    """
    brain = brain or default_store()
    if not json_path.is_file():
        return {"imported": 0, "skipped": 0, "error": "not found"}
    try:
        blob = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"imported": 0, "skipped": 0, "error": f"unreadable: {exc}"}

    if isinstance(blob, dict):
        for key in ("memories", "data", "observations", "items", "records"):
            if isinstance(blob.get(key), list):
                rows: Iterable[Any] = blob[key]
                break
        else:
            rows = [v for v in blob.values() if isinstance(v, dict)]
    elif isinstance(blob, list):
        rows = blob
    else:
        return {"imported": 0, "skipped": 0, "error": "unrecognised JSON shape"}

    imported = skipped = 0
    for row in rows:
        if isinstance(row, str):
            row = {"content": row}
        if not isinstance(row, dict):
            skipped += 1
            continue
        mapping = _map_columns(list(row.keys()))
        content = str(row.get(mapping.get("content", ""), "") or "").strip()
        title = str(row.get(mapping.get("title", ""), "") or "").strip()
        if not content:
            content, title = title, ""
        if not content:
            skipped += 1
            continue
        try:
            _, created = brain.save(
                content,
                title=title[:200],
                category=str(
                    row.get(mapping.get("category", ""), "") or "note"
                ).strip()[:40] or "note",
                project=str(row.get(mapping.get("project", ""), "") or "milo")[:60],
                tags=_as_tags(row.get(mapping.get("tags", ""), None)) + ["agentmemory"],
                importance=_as_importance(row.get(mapping.get("importance", ""), None)),
                source="agentmemory",
                origin=json_path.name,
            )
        except ValueError:
            skipped += 1
            continue
        imported += int(created)
        skipped += int(not created)
    return {"imported": imported, "skipped": skipped}


def import_engram_json_export(
    json_path: Path, brain: Optional[MemoryStore] = None
) -> Dict[str, Any]:
    """Import one of ``backup-engram.cjs``'s JSON exports.

    Those files are the only surviving copy of the hot tier on a machine where
    ``engram.db`` is already gone, so they get their own entry point.
    """
    result = import_agentmemory_json(json_path, brain)
    return result


def snapshot_sqlite(db_path: Path, label: str = "") -> Optional[Path]:
    """Copy a legacy database into ``$MILO_HOME/backups/legacy/`` before touching it."""
    if not db_path.is_file():
        return None
    out_dir = paths.backups_dir() / "legacy"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = out_dir / f"{label or db_path.stem}-{stamp}{db_path.suffix}"
    try:
        shutil.copy2(db_path, dest)
    except OSError:
        return None
    return dest


# ── One-shot ──────────────────────────────────────────────────────────────────


def migrate_all(
    brain: Optional[MemoryStore] = None, snapshot: bool = True
) -> MigrationReport:
    """Find and import every legacy store on this machine. Safe to re-run."""
    brain = brain or default_store()
    report = MigrationReport()
    found = discover_legacy()

    for db in found.get("engram", []):
        if snapshot:
            snapshot_sqlite(db, "engram")
        report.add("engram", db, **import_engram(db, brain))

    for db in found.get("bot_sqlite", []):
        if snapshot:
            snapshot_sqlite(db, "milo-bot")
        report.add("telegram-bot", db, **import_bot_sqlite(db, brain))

    for js in found.get("agentmemory", []):
        report.add("agentmemory", js, **import_agentmemory_json(js, brain))

    for js in found.get("engram_export", []):
        report.add("engram-export", js, **import_engram_json_export(js, brain))

    return report
