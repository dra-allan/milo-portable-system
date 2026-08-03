"""
milo.memory.engram — import from / export to the legacy Engram store.

Why this exists
---------------
Engram was Milo's hot tier: a SQLite database at ``~/.engram/engram.db``
written by the ``engram`` MCP server. ``backup-engram.cjs`` copied it into
``~/.milo/backups/engram/`` — *a local folder that was never pushed
anywhere*. Change machines and the entire hot tier is gone.

This module is the bridge off that cliff:

* :func:`import_engram` pulls every observation Engram knows about into the
  unified brain, so nothing is lost during migration.
* :func:`sync_from_engram` can be run repeatedly (it is idempotent) if you
  keep the Engram MCP server running alongside Milo during a transition.

Engram's schema has shifted between releases, so rather than hardcoding one
layout we introspect the database and map whatever columns look right. If we
cannot make sense of it we say so loudly instead of silently importing zero
rows.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..paths import MiloPaths, get_paths
from .store import Brain

__all__ = [
    "engram_available",
    "inspect_engram",
    "import_engram",
    "import_engram_export",
    "snapshot_engram",
]


# Column-name candidates, in priority order, for each field we care about.
_FIELD_CANDIDATES: Dict[str, Sequence[str]] = {
    "title": ("title", "name", "summary", "heading", "subject"),
    "content": ("content", "body", "text", "observation", "detail", "value"),
    "kind": ("type", "kind", "category", "obs_type"),
    "scope": ("scope", "visibility"),
    "project": ("project", "project_name", "workspace", "repo"),
    "tags": ("tags", "labels", "keywords"),
    "importance": ("importance", "weight", "priority", "strength"),
    "topic_key": ("topic_key", "topic", "key", "slug"),
    "created_at": ("created_at", "created", "timestamp", "ts", "inserted_at"),
    "updated_at": ("updated_at", "updated", "modified_at", "last_updated"),
    "id": ("id", "uuid", "observation_id", "rowid"),
}

# Tables we will consider as "the observations table", best first.
_TABLE_CANDIDATES = (
    "observations",
    "observation",
    "memories",
    "memory",
    "entries",
    "notes",
    "facts",
)


def engram_available(paths: Optional[MiloPaths] = None) -> bool:
    paths = paths or get_paths()
    return paths.engram_db.is_file()


def _engram_binary(paths: MiloPaths) -> Optional[Path]:
    """Locate the ``engram`` CLI if it is installed."""
    found = shutil.which("engram")
    if found:
        return Path(found)
    for candidate in (
        paths.home / "bin" / "engram.exe",
        paths.home / "bin" / "engram",
        paths.bin_dir / "engram.exe",
        paths.bin_dir / "engram",
    ):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def inspect_engram(db_path: Optional[Path] = None, paths: Optional[MiloPaths] = None) -> Dict[str, Any]:
    """Describe an Engram database without modifying it."""
    paths = paths or get_paths()
    db_path = Path(db_path) if db_path else paths.engram_db
    report: Dict[str, Any] = {"path": str(db_path), "exists": db_path.is_file()}
    if not report["exists"]:
        return report

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
    except sqlite3.DatabaseError as exc:
        report["error"] = str(exc)
        return report

    try:
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        report["tables"] = tables
        table = _pick_table(conn, tables)
        report["observations_table"] = table
        if table:
            cols = [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            report["columns"] = cols
            report["mapping"] = _build_mapping(cols)
            report["rows"] = conn.execute(
                f'SELECT COUNT(*) c FROM "{table}"'
            ).fetchone()["c"]
        report["size_kb"] = round(db_path.stat().st_size / 1024, 1)
    finally:
        conn.close()
    return report


def _pick_table(conn: sqlite3.Connection, tables: Sequence[str]) -> Optional[str]:
    lowered = {t.lower(): t for t in tables}
    for candidate in _TABLE_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    # Fall back to the largest table that has something content-shaped.
    best: Tuple[int, Optional[str]] = (0, None)
    for table in tables:
        try:
            cols = {r[1].lower() for r in conn.execute(f'PRAGMA table_info("{table}")')}
            if not (cols & set(_FIELD_CANDIDATES["content"]) or cols & set(_FIELD_CANDIDATES["title"])):
                continue
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count > best[0]:
                best = (count, table)
        except sqlite3.DatabaseError:
            continue
    return best[1]


def _build_mapping(columns: Sequence[str]) -> Dict[str, str]:
    lowered = {c.lower(): c for c in columns}
    mapping: Dict[str, str] = {}
    for field, candidates in _FIELD_CANDIDATES.items():
        for candidate in candidates:
            if candidate in lowered:
                mapping[field] = lowered[candidate]
                break
    return mapping


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_engram(
    brain: Brain,
    db_path: Optional[Path] = None,
    paths: Optional[MiloPaths] = None,
    *,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Copy every Engram observation into the unified brain. Idempotent."""
    paths = paths or get_paths()
    db_path = Path(db_path) if db_path else paths.engram_db
    report = inspect_engram(db_path, paths)
    if not report.get("exists"):
        return {"ok": False, "reason": "no engram.db found", "imported": 0}
    table = report.get("observations_table")
    mapping: Dict[str, str] = report.get("mapping") or {}
    if not table or not (mapping.get("title") or mapping.get("content")):
        return {
            "ok": False,
            "reason": f"could not identify an observations table (saw {report.get('tables')})",
            "imported": 0,
        }

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    seen = skipped = 0
    before = brain.stats()["total"]
    try:
        sql = f'SELECT * FROM "{table}"'
        if mapping.get("updated_at"):
            sql += f' ORDER BY "{mapping["updated_at"]}"'
        if limit:
            sql += f" LIMIT {int(limit)}"
        for row in conn.execute(sql):
            data = dict(row)
            title = _first(data, mapping, "title") or _derive_title(
                _first(data, mapping, "content")
            )
            if not title:
                skipped += 1
                continue
            brain.save(
                title=str(title)[:400],
                content=str(_first(data, mapping, "content") or ""),
                kind=_normalise_kind(_first(data, mapping, "kind")),
                scope=str(_first(data, mapping, "scope") or "project"),
                project=str(_first(data, mapping, "project") or "milo"),
                tags=_normalise_tags(_first(data, mapping, "tags")),
                importance=_normalise_importance(_first(data, mapping, "importance")),
                topic_key=_opt_str(_first(data, mapping, "topic_key")),
                source="engram-import",
            )
            seen += 1
    finally:
        conn.close()

    after = brain.stats()["total"]
    added = after - before
    return {
        "ok": True,
        "read": seen,
        "added": added,
        "merged": seen - added,  # already present; refreshed in place
        "skipped": skipped,
        "table": table,
        "source": str(db_path),
    }


