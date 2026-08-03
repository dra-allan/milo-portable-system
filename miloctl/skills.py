"""
skills.py — procedural memory.
==============================

A **skill** is a folder containing ``SKILL.md`` with YAML frontmatter, plus
optional ``scripts/``, ``references/`` and ``templates/``. This is the
`agentskills.io <https://agentskills.io>`_ / Hermes / Claude-Code layout, so
skills written for Milo work in those tools and vice-versa.

Why skills instead of just memories
-----------------------------------
Memory answers *"what is true"*. A skill answers *"how do I do this"* — and
crucially it can be **improved in place** while it is being used. That is the
learning loop: Milo does a hard thing, writes down how, and next time reads
its own notes instead of rediscovering the procedure.

Layout
------
::

    $MILO_HOME/skills/
        <name>/SKILL.md              user + agent-authored (writable)
    <repo>/skills/<category>/<name>/SKILL.md   bundled (read-only)

Frontmatter fields
------------------
``name`` ``description`` ``version`` ``author`` ``platforms`` ``tags``
``origin`` (``bundled`` | ``user`` | ``agent``) ``lifecycle``
(``active`` | ``stale`` | ``archived``) ``pinned``.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths

SKILL_FILE = "SKILL.md"
MAX_DESCRIPTION = 60
USAGE_FILE = "usage.json"

LIFECYCLES = ("active", "stale", "archived")
ORIGINS = ("bundled", "user", "agent", "pack")


# ── Frontmatter ───────────────────────────────────────────────────────────────

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse the YAML-ish frontmatter block. Deliberately dependency-free.

    Supports scalars, ``[a, b, c]`` inline lists, ``- item`` block lists and
    one level of ``key:`` nesting (enough for ``metadata.milo.tags``).
    """
    m = _FM_RE.match(text or "")
    if not m:
        return {}, text or ""
    body = text[m.end():]
    data: Dict[str, Any] = {}
    stack: List[Tuple[int, Dict[str, Any]]] = [(-1, data)]
    last_key: Optional[str] = None

    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()

        if line.startswith("- ") and last_key is not None:
            parent = stack[-1][1]
            parent.setdefault(last_key, [])
            if isinstance(parent[last_key], list):
                parent[last_key].append(_coerce(line[2:].strip()))
            continue

        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(-1, data)]
        parent = stack[-1][1]

        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not value:
            child: Dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
            last_key = key
        else:
            parent[key] = _coerce(value)
            last_key = key
    return data, body


def _coerce(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_coerce(p.strip()) for p in inner.split(",")]
    low = value.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", "~"):
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def dump_frontmatter(data: Dict[str, Any]) -> str:
    """Render frontmatter back out. Stable key order for clean diffs."""
    order = ["name", "description", "version", "author", "platforms",
             "tags", "origin", "lifecycle", "pinned", "created", "updated"]
    keys = [k for k in order if k in data] + [k for k in data if k not in order]
    lines = ["---"]
    for key in keys:
        val = data[key]
        if isinstance(val, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in val)}]")
        elif isinstance(val, bool):
            lines.append(f"{key}: {'true' if val else 'false'}")
        elif isinstance(val, dict):
            lines.append(f"{key}:")
            for k2, v2 in val.items():
                if isinstance(v2, list):
                    lines.append(f"  {k2}: [{', '.join(str(v) for v in v2)}]")
                else:
                    lines.append(f"  {k2}: {v2}")
        elif val is None:
            continue
        else:
            sval = str(val)
            if ":" in sval or sval.startswith(("[", "{", "#", "&", "*")):
                sval = '"' + sval.replace('"', '\\"') + '"'
            lines.append(f"{key}: {sval}")
    lines.append("---")
    return "\n".join(lines)


# ── Skill record ──────────────────────────────────────────────────────────────


