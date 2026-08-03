"""
milo.memory — Milo's three-tier memory.

    hot      brain.sqlite      append-everything, FTS5-searchable   store.py
    curated  MEMORY.md/USER.md bounded, injected into every prompt   curated.py
    cold     the Obsidian vault human-readable, git-backed           vault.py

``engram.py`` is the migration bridge off the old Engram database.
"""

from .curated import CuratedMemory
from .store import Brain, Observation, KINDS, SCOPES
from .vault import Vault, VaultLayout

__all__ = [
    "Brain",
    "Observation",
    "KINDS",
    "SCOPES",
    "CuratedMemory",
    "Vault",
    "VaultLayout",
]
