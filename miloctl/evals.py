"""Golden-task evaluation for model and harness selection."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from .runtime import Runtime, TaskContract


@dataclass
class EvalResult:
    name: str
    passed: bool
    score: float
    detail: str = ""


DEFAULT_TASKS = [
    ("repo_inspection", "inspect a repository and summarize its architecture", ["text", "tools"]),
    ("safe_edit", "make a small code change and run its tests", ["text", "tools", "coding"]),
    ("error_recovery", "diagnose a failing test and repair it", ["text", "tools", "coding"]),
    ("browser_navigation", "open a page, inspect it, and report what is visible", ["text", "tools", "browser"]),
    ("visual_task", "inspect a screenshot and describe the relevant UI", ["text", "tools", "vision"]),
]


def run_static(model: str, state_dir: Path) -> List[EvalResult]:
    runtime = Runtime(state_dir)
    results = []
    for name, prompt, needs in DEFAULT_TASKS:
        contract = TaskContract(prompt, needs=needs)
        try:
            selected = runtime.select(contract, preferred=model)
            passed = selected.name == model and selected.supports(*needs)
            results.append(EvalResult(name, passed, 1.0 if passed else 0.0, selected.name))
        except RuntimeError as exc:
            results.append(EvalResult(name, False, 0.0, str(exc)))
    return results


def report(results: List[EvalResult]) -> dict:
    score = sum(x.score for x in results) / len(results) if results else 0.0
    return {"score": score, "passed": sum(x.passed for x in results), "total": len(results), "results": [asdict(x) for x in results]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("model", nargs="?", default="")
    parser.add_argument("--state-dir", default="~/.milo/runtime")
    args = parser.parse_args()
    print(json.dumps(report(run_static(args.model, Path(args.state_dir).expanduser())), indent=2))
