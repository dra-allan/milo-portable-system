"""
learning.py — the closed loop that makes Milo get better.
=========================================================

Three cooperating pieces, all ported in spirit from Hermes Agent:

``build_learn_prompt``
    ``milo learn "<thing>"`` produces one prompt that tells the *live* agent
    to gather the sources and author a ``SKILL.md`` itself. No separate
    distillation engine, no extra model calls, works identically on OpenCode,
    Claude Code, Codex or anything else — because the agent uses the tools it
    already has.

``Curator``
    Inactivity-triggered housekeeping over agent-authored skills. Deterministic
    lifecycle transitions (active → stale → archived) based on real usage
    timestamps, plus an optional consolidation prompt. **Never deletes.**
    Pinned skills bypass everything.

``NudgeEngine``
    Watches the session for things worth persisting and emits reminders
    ("you made 3 decisions this session and saved none of them"). This is what
    stops the assistant from being brilliant for an hour and amnesiac tomorrow.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import paths
from .memory import MemoryStore, store as memory_store
from .naming import display_name
from .skills import Skill, SkillRegistry, registry as skill_registry

# ── /learn — the skill-authoring prompt ───────────────────────────────────────

AUTHORING_STANDARDS = """\
Follow these skill-authoring standards exactly. They are not cosmetic.

Frontmatter:
- name: lowercase-hyphenated, <=64 chars, no spaces.
- description: ONE sentence, **<=60 characters**, ends with a period. State the
  capability, not the implementation. No marketing words (powerful,
  comprehensive, seamless, advanced, robust). Do NOT repeat the skill name.
  The system-prompt skill index truncates at 60 characters and is loaded every
  session — anything past char 60 is silently cut and the skill never routes.
  Count the characters after writing it.
    Good (44): `Search arXiv papers by keyword or author.`
    Bad (118): `A comprehensive skill that lets the agent search arXiv for
                academic papers using keywords, authors, and categories.`
- version: 0.1.0
- author: the literal value `Milo`. Never fill this from the host environment,
  OS login name, or git config — skills get shared, and that is a privacy leak.
- platforms: declare [windows], [linux], [macos], [termux] ONLY if the skill
  uses OS-bound primitives (PowerShell/NSSM => windows, systemctl/proc => linux,
  osascript => macos, pkg/termux-api => termux). Prefer making it portable
  first; omit the field for portable skills.
- tags: a few lowercase, relevant tags.

Body section order (omit a section only if it genuinely has no content):
1. `# <Human Title>` then 2-3 sentences: what it does, what it does NOT do,
   and the dependency stance (e.g. "stdlib only").
2. `## When to Use` — bullet list of concrete trigger phrases.
3. `## Prerequisites` — exact env vars, install steps, credentials.
4. `## How to Run` — the canonical invocation.
5. `## Quick Reference` — flat command/endpoint list, no narration.
6. `## Procedure` — numbered steps with copy-paste-exact commands.
7. `## Pitfalls` — known limits, rate limits, things that look broken but aren't.
8. `## Verification` — one command/check that proves the skill worked.

Quality bar:
- Use exact commands, URLs, signatures and config keys that appear VERBATIM in
  the source. Never invent flags, paths or APIs. If you did not see it, do not
  write it.
- Tight and scannable: ~100 lines for a simple skill, ~200 for a complex one.
- Do not write a router/index skill that only points at other skills.
- Larger scripts belong in `scripts/`, references in `references/`, templates
  in `templates/` — referenced from SKILL.md by relative path, not inlined.
- Paths must be portable. Never hardcode a home directory; read `MILO_HOME`,
  `MILO_VAULT_DIR` or use `milo where <key>`."""


def build_learn_prompt(request: str, *, existing: Optional[Sequence[str]] = None) -> str:
    """Build the turn Milo runs to author a skill from anything described.

    ``request`` can be a directory, a URL, "what we just did", or pasted notes.
    """
    req = (request or "").strip() or (
        "the workflow we just went through in this conversation — review the "
        "steps taken and distil them into a reusable skill"
    )
    known = ""
    if existing:
        known = (
            "\nSkills that already exist (extend one instead of duplicating it "
            "if it overlaps):\n" + "\n".join(f"  - {n}" for n in existing) + "\n"
        )
    return f"""[/learn] Learn a reusable skill from the request below and save it.

