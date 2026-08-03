"""
milo.memory.vault — the cold tier (Obsidian / dra-brains).

The vault is Milo's long-term, human-readable memory. Unlike the hot tier it
is a git repo full of markdown, editable by a human in Obsidian, and it is
where knowledge goes to *stay*.

Responsibilities here:

* locate the vault wherever it lives on this machine (see :mod:`milo.paths`)
* read the boot files (identity, handoff, priorities, today's daily note)
* append to today's daily note
* **promote** hot observations into durable vault notes at task boundaries

Everything is path-relative to ``paths.vault_dir``. There is not a single
hardcoded ``C:/Users/...`` in this module, which is the whole point.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from ..paths import MiloPaths, get_paths
from .store import Brain, Observation

__all__ = ["Vault", "VaultLayout"]


@dataclass(frozen=True)
class VaultLayout:
    """Folder names inside the vault. Overridable for a differently-shaped vault."""

    inbox: str = "00 - Inbox"
    daily: str = "01 - Daily Notes"
    resources: str = "11 - Resources"
    milo_notes: str = "09 - Personal/Milo"

    boot_file: str = "CLAUDE.md"          # canonical identity / operating manual
    index_file: str = "VAULT-INDEX.md"
    handoff_file: str = "Session Handoff.md"
    priorities_file: str = "Active Priorities.md"
    daily_template: str = "01 - Daily Notes/Daily Note Template.md"


class Vault:
    """Read/write access to the dra-brains vault."""

    def __init__(
        self,
        paths: Optional[MiloPaths] = None,
        layout: Optional[VaultLayout] = None,
    ):
        self.paths = paths or get_paths()
        self.layout = layout or VaultLayout()

    # -- basics -----------------------------------------------------------

    @property
    def root(self) -> Path:
        return self.paths.vault_dir

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    @property
    def is_git_repo(self) -> bool:
        return (self.root / ".git").exists()

    def path(self, *parts: str) -> Path:
        """Safe join — refuses to escape the vault root."""
        target = self.root.joinpath(*parts)
        try:
            target.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise ValueError(f"path escapes the vault: {'/'.join(parts)}") from exc
        return target

    def read(self, relative: str) -> Optional[str]:
        target = self.path(relative)
        if not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def write(self, relative: str, content: str) -> Path:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def append(self, relative: str, content: str) -> Path:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        target.write_text(existing + content, encoding="utf-8")
        return target

    def list_notes(self, subfolder: str = "", pattern: str = "*.md") -> List[Path]:
        base = self.path(subfolder) if subfolder else self.root
        if not base.is_dir():
            return []
        return sorted(p for p in base.rglob(pattern) if ".git" not in p.parts)

    # -- boot files -------------------------------------------------------

    def boot_context(self, max_chars: int = 8000) -> Dict[str, str]:
        """The tiered-boot payload: handoff, priorities, today's note index."""
        out: Dict[str, str] = {}
        for key, relative in (
            ("handoff", self.layout.handoff_file),
            ("priorities", self.layout.priorities_file),
        ):
            text = self.read(relative)
            if text:
                out[key] = text[:max_chars]
        today = self.daily_note_path(create=False)
        if today.is_file():
            try:
                head = "\n".join(today.read_text(encoding="utf-8").splitlines()[:15])
                out["today"] = head
            except (OSError, UnicodeDecodeError):
                pass
        return out

    # -- daily notes ------------------------------------------------------

    def daily_note_path(self, day: Optional[date] = None, create: bool = False) -> Path:
        day = day or date.today()
        target = self.path(self.layout.daily, f"{day.isoformat()}.md")
        if create and not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            template = self.read(self.layout.daily_template)
            if template:
                body = template.replace("{{date}}", day.isoformat())
            else:
                body = f"# {day.isoformat()}\n\n## Index\n\n## Log\n"
            target.write_text(body, encoding="utf-8")
        return target

    def log_to_daily(self, text: str, heading: Optional[str] = None) -> Path:
        """Append a timestamped line to today's note (append-only by design)."""
        target = self.daily_note_path(create=True)
        stamp = datetime.now().strftime("%H:%M")
        block = ""
        if heading:
            block += f"\n### {heading}\n"
        block += f"\n- `{stamp}` {text.strip()}\n"
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        target.write_text(existing + block, encoding="utf-8")
        return target

    # -- promotion (hot -> cold) -----------------------------------------

    def promote(
        self,
        observations: Sequence[Observation],
        *,
        note_name: Optional[str] = None,
        subfolder: Optional[str] = None,
        brain: Optional[Brain] = None,
    ) -> Optional[Path]:
        """Write observations into a durable vault note.

        Called at task boundaries. Low-signal observations stay in the hot
        tier and die there; only what a future session genuinely needs is
        promoted, which is how the vault avoids turning into a landfill.
        """
        observations = [o for o in observations if o and o.title]
        if not observations:
            return None

        folder = subfolder if subfolder is not None else self.layout.milo_notes
        stamp = date.today().isoformat()
        name = note_name or f"Learned {stamp}"
        relative = f"{folder}/{_safe_filename(name)}.md"
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.is_file():
            body = target.read_text(encoding="utf-8")
        else:
            body = (
                f"# {name}\n\n"
                f"*Promoted from Milo's hot memory. Machine-generated; edit freely.*\n\n"
            )

        added = 0
        for obs in observations:
            marker = f"### {obs.title}"
            if marker in body:
                continue
            body += "\n" + obs.as_markdown()
            added += 1
        if added == 0:
            return target

        target.write_text(body, encoding="utf-8")

        if brain is not None:
            for obs in observations:
                brain.mark_promoted(obs.id, relative)
        return target

    def auto_promote(
        self,
        brain: Brain,
        *,
        min_importance: int = 3,
        limit: int = 50,
        since_hours: int = 24,
    ) -> Optional[Path]:
        """Promote recent, un-promoted, important observations."""
        cutoff = int(time.time()) - since_hours * 3600
        candidates = [
            obs
            for obs in brain.recent(limit=limit * 4, min_importance=min_importance)
            if obs.promoted_to is None and obs.updated_at >= cutoff
        ][:limit]
        if not candidates:
            return None
        return self.promote(candidates, brain=brain)

    # -- git --------------------------------------------------------------

    def git(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def dirty(self) -> bool:
        if not self.is_git_repo:
            return False
        result = self.git("status", "--porcelain")
        return bool(result.stdout.strip())

    def commit_and_push(self, message: str = "", push: bool = True) -> Dict[str, object]:
        """Commit any vault changes. Returns a small report dict."""
        if not self.is_git_repo:
            return {"ok": False, "reason": "vault is not a git repo"}
        if not self.dirty():
            return {"ok": True, "changed": False, "reason": "clean"}

        message = message or f"vault: sync {datetime.now():%Y-%m-%d %H:%M}"
        self.git("add", "-A")
        commit = self.git("commit", "-m", message)
        if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
            return {"ok": False, "changed": False, "reason": commit.stderr.strip()}
        if not push:
            return {"ok": True, "changed": True, "pushed": False}
        pushed = self.git("push")
        return {
            "ok": pushed.returncode == 0,
            "changed": True,
            "pushed": pushed.returncode == 0,
            "reason": pushed.stderr.strip() if pushed.returncode else "",
        }

    def pull(self) -> Dict[str, object]:
        if not self.is_git_repo:
            return {"ok": False, "reason": "vault is not a git repo"}
        result = self.git("pull", "--rebase", "--autostash")
        return {
            "ok": result.returncode == 0,
            "output": (result.stdout + result.stderr).strip()[-500:],
        }


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str) -> str:
    """Make a string safe as a filename on Windows *and* POSIX."""
    cleaned = _UNSAFE.sub("-", name).strip(" .")
    return (cleaned or "note")[:120]
