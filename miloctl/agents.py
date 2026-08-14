"""Crash-safe multi-agent jobs with isolated Git worktrees."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

_SAFE = re.compile(r"^[A-Za-z0-9._/-]+$")


class AgentWorkspace:
    def __init__(self, repo: Path, root: Optional[Path] = None):
        self.repo = Path(repo).resolve()
        self.root = Path(root or self.repo.parent / ".milo-worktrees").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = self.root / "jobs.json"

    def _git(self, *args: str) -> str:
        p = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, timeout=60)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout).strip() or "git command failed")
        return p.stdout.strip()

    def create(self, task: str, base: str = "main") -> Dict[str, Any]:
        if not task.strip() or not _SAFE.fullmatch(base):
            raise ValueError("invalid task or base branch")
        job_id = "agent_" + uuid.uuid4().hex[:12]
        branch = f"milo/{job_id}"
        worktree = self.root / job_id
        self._git("worktree", "add", "-b", branch, str(worktree), base)
        record = {"id": job_id, "task": task, "branch": branch, "worktree": str(worktree), "status": "ready", "created_at": time.time(), "pid": os.getpid()}
        self._write_state(job_id, record)
        return record

    def _write_state(self, job_id: str, record: Dict[str, Any]) -> None:
        try: data = json.loads(self.state.read_text(encoding="utf-8")) if self.state.is_file() else {}
        except (OSError, ValueError): data = {}
        data[job_id] = record
        tmp = self.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state)

    def recover(self) -> Dict[str, int]:
        try: data = json.loads(self.state.read_text(encoding="utf-8")) if self.state.is_file() else {}
        except (OSError, ValueError): data = {}
        recovered = 0
        removed = 0
        for job_id, job in data.items():
            if job.get("status") in {"running", "queued"} and not Path(job.get("worktree", "")).exists():
                job["status"] = "lost"
                job["error"] = "worktree missing after restart"
                recovered += 1
            if job.get("status") == "deleted":
                shutil.rmtree(job.get("worktree", ""), ignore_errors=True)
                removed += 1
        tmp = self.state.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state)
        return {"recovered": recovered, "removed": removed}