@dataclass
class Skill:
    name: str
    description: str = ""
    path: Path = field(default_factory=Path)
    version: str = "0.1.0"
    author: str = "Milo"
    platforms: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    origin: str = "user"
    lifecycle: str = "active"
    pinned: bool = False
    category: str = ""
    body: str = ""
    raw: str = ""

    # -- derived ---------------------------------------------------------------

    @property
    def skill_file(self) -> Path:
        return self.path / SKILL_FILE

    @property
    def editable(self) -> bool:
        """Bundled skills live in the repo; agent edits go to MILO_HOME."""
        return self.origin != "bundled"

    def supports(self, platform_id: Optional[str] = None) -> bool:
        if not self.platforms:
            return True
        return (platform_id or paths.platform_id()) in [
            str(p).lower() for p in self.platforms
        ]

    def index_line(self) -> str:
        """One line for the system-prompt skill index. Description is hard
        truncated at 60 chars — anything past that never routes."""
        desc = (self.description or "").strip()
        if len(desc) > MAX_DESCRIPTION:
            desc = desc[: MAX_DESCRIPTION - 1] + "…"
        return f"- `{self.name}` — {desc}"

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k not in ("body", "raw")}
        d["path"] = str(self.path)
        return d


def _load_skill(skill_dir: Path, origin: str, category: str = "") -> Optional[Skill]:
    f = skill_dir / SKILL_FILE
    if not f.is_file():
        return None
    try:
        raw = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = parse_frontmatter(raw)
    meta = fm.get("metadata") or {}
    nested = (meta.get("milo") or meta.get("hermes") or {}) if isinstance(meta, dict) else {}
    tags = fm.get("tags") or (nested.get("tags") if isinstance(nested, dict) else []) or []
    return Skill(
        name=str(fm.get("name") or skill_dir.name).strip(),
        description=str(fm.get("description") or "").strip(),
        path=skill_dir,
        version=str(fm.get("version") or "0.1.0"),
        author=str(fm.get("author") or "Milo"),
        platforms=[str(p).lower() for p in (fm.get("platforms") or [])],
        tags=[str(t) for t in (tags if isinstance(tags, list) else [tags])],
        origin=str(fm.get("origin") or origin),
        lifecycle=str(fm.get("lifecycle") or "active"),
        pinned=bool(fm.get("pinned") or False),
        category=category,
        body=body,
        raw=raw,
    )


# ── Registry ──────────────────────────────────────────────────────────────────


