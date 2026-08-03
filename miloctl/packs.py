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


# ── Where packs live ──────────────────────────────────────────────────────────


def packs_dir() -> Path:
    return paths.milo_home() / "packs"


def pack_dir(name: str) -> Path:
    return packs_dir() / slugify(name)


def registry_file() -> Path:
    """Which packs are installed, and which of their items are enabled."""
    return paths.state_dir() / "packs.json"


def _load_registry() -> Dict[str, Any]:
    f = registry_file()
    if not f.is_file():
        return {"packs": {}, "enabled": []}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"packs": {}, "enabled": []}
    data.setdefault("packs", {})
    data.setdefault("enabled", [])
    return data


def _save_registry(data: Dict[str, Any]) -> Path:
    f = registry_file()
    paths.ensure(f.parent)
    f.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f


# ── Fetching ──────────────────────────────────────────────────────────────────


def _run_git(args: List[str], cwd: Optional[Path] = None,
             timeout: int = 300) -> Tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "git is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} timed out"
    return p.returncode, (p.stdout + p.stderr).strip()


def resolve_source(source: str) -> Tuple[str, str]:
    """``(kind, location)`` where kind is ``local`` or ``git``.

    Accepts a shorthand from KNOWN_PACKS, a bare ``owner/repo``, a full URL, or
    a path on disk. Local paths are checked first so a directory that happens to
    be named like a shorthand still wins — surprising the user with a network
    fetch when they pointed at a folder would be worse than a name clash.
    """
    source = str(source or "").strip()
    if not source:
        raise ValueError("no source given")

    p = Path(source).expanduser()
    if p.exists():
        return "local", str(p.resolve())

    if source in KNOWN_PACKS:
        return "git", KNOWN_PACKS[source]["url"]
    if re.fullmatch(r"[\w.-]+/[\w.-]+", source):
        return "git", f"https://github.com/{source}"
    if source.startswith(("http://", "https://", "git@", "ssh://")):
        return "git", source
    raise ValueError(
        f"don't know how to fetch {source!r} — "
        f"try a git URL, owner/repo, a local path, or one of: "
        f"{', '.join(sorted(KNOWN_PACKS))}"
    )


def fetch(source: str, dest: Path) -> Tuple[bool, str]:
    """Clone or copy ``source`` into ``dest``. Replaces any previous copy."""
    kind, location = resolve_source(source)
    dest = Path(dest)
    tmp = dest.parent / f".{dest.name}.incoming"
    shutil.rmtree(tmp, ignore_errors=True)
    paths.ensure(dest.parent)

    if kind == "local":
        try:
            shutil.copytree(location, tmp,
                            ignore=shutil.ignore_patterns(".git", "node_modules",
                                                          "__pycache__"))
        except OSError as exc:
            return False, str(exc)
    else:
        code, out = _run_git(["clone", "--depth", "1", "--quiet", location, str(tmp)])
        if code != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            return False, out or f"git clone failed ({code})"

    # Swap in only after a successful fetch, so a failed update never leaves a
    # half-written pack where a working one used to be.
    shutil.rmtree(dest, ignore_errors=True)
    tmp.replace(dest)
    return True, location


# ── Installing ────────────────────────────────────────────────────────────────


@dataclass
class InstallResult:
    pack: str = ""
    source: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    enabled: List[str] = field(default_factory=list)
    renamed: Dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def render(self) -> str:
        if self.error:
            return f"{self.pack}: {self.error}"
        bits = ", ".join(f"{v} {k}{'s' if v != 1 else ''}"
                         for k, v in sorted(self.counts.items()) if v)
        line = f"{self.pack}: {bits or 'nothing importable found'}"
        if self.renamed:
            line += f" ({len(self.renamed)} renamed to avoid a clash)"
        return line


def _write_item(item: Item, dest_root: Path, pack: str,
                final_name: str) -> Path:
    """Write one item out as a Milo skill.

    Everything becomes a skill on disk — agents and commands included — because
    the skill format is what the harness layer already renders into OpenCode,
    Claude Code, Codex, Cursor and Gemini. Keeping three parallel on-disk
    formats would mean teaching every harness three things instead of one, and
    the distinction is preserved in tags rather than lost.
    """
    folder = dest_root / (item.category or "") / final_name
    folder.mkdir(parents=True, exist_ok=True)

    tags = [f"pack:{pack}", item.kind]
    if item.category:
        tags.append(item.category)

    fm: Dict[str, Any] = {
        "name": final_name,
        "description": tidy_description(item.description, final_name),
        "version": str(item.meta.get("version") or "1.0.0"),
        "author": str(item.meta.get("author") or pack),
        "tags": tags,
        "origin": "pack",
        "lifecycle": "active",
        "pinned": False,
    }
    # Carry the original text through untouched. The repaired description is
    # for routing; the full one is context once the skill is actually opened,
    # and throwing it away would lose real information.
    extras = []
    if item.description and item.description != fm["description"]:
        extras.append(f"> {' '.join(item.description.split())}")
    for key in ("tools", "model"):
        if item.meta.get(key):
            extras.append(f"> **{key}**: {item.meta[key]}")
    header = "\n".join(extras)

    provenance = (
        f"\n\n---\n*From the `{pack}` pack "
        f"(`{item.source_path.name}`). Update with `milo packs update {pack}`; "
        f"local edits will be overwritten.*\n"
    )
    body = item.body if item.body.lstrip().startswith("#") else \
        f"# {final_name.replace('-', ' ').title()}\n\n{item.body}"

    text = dump_frontmatter(fm) + "\n\n"
    if header:
        text += header + "\n\n"
    text += body.strip() + provenance
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    return folder


