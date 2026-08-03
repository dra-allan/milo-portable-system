"""
milo.skills.manager — the skill system (agentskills.io / Hermes compatible).

A *skill* is a folder containing a ``SKILL.md`` with YAML frontmatter:

    skills/
      software-development/
        systematic-debugging/
          SKILL.md          <- always loaded when the skill is invoked
          references/       <- bulky material, loaded only when needed
          scripts/          <- executable helpers

This is deliberately the same on-disk shape Hermes, Claude Code and the
agentskills.io standard use, so a skill written for Milo drops straight into
any of them — and vice versa. That is the whole reason for choosing this
format over inventing our own.

Progressive disclosure is the point: only each skill's *description* is paid
for on every turn (it goes into the system-prompt index). The body is read
when the agent actually decides to use the skill.

What makes Milo's version different from a static skill folder is
:mod:`milo.skills.curator`: skills Milo writes for itself are tracked,
aged, and consolidated automatically. That is the growth loop.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..paths import MiloPaths, get_paths

__all__ = [
    "Skill",
    "SkillManager",
    "SkillError",
    "MAX_NAME_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_SKILL_CHARS",
]

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CHARS = 100_000
#: How much of a description is shown in the always-loaded system-prompt index.
INDEX_DESCRIPTION_CHARS = 160

VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

LIFECYCLE = ("active", "stale", "archived")


class SkillError(RuntimeError):
    """Raised when a skill is malformed or an operation is not allowed."""


# ---------------------------------------------------------------------------
# Minimal YAML frontmatter parser
# ---------------------------------------------------------------------------
#
# We support the subset every real SKILL.md uses: scalars, inline lists
# (``[a, b]``), block lists, and one level of nesting. Bringing in PyYAML for
# this would add a dependency to a tool whose main selling point is that it
# runs anywhere with nothing but Python.


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split ``---`` frontmatter from the body. Returns ``(meta, body)``."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta = _parse_block(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def _parse_block(lines: Sequence[str], indent: int = 0) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.strip().startswith("#"):
            i += 1
            continue
        current_indent = len(raw) - len(raw.lstrip(" "))
        if current_indent < indent:
            break
        if current_indent > indent:
            i += 1
            continue
        line = raw.strip()
        if line.startswith("- "):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if value:
            result[key] = _parse_scalar(value)
            i += 1
            continue

        # Nested block or block list.
        block: List[str] = []
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.strip() and (len(nxt) - len(nxt.lstrip(" "))) <= indent:
                break
            block.append(nxt)
            j += 1
        stripped = [b for b in block if b.strip()]
        if stripped and stripped[0].strip().startswith("- "):
            result[key] = [_parse_scalar(b.strip()[2:]) for b in stripped]
        elif stripped:
            inner_indent = len(stripped[0]) - len(stripped[0].lstrip(" "))
            result[key] = _parse_block(block, inner_indent)
        else:
            result[key] = None
        i = j
    return result


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_inline(inner)]
    lowered = value.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", "none"):
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _split_inline(text: str) -> List[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        current += char
    if current.strip():
        parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def dump_frontmatter(meta: Mapping[str, Any]) -> str:
    """Serialise metadata back to YAML frontmatter (round-trips our own output)."""
    lines = ["---"]
    for key, value in meta.items():
        lines.extend(_dump_pair(key, value, 0))
    lines.append("---")
    return "\n".join(lines)


def _dump_pair(key: str, value: Any, indent: int) -> List[str]:
    pad = " " * indent
    if isinstance(value, Mapping):
        out = [f"{pad}{key}:"]
        for k, v in value.items():
            out.extend(_dump_pair(k, v, indent + 2))
        return out
    if isinstance(value, (list, tuple)):
        rendered = ", ".join(_dump_scalar(v) for v in value)
        return [f"{pad}{key}: [{rendered}]"]
    return [f"{pad}{key}: {_dump_scalar(value)}"]


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if any(ch in text for ch in ":#[]{}\n") or text != text.strip():
        return json.dumps(text)
    return text


# ---------------------------------------------------------------------------
# Skill record
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    category: str = ""
    source: str = "bundled"  # bundled | user | agent
    lifecycle: str = "active"
    pinned: bool = False

    @property
    def skill_file(self) -> Path:
        return self.path / "SKILL.md"

    @property
    def tags(self) -> List[str]:
        milo_meta = (self.meta.get("metadata") or {}).get("milo") or {}
        hermes_meta = (self.meta.get("metadata") or {}).get("hermes") or {}
        tags = milo_meta.get("tags") or hermes_meta.get("tags") or self.meta.get("tags") or []
        return [str(t) for t in tags] if isinstance(tags, (list, tuple)) else []

    @property
    def is_agent_created(self) -> bool:
        return self.source == "agent"

    def index_line(self) -> str:
        """One line for the always-loaded system-prompt skill index."""
        desc = self.description.strip().replace("\n", " ")
        if len(desc) > INDEX_DESCRIPTION_CHARS:
            desc = desc[: INDEX_DESCRIPTION_CHARS - 3].rstrip() + "..."
        return f"- **{self.name}** — {desc}"

    def references(self) -> List[Path]:
        ref_dir = self.path / "references"
        return sorted(ref_dir.glob("*.md")) if ref_dir.is_dir() else []

    def scripts(self) -> List[Path]:
        script_dir = self.path / "scripts"
        if not script_dir.is_dir():
            return []
        return sorted(p for p in script_dir.iterdir() if p.is_file())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "source": self.source,
            "lifecycle": self.lifecycle,
            "pinned": self.pinned,
            "tags": self.tags,
            "path": str(self.path),
            "references": [p.name for p in self.references()],
            "scripts": [p.name for p in self.scripts()],
        }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SkillManager:
    """Discover, validate, create and edit skills across all skill roots."""

    def __init__(self, paths: Optional[MiloPaths] = None):
        self.paths = paths or get_paths()

    # -- roots ------------------------------------------------------------

    @property
    def bundled_root(self) -> Path:
        """Skills shipped inside the repo. Read-only at runtime."""
        return self.paths.assets_dir / "skills"

    @property
    def user_root(self) -> Path:
        """Skills the human wrote. Survive upgrades."""
        return self.paths.skills_dir

    @property
    def agent_root(self) -> Path:
        """Skills Milo wrote for itself. The curator only ever touches these."""
        return self.paths.skills_dir / "_agent"

    def roots(self) -> List[Tuple[Path, str]]:
        return [
            (self.bundled_root, "bundled"),
            (self.agent_root, "agent"),
            (self.user_root, "user"),
        ]

    # -- discovery --------------------------------------------------------

    def discover(self, include_archived: bool = False) -> List[Skill]:
        """Find every skill. Later roots win on name collision (user > agent > bundled)."""
        found: Dict[str, Skill] = {}
        for root, source in self.roots():
            if not root.is_dir():
                continue
            for skill_file in sorted(root.rglob("SKILL.md")):
                # Never let the agent root be double-counted as a user skill.
                if source == "user" and self.agent_root in skill_file.parents:
                    continue
                try:
                    skill = self._load(skill_file, root, source)
                except SkillError:
                    continue
                if skill.lifecycle == "archived" and not include_archived:
                    continue
                found[skill.name] = skill
        return sorted(found.values(), key=lambda s: s.name)

    def get(self, name: str, include_archived: bool = True) -> Optional[Skill]:
        for skill in self.discover(include_archived=include_archived):
            if skill.name == name:
                return skill
        return None

    def _load(self, skill_file: Path, root: Path, source: str) -> Skill:
        try:
            text = skill_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SkillError(f"unreadable: {skill_file} ({exc})") from exc

        meta, body = parse_frontmatter(text)
        name = str(meta.get("name") or skill_file.parent.name).strip()
        description = str(meta.get("description") or "").strip()
        if not name:
            raise SkillError(f"missing name: {skill_file}")

        relative = skill_file.parent.relative_to(root)
        category = str(relative.parent) if str(relative.parent) != "." else ""

        state = self._read_state(skill_file.parent)
        return Skill(
            name=name,
            description=description,
            path=skill_file.parent,
            body=body,
            meta=meta,
            category=category,
            source=source,
            lifecycle=state.get("lifecycle", "active"),
            pinned=bool(state.get("pinned", False)),
        )

    # -- per-skill state (lifecycle, pinning) -----------------------------

    @staticmethod
    def _state_file(skill_dir: Path) -> Path:
        return skill_dir / ".milo-skill.json"

    def _read_state(self, skill_dir: Path) -> Dict[str, Any]:
        path = self._state_file(skill_dir)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def write_state(self, skill_dir: Path, **updates: Any) -> Dict[str, Any]:
        state = self._read_state(skill_dir)
        state.update(updates)
        state["updated_at"] = int(time.time())
        self._state_file(skill_dir).write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        return state

    # -- validation -------------------------------------------------------

    def validate(self, name: str, description: str, body: str) -> List[str]:
        """Return a list of problems. Empty list means valid."""
        problems: List[str] = []
        if not name:
            problems.append("name is required")
        elif not VALID_NAME.match(name):
            problems.append(
                f"name {name!r} must be lowercase letters, digits and hyphens "
                f"(max {MAX_NAME_LENGTH} chars)"
            )
        if not description:
            problems.append("description is required")
        elif len(description) > MAX_DESCRIPTION_LENGTH:
            problems.append(
                f"description is {len(description)} chars "
                f"(max {MAX_DESCRIPTION_LENGTH})"
            )
        if not body.strip():
            problems.append("body is empty — a skill with no instructions does nothing")
        total = len(body) + len(description)
        if total > MAX_SKILL_CHARS:
            problems.append(f"skill is {total} chars (max {MAX_SKILL_CHARS})")
        return problems

    # -- mutation ---------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        body: str,
        *,
        category: str = "",
        source: str = "agent",
        tags: Optional[Iterable[str]] = None,
        overwrite: bool = False,
    ) -> Skill:
        """Write a new skill to disk. This is how Milo teaches itself."""
        name = (name or "").strip().lower().replace(" ", "-")
        problems = self.validate(name, description, body)
        if problems:
            raise SkillError("; ".join(problems))

        root = self.agent_root if source == "agent" else self.user_root
        target = root / category / name if category else root / name
        if target.exists() and not overwrite:
            raise SkillError(
                f"skill {name!r} already exists at {target} — pass overwrite=True "
                f"or use update()"
            )
        target.mkdir(parents=True, exist_ok=True)

        meta: Dict[str, Any] = {
            "name": name,
            "description": description,
            "version": "1.0.0",
            "author": "Milo",
            "license": "MIT",
            "metadata": {"milo": {"tags": list(tags or []), "created": _today()}},
        }
        content = dump_frontmatter(meta) + "\n\n" + body.strip() + "\n"
        (target / "SKILL.md").write_text(content, encoding="utf-8")
        self.write_state(target, lifecycle="active", created_at=int(time.time()), source=source)
        return self._load(target / "SKILL.md", root, source)

    def update(self, name: str, *, description: Optional[str] = None, body: Optional[str] = None) -> Skill:
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"no such skill: {name}")
        if skill.source == "bundled":
            raise SkillError(
                f"{name!r} is bundled with the repo — copy it to your own skills "
                f"directory before editing so upgrades don't clobber it"
            )
        meta = dict(skill.meta)
        if description is not None:
            meta["description"] = description
        new_body = body if body is not None else skill.body
        problems = self.validate(skill.name, str(meta.get("description") or ""), new_body)
        if problems:
            raise SkillError("; ".join(problems))
        meta.setdefault("metadata", {}).setdefault("milo", {})["updated"] = _today()
        skill.skill_file.write_text(
            dump_frontmatter(meta) + "\n\n" + new_body.strip() + "\n", encoding="utf-8"
        )
        self.write_state(skill.path, lifecycle="active")
        return self.get(name)  # type: ignore[return-value]

    def set_lifecycle(self, name: str, lifecycle: str) -> Skill:
        if lifecycle not in LIFECYCLE:
            raise SkillError(f"lifecycle must be one of {LIFECYCLE}")
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"no such skill: {name}")
        self.write_state(skill.path, lifecycle=lifecycle)
        skill.lifecycle = lifecycle
        return skill

    def pin(self, name: str, pinned: bool = True) -> Skill:
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"no such skill: {name}")
        self.write_state(skill.path, pinned=pinned)
        skill.pinned = pinned
        return skill

    def delete(self, name: str, *, force: bool = False) -> bool:
        """Remove a skill. Archiving is preferred — deletion is unrecoverable."""
        skill = self.get(name)
        if skill is None:
            return False
        if skill.source == "bundled":
            raise SkillError("refusing to delete a bundled skill")
        if not force:
            raise SkillError(
                "deletion is permanent; archive instead, or pass force=True"
            )
        shutil.rmtree(skill.path, ignore_errors=True)
        return True

    # -- rendering --------------------------------------------------------

    def index(self, include_archived: bool = False) -> str:
        """The always-loaded skill index injected into system prompts."""
        skills = self.discover(include_archived=include_archived)
        if not skills:
            return ""
        by_category: Dict[str, List[Skill]] = {}
        for skill in skills:
            by_category.setdefault(skill.category or "general", []).append(skill)
        lines: List[str] = []
        for category in sorted(by_category):
            lines.append(f"\n**{category}**")
            lines.extend(s.index_line() for s in by_category[category])
        return "\n".join(lines).strip()

    def view(self, name: str, include_references: bool = False) -> str:
        skill = self.get(name)
        if skill is None:
            return f"error: no such skill: {name}"
        parts = [f"# {skill.name}", "", skill.description, "", skill.body]
        if include_references:
            for ref in skill.references():
                parts += ["", f"--- reference: {ref.name} ---", ref.read_text(encoding="utf-8")]
        return "\n".join(parts)


def _today() -> str:
    return time.strftime("%Y-%m-%d")
