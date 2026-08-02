"""
vault.py — the cold tier (Obsidian / dra-brains).
=================================================

The vault is Milo's long-term, *human-readable* memory. Unlike the hot tier
(``miloctl.memory``) it is a git repo full of markdown that Allan can open in
Obsidian, correct by hand, and read on a phone. It is where knowledge goes to
stay.

Everything here is relative to :func:`miloctl.paths.vault_dir`. There is not a
single hardcoded ``C:/Users/user/...`` in this module — that was the original
migration blocker, and it is gone.

Responsibilities
----------------
* locate the vault wherever it lives on this machine
* read the boot files (identity, handoff, priorities, today's note)
* append to today's daily note
* **promote** hot memories into durable vault notes at task boundaries
* full-text search across the markdown
* git pull / commit / push so the cold tier travels with the machine
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths
from .naming import display_name

__all__ = ["Vault", "VaultLayout", "VaultHit", "vault"]


# ── Layout ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VaultLayout:
    """Folder + file names inside the vault.

    Defaults match the real ``dra-brains`` vault. Override via ``.env`` keys
    (``VAULT_DAILY_DIR`` etc.) if the vault is ever reshaped, so a rename does
    not mean editing code.
    """

    inbox: str = "00 - Inbox"
    daily: str = "01 - Daily Notes"
    resources: str = "11 - Resources"
    milo_notes: str = "09 - Personal/Milo"

    boot_file: str = "CLAUDE.md"
    index_file: str = "VAULT-INDEX.md"
    handoff_file: str = "Session Handoff.md"
    priorities_file: str = "Active Priorities.md"

    @classmethod
    def from_env(cls) -> "VaultLayout":
        env = paths._read_env_file(paths.env_file())  # noqa: SLF001 - intentional
        get = lambda k, d: (os.environ.get(k) or env.get(k) or d).strip()  # noqa: E731
        return cls(
            inbox=get("VAULT_INBOX_DIR", cls.inbox),
            daily=get("VAULT_DAILY_DIR", cls.daily),
            resources=get("VAULT_RESOURCES_DIR", cls.resources),
            milo_notes=get("VAULT_MILO_DIR", cls.milo_notes),
            boot_file=get("VAULT_BOOT_FILE", cls.boot_file),
            index_file=get("VAULT_INDEX_FILE", cls.index_file),
            handoff_file=get("VAULT_HANDOFF_FILE", cls.handoff_file),
            priorities_file=get("VAULT_PRIORITIES_FILE", cls.priorities_file),
        )


@dataclass
class VaultHit:
    """One search result inside the vault."""

    path: Path
    line_no: int
    line: str
    rel: str = ""

    def render(self, width: int = 100) -> str:
        text = self.line.strip()
        if len(text) > width:
            text = text[: width - 1] + "…"
        return f"{self.rel}:{self.line_no}: {text}"


_SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", ".venv", "__pycache__"}
_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".canvas"}


# ── Vault ─────────────────────────────────────────────────────────────────────


class Vault:
    """Read/write access to the markdown vault.

    Every method degrades gracefully when the vault is missing: Milo must
    still boot on a fresh machine before ``milo install`` has cloned it.
    """

    def __init__(self, root: Optional[Path] = None, layout: Optional[VaultLayout] = None):
        self.root = Path(root) if root else paths.vault_dir()
        self.layout = layout or VaultLayout.from_env()

    # -- existence -------------------------------------------------------------

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    @property
    def is_git(self) -> bool:
        return (self.root / ".git").exists()

    def require(self) -> Path:
        if not self.exists:
            raise FileNotFoundError(
                f"vault not found at {self.root}\n"
                f"Set VAULT_DIR in {paths.env_file()} or run: milo install"
            )
        return self.root

    # -- paths -----------------------------------------------------------------

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def daily_note_path(self, when: Optional[date] = None) -> Path:
        when = when or date.today()
        return self.path(self.layout.daily, f"{when.isoformat()}.md")

    def milo_note_path(self, slug: str) -> Path:
        safe = re.sub(r"[^a-z0-9._ -]+", "", slug.lower().strip()).strip() or "note"
        return self.path(self.layout.milo_notes, f"{safe}.md")

    # -- reads -----------------------------------------------------------------

    def read(self, rel: str, limit: Optional[int] = None) -> str:
        p = self.path(rel)
        if not p.is_file():
            return ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if limit and len(text) > limit:
            return text[:limit] + f"\n\n…[truncated, {len(text)} chars total]"
        return text

    def boot_context(self, budget: int = 12000) -> Dict[str, str]:
        """The handful of files Milo reads at the start of a session.

        Returns a mapping of label -> content, already length-capped so the
        caller can splice it straight into a system prompt.
        """
        if not self.exists:
            return {}
        wanted = [
            ("identity", self.layout.boot_file),
            ("index", self.layout.index_file),
            ("handoff", self.layout.handoff_file),
            ("priorities", self.layout.priorities_file),
        ]
        out: Dict[str, str] = {}
        per_file = max(1200, budget // max(1, len(wanted) + 1))
        for label, rel in wanted:
            body = self.read(rel, limit=per_file)
            if body.strip():
                out[label] = body
        today = self.daily_note_path()
        if today.is_file():
            out["today"] = self.read(
                str(today.relative_to(self.root)), limit=per_file
            )
        return out

    # -- writes ----------------------------------------------------------------

    def append_daily(self, text: str, heading: str = "", when: Optional[date] = None) -> Path:
        """Append a timestamped block to today's daily note, creating it if needed."""
        p = self.daily_note_path(when)
        p.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M")
        block = []
        if not p.exists():
            d = (when or date.today()).isoformat()
            block.append(f"# {d}\n")
        if heading:
            block.append(f"\n## {heading}\n")
        block.append(f"\n- **{stamp}** — {text.strip()}\n")
        with p.open("a", encoding="utf-8") as fh:
            fh.write("".join(block))
        return p

    def write_note(
        self,
        rel: str,
        body: str,
        *,
        frontmatter: Optional[Dict[str, object]] = None,
        overwrite: bool = True,
    ) -> Path:
        p = self.path(rel)
        if p.exists() and not overwrite:
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        chunks: List[str] = []
        if frontmatter:
            chunks.append("---")
            for k, v in frontmatter.items():
                if isinstance(v, (list, tuple)):
                    chunks.append(f"{k}: [{', '.join(str(x) for x in v)}]")
                else:
                    chunks.append(f"{k}: {v}")
            chunks.append("---\n")
        chunks.append(body.rstrip() + "\n")
        p.write_text("\n".join(chunks), encoding="utf-8")
        return p

    def capture(self, text: str, title: str = "") -> Path:
        """Drop something into the inbox for later triage."""
        slug = re.sub(r"[^a-z0-9 -]+", "", (title or text[:48]).lower()).strip()
        slug = re.sub(r"\s+", "-", slug) or "capture"
        rel = f"{self.layout.inbox}/{date.today().isoformat()}-{slug}.md"
        return self.write_note(
            rel,
            text,
            frontmatter={
                "created": datetime.now().isoformat(timespec="seconds"),
                "source": display_name().lower(),
                "status": "inbox",
            },
            overwrite=False,
        )

    # -- promotion (hot -> cold) ----------------------------------------------

    def promote(self, memories: Sequence[object], label: str = "") -> Optional[Path]:
        """Write hot memories into a durable, human-readable vault note.

        Called at task boundaries and by ``milo backup``. Accepts anything with
        ``.content`` / ``.title`` / ``.category`` / ``.tags`` attributes, which
        keeps this module decoupled from the memory schema.
        """
        rows = [m for m in memories if getattr(m, "content", "").strip()]
        if not rows or not self.exists:
            return None
        stamp = date.today().isoformat()
        slug = re.sub(r"[^a-z0-9 -]+", "", (label or "memory").lower()).strip()
        slug = re.sub(r"\s+", "-", slug) or "memory"
        rel = f"{self.layout.milo_notes}/{stamp}-{slug}.md"
        lines = [f"# {label or 'Milo memory'} — {stamp}", ""]
        by_cat: Dict[str, List[object]] = {}
        for m in rows:
            by_cat.setdefault(getattr(m, "category", "note"), []).append(m)
        for cat in sorted(by_cat):
            lines.append(f"## {cat}")
            lines.append("")
            for m in by_cat[cat]:
                title = getattr(m, "title", "") or ""
                content = getattr(m, "content", "").strip().replace("\n", "\n  ")
                tags = getattr(m, "tags", []) or []
                tagstr = " ".join(f"#{t}" for t in tags)
                head = f"- **{title}** — " if title else "- "
                lines.append(f"{head}{content}" + (f"  {tagstr}" if tagstr else ""))
            lines.append("")
        return self.write_note(
            rel,
            "\n".join(lines),
            frontmatter={
                "created": datetime.now().isoformat(timespec="seconds"),
                "source": f"{display_name().lower()}-memory",
                "count": len(rows),
                "tags": ["milo", "memory"],
            },
        )

    # -- search ----------------------------------------------------------------

    def files(self) -> Iterable[Path]:
        if not self.exists:
            return []
        out: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                if Path(fn).suffix.lower() in _TEXT_SUFFIXES:
                    out.append(Path(dirpath) / fn)
        return out

    def search(self, query: str, limit: int = 25, ignore_case: bool = True) -> List[VaultHit]:
        """Grep the vault. Plain substring unless the query looks like a regex."""
        if not query.strip():
            return []
        flags = re.IGNORECASE if ignore_case else 0
        try:
            pat = re.compile(query, flags) if re.search(r"[\\|\[\](){}^$*+?]", query) \
                else re.compile(re.escape(query), flags)
        except re.error:
            pat = re.compile(re.escape(query), flags)

        hits: List[VaultHit] = []
        for f in self.files():
            try:
                with f.open("r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if pat.search(line):
                            hits.append(
                                VaultHit(f, i, line, rel=str(f.relative_to(self.root)))
                            )
                            if len(hits) >= limit:
                                return hits
            except OSError:
                continue
        return hits

    # -- git -------------------------------------------------------------------

    def _git(self, *args: str, check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=check,
            timeout=180,
        )

    def dirty(self) -> bool:
        if not self.is_git:
            return False
        return bool(self._git("status", "--porcelain").stdout.strip())

    def pull(self) -> Tuple[bool, str]:
        if not self.is_git:
            return False, "vault is not a git repo"
        r = self._git("pull", "--rebase", "--autostash")
        return r.returncode == 0, (r.stdout + r.stderr).strip()

    def commit_and_push(self, message: str = "", push: bool = True) -> Tuple[bool, str]:
        if not self.is_git:
            return False, "vault is not a git repo"
        if not self.dirty():
            return True, "vault clean, nothing to commit"
        message = message or (
            f"{display_name()}: vault sync {datetime.now():%Y-%m-%d %H:%M}"
        )
        self._git("add", "-A")
        r = self._git("commit", "-m", message)
        out = (r.stdout + r.stderr).strip()
        if push:
            p = self._git("push")
            out += "\n" + (p.stdout + p.stderr).strip()
            return p.returncode == 0, out.strip()
        return True, out

    def sync(self, message: str = "") -> Tuple[bool, str]:
        """Pull then commit-and-push. The one call ``milo backup`` makes."""
        if not self.exists:
            return False, f"vault missing at {self.root}"
        if not self.is_git:
            return False, "vault is not a git repo (nothing to sync)"
        logs: List[str] = []
        ok_pull, out = self.pull()
        logs.append(out)
        ok_push, out2 = self.commit_and_push(message)
        logs.append(out2)
        return (ok_pull and ok_push), "\n".join(x for x in logs if x)

    # -- diagnostics -----------------------------------------------------------

    def stats(self) -> Dict[str, object]:
        if not self.exists:
            return {"path": str(self.root), "exists": False}
        files = list(self.files())
        total = 0
        for f in files:
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return {
            "path": str(self.root),
            "exists": True,
            "git": self.is_git,
            "dirty": self.dirty(),
            "notes": len(files),
            "size_mb": round(total / 1_048_576, 2),
            "daily_today": self.daily_note_path().exists(),
            "boot_file": (self.root / self.layout.boot_file).exists(),
        }


_VAULT: Optional[Vault] = None


def vault() -> Vault:
    """Process-wide vault handle."""
    global _VAULT
    if _VAULT is None:
        _VAULT = Vault()
    return _VAULT
