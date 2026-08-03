"""
milo.memory.curated — bounded, agent-curated memory (ported from Hermes).

The hot tier (``brain.sqlite``) is append-everything: cheap, unbounded,
searchable. That is the right shape for recall, and the wrong shape for a
system prompt — you cannot paste 4,000 observations into every session.

This module provides the missing middle: two small, hand-maintained files
that are injected into *every* session's system prompt.

``MEMORY.md``  What Milo has learned about the *environment and the work*:
               conventions, tool quirks, paths that bite, decisions that stuck.
``USER.md``    What Milo has learned about *Allan*: preferences, working
               style, standing expectations.

Both are hard-capped in characters (not tokens — char counts are
model-independent and stable). When a store is full, the agent must delete
something to add something. That constraint is the feature: it forces
curation instead of accretion, which is exactly rule 5 in Milo's operating
manual — *no bloat, consolidate, don't accrete*.

Frozen-snapshot semantics
-------------------------
The system prompt is built once per session from a snapshot. Mid-session
writes land on disk immediately (durable) but do **not** rewrite the live
prompt — that would invalidate the provider's prefix cache on every save
and make every subsequent turn more expensive. The new content is picked up
at the next session boot.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from ..paths import MiloPaths, get_paths

__all__ = ["CuratedMemory", "Target", "MEMORY_TOOL_SCHEMA"]

Target = Literal["memory", "user"]

#: Entries are separated by a lone section sign so entries can be multiline.
DELIMITER = "\n§\n"

HEADERS: Dict[str, str] = {
    "memory": "MEMORY (what you have learned)",
    "user": "USER PROFILE (who Allan is)",
}

FILENAMES: Dict[str, str] = {"memory": "MEMORY.md", "user": "USER.md"}

DEFAULT_LIMITS: Dict[str, int] = {"memory": 2200, "user": 1375}


@dataclass
class WriteResult:
    ok: bool
    message: str
    used: int = 0
    limit: int = 0
    entries: int = 0

    def as_text(self) -> str:
        if not self.ok:
            return f"error: {self.message}"
        pct = int(100 * self.used / self.limit) if self.limit else 0
        return f"{self.message} [{self.used}/{self.limit} chars, {pct}% full, {self.entries} entries]"


class CuratedMemory:
    """Read/write the two bounded markdown stores."""

    def __init__(
        self,
        paths: Optional[MiloPaths] = None,
        *,
        memory_char_limit: int = DEFAULT_LIMITS["memory"],
        user_char_limit: int = DEFAULT_LIMITS["user"],
    ):
        self.paths = paths or get_paths()
        self.limits = {"memory": int(memory_char_limit), "user": int(user_char_limit)}
        self.entries: Dict[str, List[str]] = {"memory": [], "user": []}
        self._snapshot: Optional[str] = None
        self.load()

    # -- paths ------------------------------------------------------------

    def path_for(self, target: Target) -> Path:
        return self.paths.memories_dir / FILENAMES[self._norm(target)]

    @staticmethod
    def _norm(target: str) -> Target:
        t = (target or "memory").strip().lower()
        if t in ("user", "user.md", "profile"):
            return "user"
        return "memory"

    # -- io ---------------------------------------------------------------

    def load(self) -> "CuratedMemory":
        self.paths.memories_dir.mkdir(parents=True, exist_ok=True)
        for target in ("memory", "user"):
            self.entries[target] = self._read(self.path_for(target))  # type: ignore[arg-type]
        self._snapshot = self.render_block()
        return self

    @staticmethod
    def _read(path: Path) -> List[str]:
        if not path.is_file():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        return [chunk.strip() for chunk in raw.split(DELIMITER.strip()) if chunk.strip()]

    def _write(self, target: Target) -> None:
        path = self.path_for(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = DELIMITER.join(self.entries[target])
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(body + ("\n" if body else ""), encoding="utf-8")
        os.replace(tmp, path)  # atomic on every supported platform

    # -- measurements -----------------------------------------------------

    def used(self, target: Target) -> int:
        target = self._norm(target)
        return len(DELIMITER.join(self.entries[target]))

    def limit(self, target: Target) -> int:
        return self.limits[self._norm(target)]

    def is_full(self, target: Target) -> bool:
        return self.used(target) >= self.limit(target)

    # -- mutations --------------------------------------------------------

    def add(self, target: Target, text: str) -> WriteResult:
        """Append one entry, refusing to exceed the character budget."""
        target = self._norm(target)
        text = (text or "").strip()
        if not text:
            return WriteResult(False, "empty entry")
        if any(text == existing for existing in self.entries[target]):
            return self._result(target, "already present (no change)")

        projected = self.used(target) + len(text) + (len(DELIMITER) if self.entries[target] else 0)
        cap = self.limit(target)
        if projected > cap:
            over = projected - cap
            return WriteResult(
                False,
                f"would exceed the {cap}-char budget by {over}. "
                f"Remove or condense an entry first — this store is deliberately "
                f"small so it stays worth reading.",
                used=self.used(target), limit=cap, entries=len(self.entries[target]),
            )
        self.entries[target].append(text)
        self._write(target)
        return self._result(target, "added")

    def replace(self, target: Target, needle: str, text: str) -> WriteResult:
        """Replace the single entry containing ``needle``."""
        target = self._norm(target)
        matches = self._find(target, needle)
        if len(matches) == 0:
            return WriteResult(False, f"no entry matches {needle!r}")
        if len(matches) > 1:
            return WriteResult(
                False,
                f"{needle!r} matches {len(matches)} entries — use a longer, "
                f"unique substring",
            )
        index = matches[0]
        old = self.entries[target][index]
        projected = self.used(target) - len(old) + len(text.strip())
        if projected > self.limit(target):
            return WriteResult(
                False,
                f"replacement would exceed the {self.limit(target)}-char budget",
                used=self.used(target), limit=self.limit(target),
            )
        self.entries[target][index] = text.strip()
        self._write(target)
        return self._result(target, "replaced")

    def remove(self, target: Target, needle: str) -> WriteResult:
        target = self._norm(target)
        matches = self._find(target, needle)
        if len(matches) == 0:
            return WriteResult(False, f"no entry matches {needle!r}")
        if len(matches) > 1:
            return WriteResult(
                False,
                f"{needle!r} matches {len(matches)} entries — use a longer, "
                f"unique substring",
            )
        self.entries[target].pop(matches[0])
        self._write(target)
        return self._result(target, "removed")

    def _find(self, target: Target, needle: str) -> List[int]:
        needle = (needle or "").strip().lower()
        if not needle:
            return []
        return [
            i for i, entry in enumerate(self.entries[target]) if needle in entry.lower()
        ]

    def _result(self, target: Target, message: str) -> WriteResult:
        return WriteResult(
            True, message,
            used=self.used(target),
            limit=self.limit(target),
            entries=len(self.entries[target]),
        )

    # -- rendering --------------------------------------------------------

    def render_block(self) -> str:
        """The text injected into a system prompt. Empty stores render nothing."""
        chunks: List[str] = []
        for target in ("memory", "user"):
            entries = self.entries[target]
            if not entries:
                continue
            body = "\n".join(f"- {e}" if not e.startswith("-") else e for e in entries)
            chunks.append(f"## {HEADERS[target]}\n\n{body}")
        return "\n\n".join(chunks)

    @property
    def snapshot(self) -> str:
        """The frozen copy captured at load time (stable for the whole session)."""
        return self._snapshot or ""

    def summary(self) -> str:
        lines = []
        for target in ("memory", "user"):
            lines.append(
                f"{FILENAMES[target]}: {len(self.entries[target])} entries, "
                f"{self.used(target)}/{self.limit(target)} chars"  # type: ignore[arg-type]
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool schema — exported so every harness exposes the same `memory` tool
# ---------------------------------------------------------------------------

MEMORY_TOOL_SCHEMA: Dict[str, object] = {
    "name": "memory",
    "description": (
        "Curate your durable memory. Two stores, both injected into every future "
        "session's system prompt:\n"
        "  target='memory' — environment facts, conventions, tool quirks, decisions "
        "that stuck, things you learned the hard way.\n"
        "  target='user'   — who Allan is: preferences, working style, standing "
        "expectations.\n\n"
        "Both are small on purpose. When a store is full you must remove or condense "
        "something to add something. Write entries that will still make sense to a "
        "session that has no other context. Prefer one dense sentence over three "
        "vague ones. Do not store secrets, tokens, or anything you could look up in "
        "seconds."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "view"],
                "description": "What to do.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "default": "memory",
            },
            "text": {
                "type": "string",
                "description": "New entry text (for add/replace).",
            },
            "match": {
                "type": "string",
                "description": (
                    "A short substring that uniquely identifies the existing entry "
                    "to replace or remove."
                ),
            },
        },
        "required": ["action"],
    },
}


def dispatch(store: CuratedMemory, **kwargs: object) -> str:
    """Execute a ``memory`` tool call against a store. Returns display text."""
    action = str(kwargs.get("action") or "view").lower()
    target = str(kwargs.get("target") or "memory")
    text = str(kwargs.get("text") or "")
    match = str(kwargs.get("match") or "")

    if action == "view":
        block = store.render_block()
        return block or "(both stores are empty)"
    if action == "add":
        return store.add(target, text).as_text()  # type: ignore[arg-type]
    if action == "replace":
        return store.replace(target, match, text).as_text()  # type: ignore[arg-type]
    if action == "remove":
        return store.remove(target, match).as_text()  # type: ignore[arg-type]
    return f"error: unknown action {action!r}"
