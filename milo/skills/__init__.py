"""
milo.skills — the growth loop.

Skills are how Milo turns experience into capability. They use the
agentskills.io / Hermes ``SKILL.md`` layout, so anything written here also
loads in Hermes, Claude Code, or any other harness that speaks the standard.

``manager.py``  discover / validate / create / edit skills
``curator.py``  age, retire and consolidate the skills Milo wrote itself
"""

from .curator import Curator, CuratorConfig, CuratorReport
from .manager import Skill, SkillError, SkillManager

__all__ = [
    "Skill",
    "SkillError",
    "SkillManager",
    "Curator",
    "CuratorConfig",
    "CuratorReport",
]