def install(source: str, *, name: str = "", enable: Iterable[str] = (),
            kinds: Iterable[str] = KINDS) -> InstallResult:
    """Fetch a pack, normalise everything in it, and record it.

    Nothing is enabled by default. A pack that silently added 270 lines to your
    system prompt would be a pack you uninstall in anger — so installation is
    cheap and reversible, and going into the index is a separate, explicit act.
    """
    kinds = set(kinds)
    try:
        _, location = resolve_source(source)
    except ValueError as exc:
        return InstallResult(pack=str(source), error=str(exc))

    pack = slugify(name or Path(location.rstrip("/")).stem or source)
    res = InstallResult(pack=pack, source=location)

    raw_dir = pack_dir(pack) / "src"
    ok, detail = fetch(source, raw_dir)
    if not ok:
        res.error = detail
        return res

    items = [i for i in discover(raw_dir) if i.kind in kinds]
    if not items:
        res.error = "nothing importable found (looked for skills/, agents/, commands/)"
        return res

    dest_root = pack_dir(pack) / "skills"
    shutil.rmtree(dest_root, ignore_errors=True)

    from .skills import SkillRegistry
    existing = {s.name for s in SkillRegistry().all(include_archived=True)}

    manifest: Dict[str, Any] = {}
    for item in items:
        final = item.name
        if final in existing:
            # Namespace rather than skip: the clash is usually two libraries
            # having a `code-review`, and silently dropping one loses content
            # the user explicitly asked for.
            final = f"{pack}-{item.name}"
            res.renamed[item.name] = final
        existing.add(final)

        _write_item(item, dest_root, pack, final)
        res.counts[item.kind] = res.counts.get(item.kind, 0) + 1
        manifest[final] = {
            "kind": item.kind,
            "category": item.category,
            "description": tidy_description(item.description, final),
        }

    reg = _load_registry()
    reg["packs"][pack] = {
        "source": location,
        "installed_at": time.time(),
        "counts": res.counts,
        "items": manifest,
    }
    wanted = {slugify(e) for e in enable if e}
    if wanted:
        promote = sorted(n for n in manifest if n in wanted or
                         manifest[n]["kind"] in wanted or
                         manifest[n]["category"] in wanted)
        reg["enabled"] = sorted(set(reg["enabled"]) | set(promote))
        res.enabled = promote
    _save_registry(reg)
    return res


def remove(pack: str) -> bool:
    pack = slugify(pack)
    reg = _load_registry()
    if pack not in reg["packs"]:
        return False
    names = set(reg["packs"][pack].get("items", {}))
    reg["enabled"] = [n for n in reg["enabled"] if n not in names]
    del reg["packs"][pack]
    _save_registry(reg)
    shutil.rmtree(pack_dir(pack), ignore_errors=True)
    return True


def update(pack: str) -> InstallResult:
    """Re-fetch a pack from its recorded source, keeping what was enabled."""
    pack = slugify(pack)
    reg = _load_registry()
    entry = reg["packs"].get(pack)
    if not entry:
        return InstallResult(pack=pack, error="not installed")
    keep = [n for n in reg["enabled"] if n in entry.get("items", {})]
    res = install(entry["source"], name=pack)
    if not res.error:
        reg = _load_registry()
        reg["enabled"] = sorted(set(reg["enabled"]) | set(keep))
        _save_registry(reg)
        res.enabled = keep
    return res


def installed() -> Dict[str, Dict[str, Any]]:
    return _load_registry()["packs"]


def enabled_names() -> List[str]:
    return list(_load_registry()["enabled"])


def set_enabled(names: Iterable[str], on: bool = True) -> List[str]:
    reg = _load_registry()
    current = set(reg["enabled"])
    touched = []
    for n in names:
        n = slugify(n)
        if on:
            if n not in current:
                current.add(n); touched.append(n)
        elif n in current:
            current.discard(n); touched.append(n)
    reg["enabled"] = sorted(current)
    _save_registry(reg)
    return touched


def catalogue() -> List[Dict[str, Any]]:
    """Every installed item across every pack, for search and listing."""
    reg = _load_registry()
    enabled = set(reg["enabled"])
    out: List[Dict[str, Any]] = []
    for pack, entry in sorted(reg["packs"].items()):
        for name, meta in sorted(entry.get("items", {}).items()):
            out.append({
                "name": name, "pack": pack, "enabled": name in enabled, **meta,
            })
    return out


def search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Substring search over the whole installed catalogue.

    Deliberately simple. This runs against a few hundred short strings, so
    anything cleverer would add a dependency and a failure mode to save
    microseconds nobody will notice.
    """
    words = [w for w in slugify(query).split("-") if w]
    if not words:
        return []
    scored = []
    for item in catalogue():
        hay = f"{item['name']} {item.get('description','')} " \
              f"{item.get('category','')} {item['pack']}".lower()
        hits = sum(1 for w in words if w in hay)
        if not hits:
            continue
        exact = 2 if all(w in item["name"].lower() for w in words) else 0
        scored.append((hits + exact, item))
    scored.sort(key=lambda p: (-p[0], p[1]["name"]))
    return [i for _, i in scored[:limit]]
