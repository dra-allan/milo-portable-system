"""
profile.py — the deepening model of who Allan is.
=================================================

Hermes uses Honcho for dialectic user modelling. That's a hosted service and
another dependency, so Milo does the same job locally: a structured,
confidence-weighted, evidence-linked profile that grows every session and
gets injected into the system prompt.

The difference between "an assistant with memory" and "an assistant that
knows you" is that the second one keeps a *model*, not just a log. Facts get
reinforced when repeated, decay when contradicted, and carry the evidence
that produced them.

Stored at ``$MILO_HOME/state/profile.json`` and exported into the vault as
markdown so it is human-reviewable and git-diffable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import paths
from .naming import display_name

# Sections of the model. Order matters — this is prompt-injection order.
SECTIONS = (
    "identity",      # name, role, location, timezone, languages
    "working_style", # how Allan wants work done
    "preferences",   # tools, formats, tone
    "projects",      # active workstreams
    "people",        # who matters and how
    "constraints",   # hardware, time, budget, access
    "goals",         # what he's driving at
    "avoid",         # things that annoy him — hard rules
)

SECTION_LABELS = {
    "identity": "Identity",
    "working_style": "Working style",
    "preferences": "Preferences",
    "projects": "Active projects",
    "people": "People",
    "constraints": "Constraints",
    "goals": "Goals",
    "avoid": "Never do this",
}


@dataclass
class Trait:
    """One belief about the user, with the evidence behind it."""

    key: str
    value: str
    section: str = "preferences"
    confidence: float = 0.6
    observations: int = 1
    evidence: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source: str = "observed"   # observed | stated | inferred | imported | learned

    def reinforce(self, evidence: str = "", weight: float = 0.15) -> None:
        """Seeing the same thing again raises confidence, asymptotic to 1.0."""
        self.observations += 1
        self.confidence = min(0.99, self.confidence + weight * (1 - self.confidence))
        self.updated_at = time.time()
        if evidence and evidence not in self.evidence:
            self.evidence.append(evidence)
            del self.evidence[:-5]  # keep the 5 most recent

    def contradict(self, new_value: str, evidence: str = "") -> None:
        """A contradiction doesn't delete the trait — it lowers confidence and
        swaps the value once confidence drops through the floor."""
        self.confidence = max(0.05, self.confidence - 0.35)
        self.updated_at = time.time()
        if evidence:
            self.evidence.append(f"contradicted: {evidence}")
            del self.evidence[:-5]
        if self.confidence <= 0.3:
            self.value = new_value
            self.confidence = 0.55
            self.observations = 1

    def decayed_confidence(self, now: Optional[float] = None) -> float:
        """Stated facts don't decay. Observations do, slowly (180-day scale)."""
        if self.source == "stated":
            return self.confidence
        now = now or time.time()
        age_days = (now - self.updated_at) / 86400.0
        return self.confidence * (0.5 ** (age_days / 180.0))

    def line(self) -> str:
        conf = self.decayed_confidence()
        marker = "" if conf >= 0.75 else (" (likely)" if conf >= 0.45 else " (unsure)")
        return f"{self.value}{marker}"


