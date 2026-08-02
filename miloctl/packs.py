"""
packs.py — borrow other people's skills, agents and commands.
=============================================================

There are good public libraries of agent capabilities — ``obra/superpowers``,
``msitarzewski/agency-agents``, ``WorldFlowAI/everything-claude-code`` — and no
reason to rewrite them. They are, however, written for Claude Code, in three
different shapes, and one of them contains 316 agents.

That last number is the whole design problem.

The index is the routing decision
---------------------------------
A model picks a skill by reading one line per skill in its system prompt.
Importing all three libraries naively costs **~18,300 tokens of index** before
a single word of the conversation. That is not merely expensive: a 300-line
menu makes the model *worse* at choosing than a 12-line one, so you would pay
a fortune to degrade routing.

So installation and indexing are separated:

* **installed** — on disk, searchable, one command away. Cheap, unlimited.
* **enabled**   — in the prompt index. Costs tokens every single turn, so it
  is opt-in and deliberately small.

``milo packs add`` installs. ``milo skills search`` finds things across
everything installed. ``milo skills enable <name>`` promotes one into the
index. The default after adding a pack is *nothing enabled*, because a system
that silently spends your context budget is a system you stop trusting.

Shapes we accept
----------------
=========================  =======================================
``skills/*/SKILL.md``      agentskills.io / Claude Code skills
``agents/*.md``            subagent personas (frontmatter or bare)
``commands/*.md``          slash commands, frequently no frontmatter
=========================  =======================================

Anything else in the repo is ignored. Detection is by content, not by trusting
a layout convention, because these three repos already disagree about layout
and the fourth one will disagree differently.

Portability
-----------
Imported material is normalised into Milo's own skill format, which the harness
layer already writes out for OpenCode, Claude Code, Codex, Cursor and Gemini.
So a Claude-Code-only library becomes usable in every tool Milo supports —
which is the point of importing it here rather than into one vendor's folder.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import paths
from .skills import MAX_DESCRIPTION, parse_frontmatter, dump_frontmatter

#: Curated shorthands, so nobody has to remember a URL. These are the three
#: libraries Allan asked for; ``milo packs add <url>`` takes any other git repo.
KNOWN_PACKS: Dict[str, Dict[str, str]] = {
    "superpowers": {
        "url": "https://github.com/obra/superpowers",
        "summary": "Process skills: brainstorming, planning, TDD, code review.",
    },
    "agency-agents": {
        "url": "https://github.com/msitarzewski/agency-agents",
        "summary": "300+ specialist agent personas across engineering, design, ops.",
    },
    "everything-claude-code": {
        "url": "https://github.com/WorldFlowAI/everything-claude-code",
        "summary": "Skills, subagents and slash commands for day-to-day coding.",
    },
}

KINDS = ("skill", "agent", "command")

#: Directory names that signal each kind. Checked as a path *component*, so
#: ``plugins/foo/agents/bar.md`` is still recognised as an agent.
_KIND_DIRS = {
    "skills": "skill",
    "agents": "agent",
    "subagents": "agent",
    "commands": "command",
}

#: Never import these — they are repo furniture, not capabilities.
_IGNORE_NAMES = {
    "readme", "contributing", "license", "licence", "changelog", "security",
    "code_of_conduct", "release-notes", "index", "template", "example",
}
_IGNORE_PARTS = {".git", "node_modules", "tests", "test", "__pycache__",
                 "assets", "docs", "scripts", "examples", ".github"}


# ── Description repair ────────────────────────────────────────────────────────
#
# Imported descriptions are frequently unusable as index lines: 200+ characters,
# written as instructions to the model ("You MUST use this before any creative
# work..."), or missing entirely. The index truncates at 60 characters, so an
# unrepaired description means the entry is *present but unroutable* — the worst
# outcome, because it costs tokens and returns nothing.

_LEAD_NOISE = re.compile(
    r"^(you\s+must\s+use\s+this\s+(skill\s+)?(before|when|for)\s*"
    r"|use\s+this\s+(skill\s+)?(proactively\s+)?(before|when|for|to)\s*"
    r"|this\s+skill\s+(is\s+for|should\s+be\s+used|helps?)\s*"
    r"|expert\s+(at|in)\s+|specialist\s+(at|in)\s+"
    r"|use\s+proactively\s*(when|for|to)?\s*)",
    re.IGNORECASE,
)
_MARKETING = re.compile(
    r"\b(comprehensive|powerful|seamless|robust|cutting[- ]edge|world[- ]class|"
    r"advanced|ultimate|complete|holistic|state[- ]of[- ]the[- ]art)\b",
    re.IGNORECASE,
)


def tidy_description(text: str, fallback_name: str = "") -> str:
    """Turn an imported description into something that can actually route.

    Keeps the first sentence, strips the "You MUST use this before..." framing
    that reads as an instruction rather than a label, drops marketing adjectives
    that waste the 60-character budget, and only then truncates — at a word
    boundary, because a line cut mid-word looks broken and reads worse.
    """
    text = " ".join((text or "").split())
    if not text:
        return f"{fallback_name.replace('-', ' ').capitalize()}." if fallback_name else ""

    text = _LEAD_NOISE.sub("", text).strip()
    # First sentence only — imported descriptions often run to a paragraph.
    for sep in (" — ", ". ", "; "):
        if sep in text:
            head = text.split(sep, 1)[0].strip()
            if len(head) >= 20:       # a too-short head means the split was noise
                text = head
                break
    text = _MARKETING.sub("", text)
    text = " ".join(text.split()).strip(" ,;:—-")
    if not text:
        return f"{fallback_name.replace('-', ' ').capitalize()}."

    text = text[0].upper() + text[1:]
    if len(text) > MAX_DESCRIPTION:
        cut = text[: MAX_DESCRIPTION - 1]
        if " " in cut:
            cut = cut[: cut.rindex(" ")]
        text = cut.rstrip(" ,;:—-") + "…"
    elif not text.endswith((".", "…", "?", "!")):
        text += "."
    return text


def slugify(text: str) -> str:
    """``AI Data Remediation Engineer`` -> ``ai-data-remediation-engineer``."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s-]", " ", text).strip().lower()
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


