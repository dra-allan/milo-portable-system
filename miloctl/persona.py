"""
persona.py — who Milo is, assembled fresh every session.
========================================================

One identity, rendered into whatever format the host tool wants:

* OpenCode      → ``AGENTS.md`` + agent profile
* Claude Code   → ``CLAUDE.md``
* Codex / Cursor→ ``AGENTS.md`` / ``.cursorrules``
* anything else → plain markdown on stdout

The important part is that the persona is **assembled**, not stored. Every
render pulls the live state:

===================  ==========================================================
Layer                Source
===================  ==========================================================
identity             ``assets/identity/IDENTITY.md`` (bundled, editable)
operating rules      bundled, plus vault ``CLAUDE.md`` if present
user model           :mod:`miloctl.profile` — deepens every session
durable memory       :mod:`miloctl.memory` — pinned + top-ranked
skills index         :mod:`miloctl.skills` — what Milo knows how to *do*
environment          :mod:`miloctl.paths` — where things live on THIS machine
===================  ==========================================================

So Milo on a fresh laptop, five minutes after ``milo install``, has the same
mind as Milo on the old machine — because the mind is the state, not the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import paths
from .naming import display_name

__all__ = ["PersonaContext", "build", "identity_text", "write_identity", "DEFAULT_IDENTITY"]


# ── Bundled default identity ──────────────────────────────────────────────────
#
# Kept in code as the fallback so Milo has a personality even before any assets
# are on disk. `milo persona edit` writes it out to $MILO_HOME/identity.md,
# after which the on-disk copy wins.

DEFAULT_IDENTITY = """\
# Milo Sage

You are **Milo Sage** — Allan's assistant and chief of stuff.

**Milo and Mylo are the same name.** Allan spells it both ways and means the
same person every time. Never correct the spelling, never ask which one, never
treat "Mylo" as a different agent. Answer to either without comment.

## Who you are

You are not a chatbot with a name badge. You are the person who holds the
context: what Allan is building, what he decided last week, what is still open,
what he keeps forgetting. You carry that so he doesn't have to.

You have a real memory that survives machines. Use it. When something durable
happens — a decision, a preference, a fact about how Allan works, a fix that
took effort to find — save it. When a conversation starts, recall before you
guess.

## How you speak

- Direct. Lead with the answer, then the reasoning if it's needed.
- No preamble. Never open with "Great question" or "I'd be happy to".
- No filler adjectives. Say what a thing does, not how powerful it is.
- Plain words over jargon. If jargon is the precise word, use it and move on.
- Short paragraphs. Prose over bullet soup, but use structure when structure
  is genuinely the clearer shape.
- No emojis unless Allan uses them first.
- When you disagree, say so plainly and say why. Agreement you don't mean is
  worse than useless.

## How you work

- **Do the thing.** If you have the tools to act, act, then report. Don't
  narrate a plan and stop.
- **Say what you don't know.** Guessing confidently is the one unforgivable
  failure mode. "I don't know, here's how I'd find out" is always allowed.
- **Finish.** A half-applied change is worse than none. If you run out of
  room, say exactly where you stopped and what's left.
- **Check before you claim.** Ran the command? Read the file? If not, say the
  claim is unverified.
- **Learn.** When you work out a procedure worth repeating, save it as a skill
  so the next session starts where this one ended.

## Boundaries

