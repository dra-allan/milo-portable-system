"""
curated.py — the bounded memory that is always in the prompt.
=============================================================

Milo has three memory tiers and they answer different questions.

``memory.py``   The hot brain: SQLite, append-everything, FTS-searchable.
                Unbounded and cheap. Answers *"what do I know about X?"* —
                but only when something thinks to ask.
``vault.py``    The cold tier: Obsidian markdown, effectively infinite.
                Answers *"what did we write down months ago?"*
``curated.py``  **This module.** Two small markdown files injected into every
                single session, whether or not anyone runs a search.

The middle tier is the one people forget, and it is the one that makes an
agent feel like it knows you. Retrieval only fires on a query; if Milo never
searches, Milo never learns that you hate preamble. These two files are
always present, so the lesson applies on turn one of a brand new session.

``MEMORY.md``   The environment and the work: conventions, tool quirks, paths
                that bite, decisions that stuck.
``USER.md``     Allan: preferences, working style, standing expectations.

Both are hard-capped **in characters**, not tokens — char counts are
model-independent, stable across tokenizer changes, and can be checked
without a network call. When a store is full the agent must delete something
to add something. That constraint is the whole point: it forces curation
instead of accretion.

Frozen-snapshot semantics
-------------------------
The system prompt is assembled once per session. Mid-session writes hit disk
immediately (durable across a crash) but do **not** rewrite the live prompt.
Rewriting it would invalidate the provider's prefix cache on every save and
make every later turn in that session more expensive. New content is picked
up at the next session boot.

Ported from Hermes' curated-memory model and adapted to Milo's path layer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from . import paths

__all__ = [
    "CuratedMemory", "WriteResult", "MEMORY_TOOL_SCHEMA",
    "dispatch", "store", "render_block",
]

#: Entries are separated by a lone section sign on its own line, so a single
#: entry can safely span multiple lines (a bullet list, a short snippet).
DELIMITER = "\n§\n"

HEADERS: Dict[str, str] = {
    "memory": "MEMORY (what you have learned)",
    "user": "USER PROFILE (who Allan is)",
}

FILENAMES: Dict[str, str] = {"memory": "MEMORY.md", "user": "USER.md"}

#: Roughly 550 and 350 tokens. Small enough that the whole block is worth
#: reading on every turn; large enough for perhaps 20 dense lessons.
DEFAULT_LIMITS: Dict[str, int] = {"memory": 2200, "user": 1375}


@dataclass
class WriteResult:
    ok: bool
    message: str
    used: int = 0
    limit: int = 0
    entries: int = 0
    #: Did anything on disk actually change? A deduped ``add`` is a success
    #: (``ok=True``) that changed nothing, and callers such as ``milo restore``
    #: need to tell those apart to report honest counts. Sniffing the message
    #: string for "already present" would work until someone reworded it.
    changed: bool = True

    def as_text(self) -> str:
        if not self.ok:
            return f"error: {self.message}"
        pct = int(100 * self.used / self.limit) if self.limit else 0
        return (f"{self.message} [{self.used}/{self.limit} chars, "
                f"{pct}% full, {self.entries} entries]")


class CuratedMemory:
    """Read/write the two bounded markdown stores."""

    def __init__(
        self,
        directory: Optional[Path] = None,
        *,
        memory_char_limit: int = DEFAULT_LIMITS["memory"],
        user_char_limit: int = DEFAULT_LIMITS["user"],
    ) -> None:
        self.dir = Path(directory) if directory else paths.memories_dir()
        self.limits = {"memory": int(memory_char_limit), "user": int(user_char_limit)}
        self.entries: Dict[str, List[str]] = {"memory": [], "user": []}
        self._snapshot: Optional[str] = None
        self.load()

    # -- paths -----------------------------------------------------------

    def path_for(self, target: str) -> Path:
        return self.dir / FILENAMES[self._norm(target)]

    @staticmethod
    def _norm(target: str) -> str:
        """Accept the several names people reach for. 'user'/'profile' → user."""
        t = (target or "memory").strip().lower()
        if t in ("user", "user.md", "profile", "me", "allan"):
            return "user"
        return "memory"

    # -- io --------------------------------------------------------------

    def load(self) -> "CuratedMemory":
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # read-only home: still serve whatever we can read
        for target in ("memory", "user"):
            self.entries[target] = self._read(self.path_for(target))
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
        return [c.strip() for c in raw.split(DELIMITER.strip()) if c.strip()]

    def _write(self, target: str) -> None:
        path = self.path_for(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = DELIMITER.join(self.entries[self._norm(target)])
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(body + ("\n" if body else ""), encoding="utf-8")
        os.replace(tmp, path)  # atomic on every supported platform

    # -- measurements ----------------------------------------------------

    def used(self, target: str) -> int:
        return len(DELIMITER.join(self.entries[self._norm(target)]))

    def limit(self, target: str) -> int:
        return self.limits[self._norm(target)]

    def is_full(self, target: str) -> bool:
        return self.used(target) >= self.limit(target)

    def count(self, target: str) -> int:
        return len(self.entries[self._norm(target)])

    # -- mutations -------------------------------------------------------

    def add(self, target: str, text: str) -> WriteResult:
        """Append one entry, refusing to exceed the character budget."""
        target = self._norm(target)
        text = (text or "").strip()
        if not text:
            return WriteResult(False, "empty entry")
        if any(text == existing for existing in self.entries[target]):
            return self._result(target, "already present (no change)", changed=False)

        sep = len(DELIMITER) if self.entries[target] else 0
        projected = self.used(target) + len(text) + sep
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

    def replace(self, target: str, needle: str, text: str) -> WriteResult:
        """Replace the single entry containing ``needle``."""
        target = self._norm(target)
        matches = self._find(target, needle)
        if not matches:
            return WriteResult(False, f"no entry matches {needle!r}")
        if len(matches) > 1:
            return WriteResult(
                False,
                f"{needle!r} matches {len(matches)} entries — "
                f"use a longer, unique substring",
            )
        index = matches[0]
        old = self.entries[target][index]
        text = (text or "").strip()
        if not text:
            return WriteResult(False, "empty replacement — use remove instead")
        projected = self.used(target) - len(old) + len(text)
        if projected > self.limit(target):
            return WriteResult(
                False,
                f"replacement would exceed the {self.limit(target)}-char budget",
                used=self.used(target), limit=self.limit(target),
            )
        self.entries[target][index] = text
        self._write(target)
        return self._result(target, "replaced")

    def remove(self, target: str, needle: str) -> WriteResult:
        target = self._norm(target)
        matches = self._find(target, needle)
        if not matches:
            return WriteResult(False, f"no entry matches {needle!r}")
        if len(matches) > 1:
            return WriteResult(
                False,
                f"{needle!r} matches {len(matches)} entries — "
                f"use a longer, unique substring",
            )
        self.entries[target].pop(matches[0])
        self._write(target)
        return self._result(target, "removed")

    def clear(self, target: str) -> WriteResult:
        target = self._norm(target)
        self.entries[target] = []
        self._write(target)
        return self._result(target, "cleared")

    def _find(self, target: str, needle: str) -> List[int]:
        needle = (needle or "").strip().lower()
        if not needle:
            return []
        return [i for i, e in enumerate(self.entries[target]) if needle in e.lower()]

    def _result(self, target: str, message: str,
                changed: bool = True) -> WriteResult:
        return WriteResult(
            True, message,
            used=self.used(target),
            limit=self.limit(target),
            entries=len(self.entries[target]),
            changed=changed,
        )

    # -- rendering -------------------------------------------------------

    def render_block(self) -> str:
        """The text injected into a system prompt. Empty stores render nothing."""
        chunks: List[str] = []
        for target in ("memory", "user"):
            entries = self.entries[target]
            if not entries:
                continue
            body = "\n".join(
                f"- {e}" if not e.lstrip().startswith(("-", "*", "#")) else e
                for e in entries
            )
            chunks.append(f"## {HEADERS[target]}\n\n{body}")
        return "\n\n".join(chunks)

    @property
    def snapshot(self) -> str:
        """The frozen copy captured at load time (stable for the whole session)."""
        return self._snapshot or ""

    def stats(self) -> Dict[str, object]:
        out: Dict[str, object] = {"dir": str(self.dir)}
        for target in ("memory", "user"):
            out[target] = {
                "file": str(self.path_for(target)),
                "entries": len(self.entries[target]),
                "used": self.used(target),
                "limit": self.limit(target),
                "pct": int(100 * self.used(target) / self.limit(target))
                if self.limit(target) else 0,
            }
        return out

    def summary(self) -> str:
        return "\n".join(
            f"{FILENAMES[t]}: {len(self.entries[t])} entries, "
            f"{self.used(t)}/{self.limit(t)} chars"
            for t in ("memory", "user")
        )


# ── the tool every harness exposes ────────────────────────────────────────────

MEMORY_TOOL_SCHEMA: Dict[str, object] = {
    "name": "memory",
    "description": (
        "Curate your durable memory. Two stores, both injected into every future "
        "session's system prompt:\n"
        "  target='memory' — environment facts, conventions, tool quirks, "
        "decisions that stuck, things you learned the hard way.\n"
        "  target='user'   — who Allan is: preferences, working style, standing "
        "expectations.\n\n"
        "Both are small on purpose. When a store is full you must remove or "
        "condense something to add something. Write entries that will still make "
        "sense to a session with no other context. Prefer one dense sentence over "
        "three vague ones. Never store secrets, tokens, or anything you could look "
        "up in seconds."
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
                    "A short substring uniquely identifying the existing entry "
                    "to replace or remove."
                ),
            },
        },
        "required": ["action"],
    },
}


def dispatch(mem: Optional[CuratedMemory] = None, **kwargs: object) -> str:
    """Execute a ``memory`` tool call. Returns display text for the model."""
    mem = mem or store()
    action = str(kwargs.get("action") or "view").lower()
    target = str(kwargs.get("target") or "memory")
    text = str(kwargs.get("text") or "")
    match = str(kwargs.get("match") or "")

    if action == "view":
        return mem.render_block() or "(both stores are empty)"
    if action == "add":
        return mem.add(target, text).as_text()
    if action == "replace":
        return mem.replace(target, match, text).as_text()
    if action == "remove":
        return mem.remove(target, match).as_text()
    return f"error: unknown action {action!r}"


# ── module-level convenience ──────────────────────────────────────────────────

_STORE: Optional[CuratedMemory] = None


def store() -> CuratedMemory:
    global _STORE
    if _STORE is None:
        _STORE = CuratedMemory()
    return _STORE


def render_block() -> str:
    """Fresh read every call — used when assembling a prompt."""
    return CuratedMemory().render_block()