class Profile:
    """Load / mutate / render the user model."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or paths.profile_file())
        self.traits: Dict[str, Trait] = {}
        self.meta: Dict[str, Any] = {"created_at": time.time(), "sessions": 0}
        self.load()

    # -- persistence -----------------------------------------------------------

    def load(self) -> "Profile":
        if not self.path.is_file():
            return self
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self
        self.meta = data.get("meta", self.meta)
        for raw in data.get("traits", []):
            fields = Trait.__dataclass_fields__  # type: ignore[attr-defined]
            try:
                t = Trait(**{k: v for k, v in raw.items() if k in fields})
                self.traits[t.key] = t
            except TypeError:
                continue
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Serialisable form. Deterministic order so git diffs stay readable."""
        return {
            "meta": {**self.meta, "updated_at": time.time()},
            "traits": [asdict(t) for t in sorted(
                self.traits.values(), key=lambda t: (t.section, t.key)
            )],
        }

    def merge_dict(self, data: Dict[str, Any]) -> "Profile":
        """Merge a serialised profile in — used by ``milo restore``.

        Conflicts resolve by *evidence*, not recency: the trait observed more
        times wins, and confidences combine rather than overwrite. Restoring an
        older snapshot therefore cannot wipe out what this machine has learned
        since.
        """
        fields = Trait.__dataclass_fields__  # type: ignore[attr-defined]
        for raw in data.get("traits", []):
            try:
                incoming = Trait(**{k: v for k, v in raw.items() if k in fields})
            except TypeError:
                continue
            current = self.traits.get(incoming.key)
            if current is None:
                self.traits[incoming.key] = incoming
                continue
            if incoming.observations > current.observations:
                incoming.observations += current.observations
                incoming.confidence = max(incoming.confidence, current.confidence)
                self.traits[incoming.key] = incoming
            else:
                current.observations += incoming.observations
                current.confidence = max(current.confidence, incoming.confidence)
        meta = data.get("meta", {})
        self.meta["sessions"] = max(
            int(self.meta.get("sessions", 0) or 0),
            int(meta.get("sessions", 0) or 0),
        )
        self.meta.setdefault("created_at", meta.get("created_at", time.time()))
        return self

    def save(self) -> Path:
        paths.ensure(self.path.parent)
        self.path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.path

    # -- mutation --------------------------------------------------------------

    def observe(
        self,
        key: str,
        value: str,
        *,
        section: str = "preferences",
        source: str = "observed",
        evidence: str = "",
        confidence: Optional[float] = None,
    ) -> Trait:
        """Record something about the user. Repeats reinforce, changes contradict."""
        key = key.strip().lower().replace(" ", "_")
        section = section if section in SECTIONS else "preferences"
        value = (value or "").strip()
        existing = self.traits.get(key)

        if existing is None:
            base = confidence if confidence is not None else (
                0.9 if source == "stated" else 0.6
            )
            t = Trait(key=key, value=value, section=section, confidence=base,
                      source=source, evidence=[evidence] if evidence else [])
            self.traits[key] = t
            return t

        if existing.value.strip().lower() == value.lower():
            existing.reinforce(evidence)
        else:
            existing.contradict(value, evidence)
        if source == "stated":
            existing.source = "stated"
            existing.value = value
            existing.confidence = max(existing.confidence, 0.9)
        existing.section = section or existing.section
        return existing

    def forget(self, key: str) -> bool:
        return self.traits.pop(key.strip().lower().replace(" ", "_"), None) is not None

    def get(self, key: str) -> Optional[Trait]:
        return self.traits.get(key.strip().lower().replace(" ", "_"))

    def by_section(self, min_confidence: float = 0.25) -> Dict[str, List[Trait]]:
        out: Dict[str, List[Trait]] = {s: [] for s in SECTIONS}
        for t in self.traits.values():
            if t.decayed_confidence() >= min_confidence:
                out.setdefault(t.section, []).append(t)
        for section in out:
            out[section].sort(key=lambda t: -t.decayed_confidence())
        return {k: v for k, v in out.items() if v}

    def note_session(self) -> None:
        self.meta["sessions"] = int(self.meta.get("sessions", 0)) + 1
        self.meta["last_session_at"] = time.time()

    # -- rendering -------------------------------------------------------------

    def user_name(self) -> str:
        t = self.get("name")
        return t.value if t else "the user"

    def prompt_block(self, max_per_section: int = 8,
                     min_confidence: float = 0.35) -> str:
        """The block injected into every system prompt."""
        sections = self.by_section(min_confidence)
        if not sections:
            return ""
        who = self.user_name()
        lines = [
            f"## What {display_name()} knows about {who}",
            "",
            "Treat these as working beliefs, not gospel. Items marked (likely) or",
            "(unsure) are inferences — confirm before acting on them in a way that",
            "would be annoying to get wrong.",
            "",
        ]
        for section in SECTIONS:
            traits = sections.get(section)
            if not traits:
                continue
            lines.append(f"**{SECTION_LABELS[section]}**")
            for t in traits[:max_per_section]:
                lines.append(f"- {t.line()}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def markdown(self) -> str:
        """Full human-readable export for the vault."""
        lines = [
            "---",
            "tags: [milo, profile, generated]",
            f"generated: {time.strftime('%Y-%m-%d %H:%M')}",
            "---",
            "",
            f"# User Profile — as modelled by {display_name()}",
            "",
            f"Sessions observed: {self.meta.get('sessions', 0)}  ",
            f"Traits tracked: {len(self.traits)}",
            "",
            "> Generated by `milo profile export`. Correct it with",
            "> `milo profile set <key> \"<value>\" --stated` — stated facts",
            "> outrank anything Milo inferred and never decay.",
            "",
        ]
        for section in SECTIONS:
            traits = [t for t in self.traits.values() if t.section == section]
            if not traits:
                continue
            traits.sort(key=lambda t: -t.decayed_confidence())
            lines.append(f"## {SECTION_LABELS[section]}")
            lines.append("")
            lines.append("| Trait | Value | Confidence | Seen | Source |")
            lines.append("|---|---|---|---|---|")
            for t in traits:
                lines.append(
                    f"| `{t.key}` | {t.value} | {t.decayed_confidence():.0%} | "
                    f"{t.observations} | {t.source} |"
                )
            lines.append("")
        return "\n".join(lines)

    def export_markdown(self, out_path: Optional[Path] = None) -> Path:
        out = Path(out_path or (paths.vault_dir() / "09 - Personal" /
                                f"{display_name()} — User Profile.md"))
        paths.ensure(out.parent)
        out.write_text(self.markdown(), encoding="utf-8")
        return out

    def stats(self) -> Dict[str, Any]:
        confident = sum(1 for t in self.traits.values()
                        if t.decayed_confidence() >= 0.75)
        return {
            "path": str(self.path),
            "traits": len(self.traits),
            "confident": confident,
            "sessions": self.meta.get("sessions", 0),
            "sections": {s: len(v) for s, v in self.by_section(0.0).items()},
        }


# ── Bootstrapping from .env / first run ───────────────────────────────────────

def seed_from_env(profile: Optional[Profile] = None) -> Profile:
    """Fill in what we already know from ``.env`` — no interrogation needed."""
    from . import env as envmod

    p = profile or Profile()
    data = envmod.load()
    if data.get("MILO_USER_NAME"):
        p.observe("name", data["MILO_USER_NAME"], section="identity", source="stated")
    if data.get("GITHUB_USER"):
        p.observe("github", data["GITHUB_USER"], section="identity", source="stated")
    if data.get("TELEGRAM_CHAT_ID"):
        p.observe("telegram", "reachable on Telegram", section="identity",
                  source="stated")
    p.observe("platform", f"works on {paths.platform_id()}", section="constraints",
              source="observed", evidence="detected at install")
    p.save()
    return p


def extraction_prompt(transcript_excerpt: str = "") -> str:
    """Prompt Milo runs at session end to update its model of the user.

    Deliberately conservative: it is far worse to confidently learn something
    wrong about someone than to learn nothing.
    """
    body = (
        f"\nSESSION EXCERPT\n---------------\n{transcript_excerpt}\n"
        if transcript_excerpt else
        "\nUse this conversation as the source.\n"
    )
    keys = ", ".join(SECTIONS)
    return f"""[profile] Update your model of the user from this session.
{body}
Extract ONLY things that will still be true next month. Skip anything
situational ("wants the bug fixed today"). Be conservative — confidently
learning something wrong about someone is worse than learning nothing.

For each item, run:
    milo profile set <key> "<value>" --section <section> --source <source>

  section: one of {keys}
  source:  `stated` if they said it outright, `inferred` if you deduced it, `learned` if learned through experience

Good:
    milo profile set tone "wants direct answers, no preamble" \\
        --section working_style --source inferred
    milo profile set timezone "East Africa Time (UTC+3)" \\
        --section identity --source stated

Bad (too situational, or a guess dressed as fact):
    milo profile set mood "frustrated today"
    milo profile set expertise "probably a senior engineer"

If nothing durable came up, say "nothing new" and stop."""


def build_extract_prompt(transcript_excerpt: str = "", *, existing: Optional[Sequence[str]] = None) -> str:
    """Build the extraction prompt for updating the user model from conversation.

    Similar to build_learn_prompt but for profile extraction.
    """
    # Get recent traits to provide context
    profile = Profile()
    recent_traits = []
    for trait in profile.traits.values():
        if trait.decayed_confidence() > 0.5:  # Only reasonably confident traits
            recent_traits.append(f"{trait.key}: {trait.value}")

    known = ""
    if recent_traits:
        known = (
            "\nKnown traits (update or contradict these if needed):\n" +
            "\n".join(f"  - {t}" for t in recent_traits[:10]) + "\n"
        )

    return f"""[profile] Update your model of the user from this session.

PROVIDED CONTEXT
----------------
{extraction_prompt(transcript_excerpt)}
{known}
IMPORTANT: When setting traits, use the --source flag appropriately:
- --source stated: User explicitly stated this fact
- --source inferred: You deduced this from behavior/context
- --source learned: Learned through repeated observation/experience
"""


def run_extraction(transcript_excerpt: str = "", *, with_harness: str = "", model: str = "") -> int:
    """Run profile extraction through the available agent harness.

    Returns exit code from the harness (0 for success).
    """
    from . import harness
    from .cli_extra import _run_through_harness

    prompt = build_extract_prompt(transcript_excerpt)

    if with_harness:
        h = harness.get_harness(with_harness)
        if h is None:
            # Fallback to auto-detect
            installed = harness.detect_installed()
            runnable = [x for x in installed if x.which()]
            if not runnable:
                print("No agent runtime found — printing extraction prompt instead")
                print(prompt)
                return 1
            h = runnable[0]
    else:
        # Auto-detect harness
        installed = harness.detect_installed()
        runnable = [x for x in installed if x.which()]
        if not runnable:
            print("No agent runtime found — printing extraction prompt instead")
            print(prompt)
            return 1
        # Prefer the same heuristic as _run_through_harness
        h = runnable[0]

    print(f"Running profile extraction through {h.name}...")
    code, out = h.run(prompt, model=model or "")
    print(out)
    return code