REQUEST
-------
{req}
{known}
STEP 1 — GATHER
Collect the sources named in the request using the tools you already have:
  - a directory or file  -> `read_file` / `search_files`
  - a URL                -> `web_extract` (or `terminal` + curl if unavailable)
  - "what I just did"    -> re-read this conversation's tool calls and outputs
  - pasted notes         -> the request text itself
Read enough to write exact commands. Do not guess.

STEP 2 — DECIDE
If an existing skill already covers this, improve that skill instead of
creating a near-duplicate. Say which one you chose and why, in one line.

STEP 3 — AUTHOR
Write a single SKILL.md.

{AUTHORING_STANDARDS}

STEP 4 — SAVE
Save it by writing the file to `$MILO_HOME/skills/<name>/SKILL.md`
(resolve $MILO_HOME with `milo where MILO_HOME`), then run:
    milo skill lint <name>
Fix every error it reports. Warnings are judgement calls; fix them unless you
can justify keeping them.

STEP 5 — REPORT
Reply with: the skill name, its description, the file path, and one sentence
on when it will trigger."""


def build_improve_prompt(skill: Skill, note: str = "") -> str:
    """Prompt for improving a skill *in place* right after using it."""
    return f"""[/improve] You just used the `{skill.name}` skill. Improve it now,
while the friction is fresh.

Current description: {skill.description}
File: {skill.skill_file}

{('Observed problem: ' + note) if note else 'Consider what slowed you down.'}

Do this:
1. `read_file` the SKILL.md.
2. Identify what was wrong, missing, or misleading — a wrong flag, a missing
   prerequisite, an undocumented failure mode, a step that needed improvising.
3. Patch ONLY those parts. Do not rewrite the whole file, do not pad it.
4. Add anything you had to discover the hard way to `## Pitfalls`.
5. Keep the description <=60 chars.
6. Run `milo skill lint {skill.name}` and fix errors.