# ── Discovery ─────────────────────────────────────────────────────────────────


@dataclass
class Item:
    """One importable thing found in a source tree."""

    kind: str
    name: str
    description: str
    body: str
    source_path: Path
    category: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


#: Sniffed from the body when the directory gives us nothing. Every agent
#: persona in the wild opens by telling the model who it is.
_AGENT_BODY = re.compile(
    r"^\s*#{0,3}\s*.*\n+\s*You are (a|an|the)\b|^\s*You are (a|an|the)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _kind_for(rel: Path, meta: Dict[str, Any], body: str) -> str:
    """Classify by directory first, then by content.

    Content sniffing is not a nicety. ``agency-agents`` keeps its 316 personas
    in division directories at the repo root — ``engineering/``, ``design/``,
    ``finance/`` — with no ``agents/`` folder anywhere. A directory-only
    classifier finds exactly zero of them and reports success, which is the
    kind of silent no-op that makes an importer untrustworthy.
    """
    for part in rel.parts[:-1]:
        hit = _KIND_DIRS.get(part.lower())
        if hit:
            return hit
    if _AGENT_BODY.search(body[:1500]) and meta.get("description"):
        return "agent"
    # Persona frontmatter. `tools`/`model` are the Claude Code subagent fields;
    # `emoji`/`color`/`vibe` are how agency-agents styles a persona. Requiring
    # name+description alongside keeps this from swallowing ordinary docs that
    # happen to carry a colour.
    if meta.get("name") and meta.get("description") and any(
        meta.get(k) for k in ("tools", "model", "vibe", "emoji", "color")
    ):
        return "agent"
    return ""


def _category_for(rel: Path, kind: str) -> str:
    """Grouping label, or "" when there is no real grouping.

    Getting this wrong in either direction hurts. Inventing a category per item
    (``brainstorming`` for ``skills/brainstorming/SKILL.md``) produces 300
    categories of one, which is no better than a flat list. Finding none for
    agency-agents' divisions loses the only structure that makes 316 agents
    navigable.
    """
    parts = list(rel.parts)
    # A skill's own folder is its name, never its category:
    #   skills/brainstorming/SKILL.md   -> no category
    #   skills/web/scraping/SKILL.md    -> "web"
    if kind == "skill" and parts[-1] == "SKILL.md":
        parts = parts[:-1]
        for i, p in enumerate(parts):
            if p.lower() in _KIND_DIRS and i + 2 <= len(parts) - 1:
                return slugify(parts[i + 1])
        return ""

    for i, p in enumerate(parts[:-1]):
        if p.lower() in _KIND_DIRS:
            rest = parts[i + 1:-1]
            return slugify(rest[0]) if rest else ""

    # No kind directory at all: the top-level folder IS the grouping. This is
    # agency-agents, where `engineering/foo.md` means division "engineering".
    if len(parts) > 1:
        return slugify(parts[0])
    return ""


def _first_prose_line(body: str) -> str:
    """First real sentence of a document, for files with no frontmatter."""
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "```", "---", "<!--", ">", "|")):
            continue
        if line.startswith(("-", "*", "1.")):
            line = line.lstrip("-*0123456789. ").strip()
            if not line:
                continue
        return line
    # Fall back to the first heading — better than nothing.
    for raw in body.splitlines():
        if raw.strip().startswith("#"):
            return raw.lstrip("# ").strip()
    return ""


def discover(root: Path) -> List[Item]:
    """Walk a source tree and return everything importable.

    Classification is by directory component *and* content, so a repo that
    keeps agents under ``plugins/x/agents/`` is handled without a special case.
    """
    root = Path(root)
    items: List[Item] = []
    seen: set = set()

    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if any(p in _IGNORE_PARTS for p in rel.parts):
            continue
        stem = path.stem.lower()
        if stem in _IGNORE_NAMES and path.name != "SKILL.md":
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue

        meta, body = parse_frontmatter(text)
        kind = _kind_for(rel, meta, body)
        if not kind:
            continue
        if kind == "skill" and path.name != "SKILL.md":
            # A skills/ dir can hold supporting docs; only SKILL.md is the skill.
            continue
        raw_name = str(meta.get("name") or "").strip()
        if not raw_name:
            # SKILL.md takes its identity from the containing directory.
            raw_name = path.parent.name if path.name == "SKILL.md" else path.stem
        name = slugify(raw_name)
        if not name or name in seen:
            continue
        seen.add(name)

        desc = str(meta.get("description") or "").strip() or _first_prose_line(body)

        category = _category_for(rel, kind)

        items.append(Item(
            kind=kind,
            name=name,
            description=desc,
            body=body.strip(),
            source_path=path,
            category=category,
            meta={k: v for k, v in meta.items()
                  if k in ("tools", "model", "color", "emoji", "vibe",
                           "version", "author", "license", "allowed-tools")},
        ))
    return items
