"""
naming.py — Milo/Mylo identity normalisation.
=============================================

Allan writes "Milo" and "Mylo" interchangeably. So does everyone else once
autocorrect gets involved. This module is the single place that decides
"is this word referring to the assistant?" — every CLI entrypoint, command
router, agent-name resolver and persona loader goes through here.

Rules
-----
* ``milo`` and ``mylo`` are the same entity, always.
* Case never matters.
* Common typos and separator styles are folded: ``Milo-Sage``, ``mylo_sage``,
  ``MILO SAGE``, ``m1lo`` all resolve to the canonical ``milo``.
* Canonical display name is configurable (``MILO_DISPLAY_NAME``) but defaults
  to ``Milo``.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Iterable, Optional

# ── Canonical identity ────────────────────────────────────────────────────────

CANONICAL = "milo"
DISPLAY_DEFAULT = "Milo"

#: Every spelling that means "the assistant". Keep lowercase, no separators.
ALIASES: set[str] = {
    "milo",
    "mylo",
    "myllo",
    "millo",
    "m1lo",
    "mi1o",
    "milosage",
    "mylosage",
    "milo-sage",
    "mylo-sage",
    "milobot",
    "mylobot",
    "milosbot",
    "jarvis",  # legacy nickname from the old README ("Just A Rather Very...")
}

#: Aliases for the *agent profile* name used by OpenCode / Claude Code.
AGENT_ALIASES: set[str] = {"milo", "mylo", "milo-sage", "mylo-sage", "sage"}

_SEPARATORS = re.compile(r"[\s_\-.]+")
_NONWORD = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    """Lowercase, strip accents, collapse separators — comparison form."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.strip().lower()
    text = _SEPARATORS.sub("", text)
    return _NONWORD.sub("", text)


def is_milo(name: Optional[str]) -> bool:
    """True when ``name`` refers to the assistant, whatever the spelling."""
    if not name:
        return False
    folded = _fold(name)
    if not folded:
        return False
    if folded in {_fold(a) for a in ALIASES}:
        return True
    # "milo sage", "mylo the assistant", "ask-milo" → prefix/contains match on
    # the two real stems only (never the fuzzy typos, which would over-match).
    return folded.startswith("milo") or folded.startswith("mylo")


def canonical(name: Optional[str] = None) -> str:
    """Return the canonical lowercase id for any Milo spelling.

    Non-Milo names pass through folded but unchanged in meaning, so this is
    safe to use as a generic slugifier for agent names too.
    """
    if name is None:
        return CANONICAL
    if is_milo(name):
        return CANONICAL
    folded = _SEPARATORS.sub("-", (name or "").strip().lower())
    return _NONWORD.sub("-", folded).strip("-") or CANONICAL


def display_name() -> str:
    """Human-facing name. Override with ``MILO_DISPLAY_NAME=Mylo``."""
    return os.environ.get("MILO_DISPLAY_NAME", "").strip() or DISPLAY_DEFAULT


def normalise_text(text: str, to: Optional[str] = None) -> str:
    """Rewrite every Milo/Mylo spelling inside free text to one spelling.

    Used when rendering the persona file so the agent sees a consistent name
    no matter which way the source material spelled it.
    """
    target = to or display_name()
    pattern = re.compile(r"\b(m[iy]l+o)\b", re.IGNORECASE)

    def _sub(match: re.Match[str]) -> str:
        word = match.group(0)
        if word.isupper():
            return target.upper()
        if word[0].isupper():
            return target[0].upper() + target[1:]
        return target.lower()

    return pattern.sub(_sub, text)


def match_command(word: str, candidates: Iterable[str]) -> Optional[str]:
    """Resolve ``word`` against ``candidates`` with Milo-aware fuzziness.

    Order of attempts: exact → folded-exact → unique prefix. Returns the
    matched candidate or ``None``.
    """
    word = (word or "").strip()
    cands = list(candidates)
    if word in cands:
        return word
    folded = _fold(word)
    if not folded:
        return None
    exact = [c for c in cands if _fold(c) == folded]
    if len(exact) == 1:
        return exact[0]
    prefix = [c for c in cands if _fold(c).startswith(folded)]
    if len(prefix) == 1:
        return prefix[0]
    return None


def agent_name(name: Optional[str] = None) -> str:
    """Agent-profile name for OpenCode/Claude Code (``milo`` unless custom)."""
    if name and not is_milo(name):
        return canonical(name)
    return CANONICAL
