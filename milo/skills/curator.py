"""
milo.skills.curator — keeps the self-written skill collection healthy.

Adapted from Hermes' background curator, minus the LLM orchestration
machinery. The deterministic half — which is the half that matters — runs
with no model calls at all.

Why this exists
---------------
An agent that can write its own skills will, given a few months, write
forty of them. Half will be one-off experiments that never fire again. The
skill *index* is paid for on every single turn, so dead skills are a
permanent tax on every conversation.

The curator applies the same rule Milo's operating manual applies to the
vault — *no bloat, consolidate, don't accrete* — automatically:

============  ==============================================================
active        used recently, or newly created
stale         no recorded use for ``stale_after_days``  (dropped from index)
archived      no recorded use for ``archive_after_days`` (hidden entirely)
============  ==============================================================

Hard invariants, inherited from Hermes because they are correct:

* Only **agent-created** skills are ever touched. Skills you wrote by hand,
  and skills bundled with the repo, are never auto-modified.
* **Nothing is ever auto-deleted.** Archive is recoverable; deletion is not.
* **Pinned skills bypass every transition.** Pinning is how you say
  "this one is permanent, stop thinking about it".
* Runs on inactivity, not a cron daemon — there is no extra process to
  install, monitor, or forget to migrate.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..memory.store import Brain
from ..paths import MiloPaths, get_paths
from .manager import Skill, SkillManager

__all__ = ["Curator", "CuratorReport", "CuratorConfig"]

DAY = 86400


@dataclass
class CuratorConfig:
    enabled: bool = True
    interval_hours: int = 168  # weekly
    min_idle_hours: int = 2
    stale_after_days: int = 30
    archive_after_days: int = 90
    #: Suggest merging skills whose tag sets overlap this much (0..1).
    consolidate: bool = False
    consolidate_similarity: float = 0.6

    @classmethod
    def from_settings(cls, settings: Any) -> "CuratorConfig":
        get = getattr(settings, "get", None)
        if get is None:
            return cls()
        return cls(
            enabled=bool(get("skills.curator.enabled", True)),
            interval_hours=int(get("skills.curator.interval_hours", 168)),
            stale_after_days=int(get("skills.curator.stale_after_days", 30)),
            archive_after_days=int(get("skills.curator.archive_after_days", 90)),
            consolidate=bool(get("skills.curator.consolidate", False)),
        )


@dataclass
class CuratorReport:
    ran: bool = False
    reason: str = ""
    reviewed: int = 0
    to_stale: List[str] = field(default_factory=list)
    to_active: List[str] = field(default_factory=list)
    to_archived: List[str] = field(default_factory=list)
    merge_candidates: List[Sequence[str]] = field(default_factory=list)
    unused_forever: List[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.to_stale) + len(self.to_active) + len(self.to_archived)

    def as_text(self) -> str:
        if not self.ran:
            return f"curator: skipped ({self.reason})"
        lines = [f"curator: reviewed {self.reviewed} agent-created skill(s)"]
        if self.to_active:
            lines.append(f"  reactivated : {', '.join(self.to_active)}")
        if self.to_stale:
            lines.append(f"  -> stale    : {', '.join(self.to_stale)}")
        if self.to_archived:
            lines.append(f"  -> archived : {', '.join(self.to_archived)}")
        if self.merge_candidates:
            for group in self.merge_candidates:
                lines.append(f"  merge?      : {' + '.join(group)}")
        if self.changed == 0 and not self.merge_candidates:
            lines.append("  nothing to do — collection is healthy")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "reviewed": self.reviewed,
            "to_stale": self.to_stale,
            "to_active": self.to_active,
            "to_archived": self.to_archived,
            "merge_candidates": [list(g) for g in self.merge_candidates],
            "unused_forever": self.unused_forever,
        }


class Curator:
    """Deterministic skill lifecycle maintenance."""

    def __init__(
        self,
        manager: Optional[SkillManager] = None,
        brain: Optional[Brain] = None,
        config: Optional[CuratorConfig] = None,
        paths: Optional[MiloPaths] = None,
    ):
        self.paths = paths or get_paths()
        self.manager = manager or SkillManager(self.paths)
        self.brain = brain
        self.config = config or CuratorConfig()

    # -- state ------------------------------------------------------------

    @property
    def state_file(self) -> Path:
        return self.paths.state_dir / "curator.json"

    def _state(self) -> Dict[str, Any]:
        if not self.state_file.is_file():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self, **updates: Any) -> None:
        state = self._state()
        state.update(updates)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def due(self) -> bool:
        """True if enough time has passed since the last run."""
        if not self.config.enabled:
            return False
        last = int(self._state().get("last_run_at") or 0)
        return (time.time() - last) >= self.config.interval_hours * 3600

    # -- the run ----------------------------------------------------------

    def maybe_run(self, force: bool = False) -> CuratorReport:
        if not self.config.enabled and not force:
            return CuratorReport(ran=False, reason="curator disabled in milo.json")
        if not force and not self.due():
            last = int(self._state().get("last_run_at") or 0)
            hours = (time.time() - last) / 3600
            return CuratorReport(
                ran=False,
                reason=f"last run {hours:.0f}h ago; interval is "
                f"{self.config.interval_hours}h",
            )
        return self.run()

    def run(self, dry_run: bool = False) -> CuratorReport:
        report = CuratorReport(ran=True)
        usage = self.brain.skill_stats() if self.brain else {}
        now = time.time()
        stale_cutoff = now - self.config.stale_after_days * DAY
        archive_cutoff = now - self.config.archive_after_days * DAY

        skills = [
            s
            for s in self.manager.discover(include_archived=True)
            if s.is_agent_created
        ]
        report.reviewed = len(skills)

        for skill in skills:
            if skill.pinned:
                continue
            last_used = self._last_activity(skill, usage)
            target = skill.lifecycle

            if last_used >= stale_cutoff:
                target = "active"
            elif last_used < archive_cutoff:
                target = "archived"
            else:
                target = "stale"

            if target == skill.lifecycle:
                continue
            if not dry_run:
                self.manager.set_lifecycle(skill.name, target)
            {"active": report.to_active, "stale": report.to_stale,
             "archived": report.to_archived}[target].append(skill.name)

            if target == "archived" and usage.get(skill.name, {}).get("uses", 0) == 0:
                report.unused_forever.append(skill.name)

        if self.config.consolidate:
            report.merge_candidates = self._merge_candidates(
                [s for s in skills if s.lifecycle != "archived"]
            )

        if not dry_run:
            self._save_state(
                last_run_at=int(now),
                last_report=report.to_dict(),
            )
        return report

    # -- helpers ----------------------------------------------------------

    def _last_activity(self, skill: Skill, usage: Dict[str, Dict[str, Any]]) -> float:
        """Most recent evidence this skill is alive: recorded use, else mtime."""
        recorded = usage.get(skill.name, {}).get("last_used")
        if recorded:
            return float(recorded)
        state = self.manager._read_state(skill.path)  # noqa: SLF001 - same package
        for key in ("used_at", "updated_at", "created_at"):
            if state.get(key):
                return float(state[key])
        try:
            return skill.skill_file.stat().st_mtime
        except OSError:
            return 0.0

    def _merge_candidates(self, skills: Sequence[Skill]) -> List[Sequence[str]]:
        """Flag pairs whose tags overlap enough to suggest one skill, not two.

        Deliberately advisory: the curator reports the pair and lets a human
        (or an explicit ``milo skills merge``) decide. Auto-merging prose is
        how you silently lose the one paragraph that mattered.
        """
        groups: List[Sequence[str]] = []
        for i, a in enumerate(skills):
            tags_a = set(a.tags)
            if not tags_a:
                continue
            for b in skills[i + 1 :]:
                tags_b = set(b.tags)
                if not tags_b:
                    continue
                overlap = len(tags_a & tags_b) / len(tags_a | tags_b)
                if overlap >= self.config.consolidate_similarity:
                    groups.append((a.name, b.name))
        return groups

    # -- nudges -----------------------------------------------------------

    @staticmethod
    def creation_nudge() -> str:
        """Injected after N tool iterations with no skill activity."""
        return (
            "[milo] You've done a lot of steps without capturing anything reusable. "
            "If this task involved a non-obvious procedure you'd have to rediscover "
            "next time — a working sequence of commands, a trap you fell into, a "
            "tool that needed coaxing — write it down now with `skill_manage` "
            "(action='create'). If nothing here is reusable, ignore this and carry on."
        )

    @staticmethod
    def memory_nudge() -> str:
        """Injected every N assistant turns."""
        return (
            "[milo] Checkpoint: anything worth a future session knowing? Decisions "
            "made, things that broke and why, preferences Allan expressed. Save it "
            "with `mem_save` now while the context is fresh — reconstructing it later "
            "costs far more than the ten seconds it costs now."
        )