If nothing genuinely needs changing, say "no change needed" and stop. Do not
invent edits to look productive."""


# ── Curator ───────────────────────────────────────────────────────────────────

DEFAULT_INTERVAL_HOURS = 24 * 7
DEFAULT_MIN_IDLE_HOURS = 2
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90


@dataclass
class CuratorState:
    last_run_at: Optional[float] = None
    last_run_summary: str = ""
    run_count: int = 0
    paused: bool = False

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CuratorState":
        p = Path(path or paths.curator_state_file())
        if not p.is_file():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items()
                          if k in cls.__dataclass_fields__})  # type: ignore[attr-defined]
        except (OSError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self, path: Optional[Path] = None) -> None:
        p = Path(path or paths.curator_state_file())
        paths.ensure(p.parent)
        p.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


class Curator:
    """Keeps the skill collection healthy without ever losing anything.

    Invariants (same as Hermes):
      * only touches non-bundled skills
      * never deletes — archive only, and archive is reversible
      * pinned skills bypass every automatic transition
    """

    def __init__(self, registry: Optional[SkillRegistry] = None,
                 *, stale_days: int = DEFAULT_STALE_AFTER_DAYS,
                 archive_days: int = DEFAULT_ARCHIVE_AFTER_DAYS):
        self.reg = registry or skill_registry()
        self.stale_days = stale_days
        self.archive_days = archive_days
        self.state = CuratorState.load()

    # -- deterministic pass ----------------------------------------------------

    def apply_transitions(self, dry_run: bool = False) -> Dict[str, List[str]]:
        """active → stale → archived based on real usage timestamps."""
        usage = self.reg.usage()
        now = time.time()
        result: Dict[str, List[str]] = {"stale": [], "archived": [], "revived": []}

        for skill in self.reg.all(include_archived=True):
            if skill.origin == "bundled" or skill.pinned:
                continue
            entry = usage.get(skill.name, {})
            last = float(entry.get("last_used") or 0) or self._mtime(skill)
            age_days = (now - last) / 86400.0

            if skill.lifecycle == "active" and age_days > self.stale_days:
                result["stale"].append(skill.name)
                if not dry_run:
                    self.reg.set_meta(skill.name, lifecycle="stale")
            elif skill.lifecycle == "stale" and age_days > self.archive_days:
                result["archived"].append(skill.name)
                if not dry_run:
                    self.reg.set_meta(skill.name, lifecycle="archived")
            elif skill.lifecycle in ("stale", "archived") and age_days <= self.stale_days:
                result["revived"].append(skill.name)
                if not dry_run:
                    self.reg.set_meta(skill.name, lifecycle="active")
        return result

    @staticmethod
    def _mtime(skill: Skill) -> float:
        try:
            return skill.skill_file.stat().st_mtime
        except OSError:
            return time.time()

    # -- scheduling ------------------------------------------------------------

    def due(self, interval_hours: int = DEFAULT_INTERVAL_HOURS) -> bool:
        if self.state.paused:
            return False
        if not self.state.last_run_at:
            return True
        return (time.time() - self.state.last_run_at) > interval_hours * 3600

    def run(self, dry_run: bool = False) -> Dict[str, Any]:
        started = time.time()
        transitions = self.apply_transitions(dry_run=dry_run)
        problems = self.reg.lint()
        duplicates = self.find_duplicates()
        summary = (
            f"{len(transitions['stale'])} stale, "
            f"{len(transitions['archived'])} archived, "
            f"{len(transitions['revived'])} revived, "
            f"{len([p for p in problems if p[1] == 'error'])} lint errors, "
            f"{len(duplicates)} possible duplicates"
        )
        if not dry_run:
            self.state.last_run_at = started
            self.state.last_run_summary = summary
            self.state.run_count += 1
            self.state.save()
        return {
            "summary": summary,
            "transitions": transitions,
            "lint": problems,
            "duplicates": duplicates,
            "duration_s": round(time.time() - started, 2),
        }

    # -- consolidation candidates ----------------------------------------------

    def find_duplicates(self, threshold: float = 0.6) -> List[Dict[str, Any]]:
        """Skill pairs that likely want merging, by tag+token overlap."""
        skills = [s for s in self.reg.all() if s.origin != "bundled"]
        out: List[Dict[str, Any]] = []
        for i, a in enumerate(skills):
            for b in skills[i + 1:]:
                sim = _similarity(a, b)
                if sim >= threshold:
                    out.append({"a": a.name, "b": b.name, "similarity": round(sim, 2)})
        out.sort(key=lambda d: -d["similarity"])
        return out

    def consolidation_prompt(self, pairs: Sequence[Dict[str, Any]]) -> str:
        if not pairs:
            return ""
        listing = "\n".join(
            f"  - `{p['a']}` and `{p['b']}` (overlap {p['similarity']})" for p in pairs
        )
        return f"""[curator] These skills look like they overlap:

{listing}

For each pair, decide ONE of:
  (a) genuinely distinct — leave both alone, say why in one line;
  (b) one supersedes the other — merge the useful parts into the better skill,
      then `milo skill archive <loser>`;
  (c) both are thin — merge into a single well-named skill and archive both
      originals.