class SkillRegistry:
    """Discovers, reads, writes and tracks usage of skills."""

    def __init__(self, user_dir: Optional[Path] = None,
                 bundled_dir: Optional[Path] = None):
        self.user_dir = Path(user_dir or paths.skills_dir())
        self.bundled_dir = Path(bundled_dir or paths.bundled("skills"))
        paths.ensure(self.user_dir)

    # -- discovery -------------------------------------------------------------

    def _scan_bundled(self) -> List[Skill]:
        out: List[Skill] = []
        if not self.bundled_dir.is_dir():
            return out
        for entry in sorted(self.bundled_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if (entry / SKILL_FILE).is_file():        # flat: skills/<name>/
                s = _load_skill(entry, "bundled")
                if s:
                    out.append(s)
                continue
            for sub in sorted(entry.iterdir()):        # nested: skills/<cat>/<name>/
                if sub.is_dir() and (sub / SKILL_FILE).is_file():
                    s = _load_skill(sub, "bundled", category=entry.name)
                    if s:
                        out.append(s)
        return out

    def _scan_user(self) -> List[Skill]:
        out: List[Skill] = []
        if not self.user_dir.is_dir():
            return out
        for entry in sorted(self.user_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if (entry / SKILL_FILE).is_file():
                s = _load_skill(entry, "user")
                if s:
                    out.append(s)
                continue
            for sub in sorted(entry.iterdir()):
                if sub.is_dir() and (sub / SKILL_FILE).is_file():
                    s = _load_skill(sub, "user", category=entry.name)
                    if s:
                        out.append(s)
        return out

    def _scan_packs(self) -> List[Skill]:
        """Skills imported from third-party packs.

        Failures here are swallowed on purpose: a malformed pack must not be
        able to take down `milo skills list`, which is exactly what you would
        reach for to diagnose the bad pack.
        """
        try:
            from . import packs
        except Exception:
            return []
        out: List[Skill] = []
        root = packs.packs_dir()
        if not root.is_dir():
            return out
        for pack in sorted(root.iterdir()):
            skills_root = pack / "skills"
            if not skills_root.is_dir():
                continue
            for skill_md in sorted(skills_root.rglob(SKILL_FILE)):
                rel = skill_md.parent.relative_to(skills_root)
                category = rel.parts[0] if len(rel.parts) > 1 else ""
                s = _load_skill(skill_md.parent, "pack", category=category)
                if s:
                    out.append(s)
        return out

    def all(self, include_archived: bool = False) -> List[Skill]:
        """User skills shadow bundled skills of the same name.

        Pack skills come first so a same-named bundled or user skill wins: what
        Milo ships, and what Allan wrote, both outrank a third-party import.
        """
        merged: Dict[str, Skill] = {}
        for s in self._scan_packs():
            merged[s.name.lower()] = s
        for s in self._scan_bundled():
            merged[s.name.lower()] = s
        for s in self._scan_user():
            merged[s.name.lower()] = s
        out = list(merged.values())
        if not include_archived:
            out = [s for s in out if s.lifecycle != "archived"]
        out.sort(key=lambda s: (not s.pinned, s.name.lower()))
        return out

    def get(self, name: str) -> Optional[Skill]:
        target = (name or "").strip().lower().replace(" ", "-")
        for s in self.all(include_archived=True):
            if s.name.lower() == target or s.path.name.lower() == target:
                return s
        # unique prefix match — `milo skill show obsid` should work
        hits = [s for s in self.all(include_archived=True)
                if s.name.lower().startswith(target)]
        return hits[0] if len(hits) == 1 else None

    def search(self, query: str, limit: int = 20) -> List[Skill]:
        q = (query or "").strip().lower()
        if not q:
            return self.all()[:limit]
        terms = q.split()
        scored: List[Tuple[int, Skill]] = []
        for s in self.all():
            hay = " ".join([s.name, s.description, " ".join(s.tags),
                            s.category, s.body[:2000]]).lower()
            score = 0
            for t in terms:
                if t in s.name.lower():
                    score += 10
                if t in s.description.lower():
                    score += 5
                if any(t in tag.lower() for tag in s.tags):
                    score += 4
                if t in hay:
                    score += 1
            if score:
                scored.append((score, s))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return [s for _, s in scored[:limit]]

    # -- system prompt ---------------------------------------------------------

    def index(self, platform_id: Optional[str] = None, limit: int = 200) -> str:
        """Skill index injected into the system prompt every session.

        Pack skills are included **only when explicitly enabled**. This is the
        single most important line in the file: the three libraries Milo can
        import total ~18,300 tokens of index. Spending that every turn would
        not just be expensive, it would make routing *worse* — a 300-line menu
        is harder to choose from than a 12-line one. So installed-and-findable
        is the default, and in-the-prompt is opt-in.
        """
        try:
            from . import packs
            enabled = set(packs.enabled_names())
            n_installed = len(packs.catalogue())
        except Exception:
            enabled, n_installed = set(), 0

        usable = [
            s for s in self.all()
            if s.supports(platform_id) and s.lifecycle == "active"
            and (s.origin != "pack" or s.name in enabled)
        ]
        if not usable:
            return ""
        lines = [
            "## Skills available",
            "",
            "Load a skill's SKILL.md before doing the thing it covers. "
            "If no skill fits and the task was non-trivial, write one after.",
            "",
        ]
        lines += [s.index_line() for s in usable[:limit]]

        hidden = n_installed - len([s for s in usable if s.origin == "pack"])
        if hidden > 0:
            lines += [
                "",
                f"{hidden} more skills and agents are installed but not listed "
                "here, to keep this index short. Search them with "
                "`milo skills search \"<what you need>\"` and read one with "
                "`milo skills show <name>`.",
            ]
        return "\n".join(lines)

    # -- authoring -------------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        body: str = "",
        *,
        tags: Optional[Sequence[str]] = None,
        platforms: Optional[Sequence[str]] = None,
        origin: str = "user",
        author: str = "Milo",
        overwrite: bool = False,
    ) -> Skill:
        slug = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")
        if not slug:
            raise ValueError("skill needs a name")
        target = self.user_dir / slug
        if target.exists() and not overwrite:
            raise FileExistsError(f"skill '{slug}' already exists at {target}")
        paths.ensure(target)

        desc = (description or "").strip()
        if len(desc) > MAX_DESCRIPTION:
            desc = desc[: MAX_DESCRIPTION - 1].rstrip() + "."

        today = time.strftime("%Y-%m-%d")
        fm = {
            "name": slug,
            "description": desc,
            "version": "0.1.0",
            "author": author,
            "tags": list(tags or []),
            "origin": origin,
            "lifecycle": "active",
            "created": today,
            "updated": today,
        }
        if platforms:
            fm["platforms"] = list(platforms)
        content = dump_frontmatter(fm) + "\n\n" + (body.strip() or _scaffold(slug, desc))
        (target / SKILL_FILE).write_text(content + "\n", encoding="utf-8")
        s = _load_skill(target, origin)
        assert s is not None
        return s

    def write_body(self, name: str, body: str) -> Optional[Skill]:
        """Replace a skill's body, bumping ``updated``. Copies bundled → user
        on first edit so shipped skills stay pristine and updatable."""
        s = self.get(name)
        if not s:
            return None
        if not s.editable:
            s = self.fork(s.name) or s
        fm, _ = parse_frontmatter(s.raw)
        fm["updated"] = time.strftime("%Y-%m-%d")
        fm["version"] = _bump(str(fm.get("version") or "0.1.0"))
        s.skill_file.write_text(
            dump_frontmatter(fm) + "\n\n" + body.strip() + "\n", encoding="utf-8"
        )
        return _load_skill(s.path, s.origin)

    def fork(self, name: str) -> Optional[Skill]:
        """Copy a bundled skill into ``$MILO_HOME/skills`` so it can evolve."""
        s = self.get(name)
        if not s or s.editable:
            return s
        target = self.user_dir / s.path.name
        if target.exists():
            return _load_skill(target, "user")
        shutil.copytree(s.path, target)
        fm, body = parse_frontmatter(
            (target / SKILL_FILE).read_text(encoding="utf-8", errors="replace")
        )
        fm["origin"] = "user"
        (target / SKILL_FILE).write_text(
            dump_frontmatter(fm) + "\n\n" + body.strip() + "\n", encoding="utf-8"
        )
        return _load_skill(target, "user")

    def set_meta(self, name: str, **changes: Any) -> Optional[Skill]:
        s = self.get(name)
        if not s:
            return None
        if not s.editable:
            s = self.fork(s.name)
            if not s:
                return None
        fm, body = parse_frontmatter(s.raw if s.raw else s.skill_file.read_text("utf-8"))
        fm.update({k: v for k, v in changes.items() if v is not None})
        fm["updated"] = time.strftime("%Y-%m-%d")
        s.skill_file.write_text(
            dump_frontmatter(fm) + "\n\n" + body.strip() + "\n", encoding="utf-8"
        )
        return _load_skill(s.path, s.origin)

    def archive(self, name: str) -> Optional[Skill]:
        """Never delete — archive. Recoverable with ``milo skill restore``."""
        return self.set_meta(name, lifecycle="archived")

    def restore(self, name: str) -> Optional[Skill]:
        return self.set_meta(name, lifecycle="active")

    def remove(self, name: str, hard: bool = False) -> bool:
        s = self.get(name)
        if not s:
            return False
        if not hard:
            return self.archive(name) is not None
        if not s.editable:
            return False
        shutil.rmtree(s.path, ignore_errors=True)
        return True

    # -- usage tracking (drives the curator) -----------------------------------

    def _usage_path(self) -> Path:
        return self.user_dir / f".{USAGE_FILE}"

    def usage(self) -> Dict[str, Dict[str, Any]]:
        p = self._usage_path()
        if not p.is_file():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def record_use(self, name: str, outcome: str = "used") -> None:
        data = self.usage()
        entry = data.setdefault(name, {"count": 0, "first_used": time.time(),
                                       "outcomes": {}})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_used"] = time.time()
        entry["outcomes"][outcome] = int(entry["outcomes"].get(outcome, 0)) + 1
        paths.ensure(self.user_dir)
        self._usage_path().write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )

    def stats(self) -> Dict[str, Any]:
        skills = self.all(include_archived=True)
        usage = self.usage()
        return {
            "total": len(skills),
            "active": sum(1 for s in skills if s.lifecycle == "active"),
            "archived": sum(1 for s in skills if s.lifecycle == "archived"),
            "pinned": sum(1 for s in skills if s.pinned),
            "bundled": sum(1 for s in skills if s.origin == "bundled"),
            "user": sum(1 for s in skills if s.origin == "user"),
            "agent": sum(1 for s in skills if s.origin == "agent"),
            "used": len(usage),
            "user_dir": str(self.user_dir),
            "bundled_dir": str(self.bundled_dir),
        }

    # -- validation ------------------------------------------------------------

    def lint(self, name: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """Return ``(skill, level, message)`` problems. Mirrors the Hermes
        HARDLINE authoring rules that actually affect routing."""
        problems: List[Tuple[str, str, str]] = []
        targets = [self.get(name)] if name else self.all(include_archived=True)
        for s in [t for t in targets if t]:
            if not s.description:
                problems.append((s.name, "error", "missing description — it will never route"))
            elif len(s.description) > MAX_DESCRIPTION:
                problems.append((
                    s.name, "error",
                    f"description is {len(s.description)} chars; the prompt index "
                    f"truncates at {MAX_DESCRIPTION}",
                ))
            if s.description and not s.description.endswith("."):
                problems.append((s.name, "warn", "description should end with a period"))
            if re.search(r"\b(powerful|comprehensive|seamless|advanced|robust)\b",
                         s.description, re.IGNORECASE):
                problems.append((s.name, "warn", "marketing words in description"))
            if s.name.lower() in s.description.lower():
                problems.append((s.name, "warn", "description repeats the skill name"))
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", s.name):
                problems.append((s.name, "error", "name must be lowercase-hyphenated, <=64 chars"))
            if len(s.body.strip()) < 80:
                problems.append((s.name, "warn", "body is nearly empty"))
            for plat in s.platforms:
                if plat not in ("linux", "macos", "windows", "termux", "wsl"):
                    problems.append((s.name, "warn", f"unknown platform '{plat}'"))
        return problems


def _bump(version: str) -> str:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[2] = str(int(parts[2]) + 1)
    except ValueError:
        parts[2] = "1"
    return ".".join(parts[:3])


def _scaffold(slug: str, description: str) -> str:
    title = slug.replace("-", " ").title()
    return f"""# {title}

{description or 'Describe what this does, what it does NOT do, and its dependencies.'}

## When to Use

- Trigger phrase one.
- Trigger phrase two.

## Prerequisites

- Required env vars, credentials, or installed tools.

## How to Run

Invoke through the `terminal` tool, or read files with `read_file` and search
with `search_files`.

## Quick Reference

| Action | Command |
|--------|---------|
|        |         |

## Procedure

1. Step one — exact, copy-pasteable.
2. Step two.

## Pitfalls

- Known limits and things that look broken but aren't.

## Verification

A single command or check that proves the skill worked.
"""


# ── Convenience ───────────────────────────────────────────────────────────────

_REGISTRY: Optional[SkillRegistry] = None


def registry() -> SkillRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SkillRegistry()
    return _REGISTRY