def import_engram_export(brain: Brain, json_path: Path) -> Dict[str, Any]:
    """Import a JSON export produced by ``engram export`` (or our own backup)."""
    json_path = Path(json_path)
    if not json_path.is_file():
        return {"ok": False, "reason": f"no such file: {json_path}", "imported": 0}
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "reason": str(exc), "imported": 0}

    # Our own format round-trips natively.
    if isinstance(payload, Mapping) and payload.get("format") == "milo-brain":
        result = brain.import_dict(payload)
        return {"ok": True, "imported": result["added"], **result}

    records = _extract_records(payload)
    imported = 0
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        title = (
            raw.get("title")
            or raw.get("name")
            or raw.get("summary")
            or _derive_title(raw.get("content") or raw.get("body") or raw.get("text"))
        )
        if not title:
            continue
        brain.save(
            title=str(title)[:400],
            content=str(raw.get("content") or raw.get("body") or raw.get("text") or ""),
            kind=_normalise_kind(raw.get("type") or raw.get("kind") or raw.get("category")),
            scope=str(raw.get("scope") or "project"),
            project=str(raw.get("project") or raw.get("project_name") or "milo"),
            tags=_normalise_tags(raw.get("tags")),
            importance=_normalise_importance(raw.get("importance")),
            topic_key=_opt_str(raw.get("topic_key")),
            source="engram-export",
        )
        imported += 1
    return {"ok": True, "imported": imported, "source": str(json_path)}


def _extract_records(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in ("observations", "memories", "entries", "records", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        # The AgentMemory JSON store nests under state.memories
        state = payload.get("state")
        if isinstance(state, Mapping) and isinstance(state.get("memories"), list):
            return state["memories"]
    return []


# ---------------------------------------------------------------------------
# Snapshot (used by `milo backup`)
# ---------------------------------------------------------------------------


def snapshot_engram(paths: Optional[MiloPaths] = None) -> Dict[str, Any]:
    """Copy engram.db aside and try a JSON export. Non-fatal on failure."""
    paths = paths or get_paths()
    if not paths.engram_db.is_file():
        return {"ok": True, "skipped": "no engram.db"}

    out_dir = paths.backups_dir / "engram"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    result: Dict[str, Any] = {"ok": True}

    db_copy = out_dir / f"engram-{stamp}.db"
    try:
        # sqlite3's backup API is safe against a concurrently-writing server;
        # a plain file copy is not.
        src = sqlite3.connect(f"file:{paths.engram_db}?mode=ro", uri=True, timeout=15)
        dst = sqlite3.connect(str(db_copy))
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        result["db"] = str(db_copy)
    except sqlite3.DatabaseError as exc:
        try:
            shutil.copy2(paths.engram_db, db_copy)
            result["db"] = str(db_copy)
            result["warning"] = f"used file copy fallback: {exc}"
        except OSError as exc2:
            result["ok"] = False
            result["error"] = str(exc2)

    binary = _engram_binary(paths)
    if binary:
        export_file = out_dir / f"engram-export-{stamp}.json"
        try:
            proc = subprocess.run(
                [str(binary), "export", str(export_file)],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0 and export_file.is_file():
                result["export"] = str(export_file)
            else:
                result["export_error"] = (proc.stderr or proc.stdout).strip()[:300]
        except (OSError, subprocess.SubprocessError) as exc:
            result["export_error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------


def _first(data: Mapping[str, Any], mapping: Mapping[str, str], field: str) -> Any:
    column = mapping.get(field)
    return data.get(column) if column else None


def _opt_str(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _derive_title(content: Any) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0].lstrip("#").strip()
    return first[:120]


def _normalise_kind(value: Any) -> str:
    from .store import KINDS

    text = str(value or "").strip().lower()
    if text in KINDS:
        return text
    aliases = {
        "fact": "note",
        "insight": "discovery",
        "learning": "discovery",
        "bug": "bugfix",
        "fix": "bugfix",
        "arch": "architecture",
        "design": "architecture",
        "pref": "preference",
        "rule": "constraint",
    }
    return aliases.get(text, "note")


def _normalise_tags(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip().lstrip("#") for v in value if str(v).strip()]
    text = str(value).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip().lstrip("#") for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
    return [t.strip().lstrip("#") for t in text.replace(";", ",").split(",") if t.strip()]


def _normalise_importance(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 3
    # Some stores use 0..1 strength scores.
    if 0 < number <= 1:
        number *= 5
    return max(1, min(5, int(round(number))))