Rules: never delete, only archive. Keep descriptions <=60 chars. Run
`milo skill lint` afterwards and fix every error."""


def _similarity(a: Skill, b: Skill) -> float:
    def toks(s: Skill) -> set:
        text = f"{s.name} {s.description} {' '.join(s.tags)}".lower()
        return {t for t in text.replace("-", " ").split() if len(t) > 2}
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── Nudges ────────────────────────────────────────────────────────────────────

@dataclass
class Nudge:
    kind: str
    message: str
    action: str = ""
    priority: int = 2  # 1 = quiet, 3 = say it now

    def render(self) -> str:
        prefix = {1: "note", 2: "nudge", 3: "important"}.get(self.priority, "nudge")
        line = f"[{prefix}] {self.message}"
        return f"{line}\n  → {self.action}" if self.action else line


class NudgeEngine:
    """Decides when to remind Milo to persist knowledge or write a skill.

    This is deliberately rule-based and cheap: it runs every turn, costs no
    tokens, and only speaks when it has something concrete to say.
    """

    def __init__(self, mem: Optional[MemoryStore] = None,
                 registry: Optional[SkillRegistry] = None):
        self.mem = mem or memory_store()
        self.reg = registry or skill_registry()

    # -- individual checks -----------------------------------------------------

    def check_unsaved_decisions(self, turn_count: int,
                                saves_this_session: int) -> Optional[Nudge]:
        if turn_count >= 12 and saves_this_session == 0:
            return Nudge(
                "persist",
                f"{turn_count} turns and nothing saved to memory this session.",
                "Save the durable outcomes: `milo remember \"...\" --category decision`",
                priority=3,
            )
        return None

    def check_skill_worthy(self, tool_calls: int, distinct_tools: int,
                           had_error: bool) -> Optional[Nudge]:
        """A long, multi-tool, error-recovering task is a skill waiting to
        be written — that is exactly the knowledge that evaporates."""
        if tool_calls >= 8 and distinct_tools >= 3:
            reason = "multi-step task with recovery" if had_error else "multi-step task"
            return Nudge(
                "learn",
                f"That was a {reason} ({tool_calls} tool calls). Worth keeping.",
                "`milo learn \"what we just did\"`",
                priority=2,
            )
        return None

    def check_stale_memory(self, days: int = 5) -> Optional[Nudge]:
        stats = self.mem.stats()
        newest = stats.get("newest") or 0
        if newest and (time.time() - newest) > days * 86400:
            return Nudge(
                "stale",
                f"No new memories in {int((time.time() - newest) / 86400)} days.",
                "Anything learned recently that should outlive this session?",
                priority=1,
            )
        return None

    def check_curator_due(self) -> Optional[Nudge]:
        curator = Curator(self.reg)
        if curator.due():
            return Nudge(
                "curator",
                "Skill curator hasn't run in over a week.",
                "`milo skill curate`",
                priority=1,
            )
        return None

    def check_backup_due(self, days: int = 3) -> Optional[Nudge]:
        marker = paths.state_dir() / "last_backup"
        try:
            last = float(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            last = 0.0
        if time.time() - last > days * 86400:
            ago = "never" if not last else f"{int((time.time() - last) / 86400)}d ago"
            return Nudge(
                "backup",
                f"Last backup: {ago}. Memory only survives what you push.",
                "`milo backup`",
                priority=3 if not last else 2,
            )
        return None

    # -- aggregate -------------------------------------------------------------

    def collect(self, *, turn_count: int = 0, saves_this_session: int = 0,
                tool_calls: int = 0, distinct_tools: int = 0,
                had_error: bool = False, limit: int = 2) -> List[Nudge]:
        candidates = [
            self.check_unsaved_decisions(turn_count, saves_this_session),
            self.check_skill_worthy(tool_calls, distinct_tools, had_error),
            self.check_backup_due(),
            self.check_curator_due(),
            self.check_stale_memory(),
        ]
        found = [n for n in candidates if n]
        found.sort(key=lambda n: -n.priority)
        return found[:limit]

    def render(self, **kwargs: Any) -> str:
        nudges = self.collect(**kwargs)
        if not nudges:
            return ""
        who = display_name()
        return f"\n--- {who}: housekeeping ---\n" + "\n".join(
            n.render() for n in nudges
        )