- Never invent a fact about Allan's life, business or code. Recall it or ask.
- Never commit secrets. Config templates use `{{PLACEHOLDER}}` tokens.
- Never hardcode a home directory. Paths come from the environment.
- Destructive operations (delete, force-push, drop) get confirmed first.
"""


# ── Context ───────────────────────────────────────────────────────────────────


@dataclass
class PersonaContext:
    """Everything that goes into a rendered persona, already assembled."""

    identity: str = ""
    environment: str = ""
    user_model: str = ""
    memory: str = ""
    skills: str = ""
    vault: str = ""
    tools: str = ""
    nudges: str = ""
    generated_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    def sections(self) -> List[tuple]:
        return [
            ("identity", self.identity),
            ("environment", self.environment),
            ("user_model", self.user_model),
            ("memory", self.memory),
            ("skills", self.skills),
            ("vault", self.vault),
            ("tools", self.tools),
            ("nudges", self.nudges),
        ]

    def render(self, include: Optional[Sequence[str]] = None) -> str:
        wanted = set(include) if include else None
        parts = [
            body.strip()
            for name, body in self.sections()
            if body.strip() and (wanted is None or name in wanted)
        ]
        who = display_name()
        footer = (
            f"\n---\n*Assembled by {who} {self.generated_at}. "
            f"Regenerate with `milo persona sync`; edit the source with "
            f"`milo persona edit`.*\n"
        )
        return "\n\n".join(parts) + footer

    def approx_tokens(self) -> int:
        return len(self.render()) // 4


# ── Identity source ───────────────────────────────────────────────────────────


def identity_path() -> Path:
    """On-disk identity file. Editable; overrides the bundled default."""
    return paths.milo_home() / "identity.md"


def identity_text() -> str:
    """The identity, preferring the user-edited copy over the built-in one."""
    p = identity_path()
    if p.is_file():
        try:
            text = p.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    bundled = paths.bundled("assets", "identity", "IDENTITY.md")
    if bundled.is_file():
        try:
            text = bundled.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return DEFAULT_IDENTITY


def write_identity(text: str = "") -> Path:
    """Materialise the identity to disk so it can be hand-edited."""
    p = identity_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((text or identity_text()).rstrip() + "\n", encoding="utf-8")
    return p


# ── Section builders ──────────────────────────────────────────────────────────


def _environment_block() -> str:
    who = display_name()
    d = paths.describe()
    lines = [
        "## This machine",
        "",
        f"Platform: **{d.get('platform', 'unknown')}**. "
        f"{who}'s state lives under `{d.get('home')}`.",
        "",
        "| What | Where |",
        "|---|---|",
    ]
    for label, key in (
        ("Milo home", "home"),
        ("Vault (long-term notes)", "vault"),
        ("Workspace", "workspace"),
        ("Skills", "skills"),
        ("Memory DB", "memory_db"),
    ):
        if d.get(key):
            lines.append(f"| {label} | `{d[key]}` |")
    lines += [
        "",
        "Never hardcode any of these into a script or a skill. Read them from "
        "the environment (`MILO_HOME`, `MILO_VAULT_DIR`) or call `milo path <name>`. "
        "Paths differ on every machine; that is the whole point of this system.",
    ]
    return "\n".join(lines)


def _memory_block(query: str = "", budget: int = 14) -> str:
    try:
        from .memory import store
    except Exception:  # pragma: no cover - defensive
        return ""
    try:
        rows = store().context(query, budget=budget)
    except Exception:
        return ""
    if not rows:
        return ""
    lines = [
        "## What you already know",
        "",
        "Durable memory, highest-signal first. Treat it as true unless Allan "
        "contradicts it — then update it with `milo remember`.",
        "",
    ]
    pinned = [m for m in rows if m.pinned]
    rest = [m for m in rows if not m.pinned]
    if pinned:
        lines.append("**Always true:**")
        lines += [f"- {m.content.strip()}" for m in pinned]
        lines.append("")
    if rest:
        by_cat: Dict[str, List] = {}
        for m in rest:
            by_cat.setdefault(m.category, []).append(m)
        for cat in sorted(by_cat):
            lines.append(f"**{cat}:**")
            lines += [f"- {m.content.strip()}" for m in by_cat[cat]]
            lines.append("")
    return "\n".join(lines).rstrip()


def _skills_block() -> str:
    try:
        from .skills import registry
    except Exception:  # pragma: no cover
        return ""
    try:
        index = registry().index()
    except Exception:
        return ""
    if not index.strip():
        return ""
    return (
        index.rstrip()
        + "\n\nRead a skill's SKILL.md before using it. If you work out a "
        "procedure that isn't listed, save it: `milo learn \"<what you did>\"`."
    )


def _profile_block() -> str:
    try:
        from .profile import Profile
    except Exception:  # pragma: no cover
        return ""
    try:
        return Profile().prompt_block()
    except Exception:
        return ""


def _vault_block(budget: int = 6000) -> str:
    try:
        from .vault import vault
    except Exception:  # pragma: no cover
        return ""
    v = vault()
    if not v.exists:
        return ""
    ctx = v.boot_context(budget=budget)
    if not ctx:
        return (
            f"## Vault\n\nLong-term notes live at `{v.root}`. "
            "Search it with `milo vault search <query>`."
        )
    lines = [
        "## Vault",
        "",
        f"Long-term notes live at `{v.root}` (Obsidian, git-backed). "
        "Search with `milo vault search <query>`; capture with "
        "`milo vault capture \"...\"`.",
        "",
    ]
    labels = {
        "identity": "Operating manual (vault copy)",
        "index": "Vault index",
        "handoff": "Where we left off",
        "priorities": "Active priorities",
        "today": "Today's note",
    }
    for key in ("handoff", "priorities", "today", "index"):
        if ctx.get(key):
            lines.append(f"### {labels[key]}")
            lines.append("")
            lines.append(ctx[key].strip())
            lines.append("")
    return "\n".join(lines).rstrip()


def _tools_block() -> str:
    who = display_name()
    return f"""\
## Your own controls

{who} ships a CLI. Use it — it is faster and more reliable than re-deriving
state from files.

| Need | Command |
|---|---|
| Save something durable | `milo remember "..." --category decision` |
| Look something up | `milo recall "<query>"` |
| What do I know about X | `milo about "<name>"` |
| Learn a repeatable procedure | `milo learn "<what you just did>"` |
| List / read skills | `milo skills` · `milo skills show <name>` |
| Search past sessions | `milo sessions search "<query>"` |
| Search long-term notes | `milo vault search "<query>"` |
| Append to today's note | `milo vault daily "..."` |
| Back everything up | `milo backup` |
| Check the system | `milo doctor` |

Both spellings work everywhere: `milo` and `mylo` are the same command."""


def _nudge_block(**signals) -> str:
    if not signals:
        return ""
    try:
        from .learning import NudgeEngine
    except Exception:  # pragma: no cover
        return ""
    try:
        return NudgeEngine().render(**signals).strip()
    except Exception:
        return ""


# ── Assembly ──────────────────────────────────────────────────────────────────


def build(
    *,
    query: str = "",
    memory_budget: int = 14,
    vault_budget: int = 6000,
    include_memory: bool = True,
    include_profile: bool = True,
    include_vault: bool = True,
    include_tools: bool = True,
    signals: Optional[Dict[str, object]] = None,
) -> PersonaContext:
    """Assemble the full persona from live state.

    ``query`` biases memory retrieval toward the current task — pass the user's
    first message and the recalled memories become relevant instead of generic.

    The ``include_*`` switches exist because a persona written to a *config
    file* (``milo sync``) is read once at session start and must not go stale,
    whereas a persona rendered *per turn* wants everything. Turning memory and
    vault off gives the durable half: identity, environment, skills, tools.
    """
    return PersonaContext(
        identity=identity_text(),
        environment=_environment_block(),
        user_model=_profile_block() if include_profile else "",
        memory=_memory_block(query, memory_budget) if include_memory else "",
        skills=_skills_block(),
        vault=_vault_block(vault_budget) if include_vault else "",
        tools=_tools_block() if include_tools else "",
        nudges=_nudge_block(**(signals or {})),
    )
