#!/usr/bin/env python3
"""Resume a POV project from its first incomplete checkpoint."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECTED = [
    ("agents", ["00_RESEARCH_NOTES.txt", "01_SCRIPT_RAW.txt", "05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt", "04_THUMBNAIL/THUMBNAIL_PROMPT.txt", "02_SCRIPT_ELEVENLABS.txt", "07_METADATA.txt", "COMPLETENESS_REPORT.txt"]),
    ("tts", ["06_AUDIO"]),
    ("images", ["05_IMAGES"]),
    ("thumb", ["04_THUMBNAIL/thumbnail.png"]),
    ("assemble", ["output_pro"]),
]

def present(project: Path, rel: str) -> bool:
    p = project / rel
    if not p.exists():
        return False
    if p.is_file():
        return p.stat().st_size > 0
    return any(x.is_file() and x.stat().st_size > 0 for x in p.rglob("*"))

def run(project: Path, stage: str, extra: list[str]) -> int:
    cmd = [sys.executable, str(HERE / "run_pov_pipeline.py"), "--project", project.name, "--stage", stage]
    cmd.extend(extra)
    print("[resume]", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(HERE), env=os.environ.copy()).returncode

def main() -> int:
    ap = argparse.ArgumentParser(description="Resume POV at the first missing checkpoint")
    ap.add_argument("project", nargs="?", help="project folder name; omit to use newest incomplete project")
    ap.add_argument("--flow-profiles", default=os.getenv("POV_FLOW_PROFILES", ""))
    ap.add_argument("--skip-upload", action="store_true")
    args = ap.parse_args()
    root = Path(os.getenv("POV_PROJECTS_DIR", str(HERE / "projects"))).expanduser()
    if args.project:
        project = root / args.project
    else:
        candidates = [p for p in root.iterdir() if p.is_dir() and (p / "state" / "manifest.json").exists()] if root.exists() else []
        incomplete = [p for p in candidates if not present(p, "COMPLETENESS_REPORT.txt") or not present(p, "output_pro")]
        if not incomplete:
            print("[resume] no incomplete POV project found")
            return 1
        project = max(incomplete, key=lambda p: p.stat().st_mtime)
    if not project.is_dir():
        print(f"[resume] project not found: {project}", file=sys.stderr)
        return 2
    extra = (["--flow-profiles", args.flow_profiles] if args.flow_profiles else [])
    for stage, files in EXPECTED:
        if not all(present(project, f) for f in files):
            rc = run(project, stage, extra)
            if rc:
                print(f"[resume] stopped at {stage} (exit {rc})", file=sys.stderr)
                return rc
    print(f"[resume] checkpoints complete: {project.name}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
