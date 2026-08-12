"""Unified Milo runtime contract.

Keeps model routing, tool permissions, lifecycle learning, and task outcomes
outside vendor-specific harnesses. Harnesses remain transport adapters; this
module is the stable behavior layer.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class ModelProfile:
    name: str
    provider: str = ""
    capabilities: List[str] = field(default_factory=list)
    context_window: int = 0
    quality: float = 0.0
    tool_score: float = 0.0
    vision_score: float = 0.0
    latency_ms: int = 0
    enabled: bool = True
    notes: str = ""

    def supports(self, *required: str) -> bool:
        return all(x in self.capabilities for x in required)


@dataclass
class TaskContract:
    task: str
    needs: List[str] = field(default_factory=list)
    risk: str = "normal"
    max_steps: int = 40
    require_tests: bool = True
    require_approval: bool = False


@dataclass
class RunEvent:
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Runtime:
    """Provider-neutral runtime state and routing policy.

    This deliberately does not call a model. OpenCode, Claude Code, Codex and
    future adapters ask it which profile and capabilities are appropriate, then
    report events/results back through the same contract.
    """

    DEFAULT_PROFILES = [
        ModelProfile("deepseek-chat", "deepseek", ["text", "tools", "coding"], quality=0.72, tool_score=0.76),
        ModelProfile("claude-opus", "anthropic", ["text", "tools", "coding", "vision"], quality=0.90, tool_score=0.91, vision_score=0.92),
        ModelProfile("gpt-5-codex", "openai", ["text", "tools", "coding", "vision"], quality=0.91, tool_score=0.94, vision_score=0.88),
        ModelProfile("gemini-pro", "google", ["text", "tools", "coding", "vision"], quality=0.84, tool_score=0.78, vision_score=0.90),
    ]

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir or os.environ.get("MILO_RUNTIME_DIR", "~/.milo/runtime")).expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.events: List[RunEvent] = []
        self.profiles = self._load_profiles()

    @property
    def profiles_path(self) -> Path:
        return self.state_dir / "models.json"

    def _load_profiles(self) -> List[ModelProfile]:
        if self.profiles_path.is_file():
            try:
                return [ModelProfile(**x) for x in json.loads(self.profiles_path.read_text())]
            except (OSError, ValueError, TypeError):
                pass
        return list(self.DEFAULT_PROFILES)

    def save(self) -> None:
        self.profiles_path.write_text(json.dumps([asdict(p) for p in self.profiles], indent=2) + "\n")

    def select(self, contract: TaskContract, preferred: str = "") -> ModelProfile:
        candidates = [p for p in self.profiles if p.enabled and p.supports(*contract.needs)]
        if preferred:
            exact = [p for p in candidates if p.name == preferred]
            if exact:
                return exact[0]
        if not candidates:
            raise RuntimeError("no enabled model satisfies: " + ", ".join(contract.needs))
        # Tool reliability beats raw benchmark quality for agent tasks.
        return max(candidates, key=lambda p: (p.tool_score * 0.55 + p.quality * 0.35 + p.vision_score * 0.10))

    def emit(self, kind: str, **data: Any) -> RunEvent:
        event = RunEvent(kind, data)
        self.events.append(event)
        with (self.state_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(event)) + "\n")
        return event

    def learn(self, task: str, model: str, success: bool, score: float, lesson: str = "") -> None:
        """Update routing evidence after every run, not only on explicit learn calls."""
        for profile in self.profiles:
            if profile.name == model:
                weight = 0.08
                profile.quality = max(0.0, min(1.0, profile.quality * (1 - weight) + score * weight))
                if not success:
                    profile.tool_score = max(0.0, profile.tool_score - 0.03)
                break
        self.save()
        self.emit("learned", task=task, model=model, success=success, score=score, lesson=lesson)

    def finish(self, contract: TaskContract, model: str, success: bool, tests_passed: bool = False, **data: Any) -> None:
        self.emit("task_finished", task=contract.task, model=model, success=success, tests_passed=tests_passed, **data)
        self.learn(contract.task, model, success, 1.0 if success and tests_passed else 0.35 if success else 0.0, data.get("lesson", ""))


def contract_for(task: str, *, computer: bool = False, vision: bool = False, destructive: bool = False) -> TaskContract:
    needs = ["text", "tools"]
    if computer:
        needs.append("browser")
    if vision:
        needs.append("vision")
    return TaskContract(task, needs=needs, risk="high" if destructive else "normal", require_approval=destructive)
