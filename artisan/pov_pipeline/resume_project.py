#!/usr/bin/env python3
"""Resume a POV project from its first incomplete checkpoint."""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def nonempty(project: Path, rel: str) -> bool:
    p = project / rel
    if p.is_file():
        return p.stat().st_size > 0
    return p.is_dir() and any(x.is_file() and x.stat().st_size > 0 for x in p.rglob("*"))

def run(project: Path, stage: str, extra: list[str]) -> int:
    cmd = [sys.executable, str(HERE / "run_pov_pipeline.py"), "--project", project.name, "--stage", stage]
    cmd.extend(extra)
    print("[resume]", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(HERE), env=os.environ.copy()).returncode

def main() -> int:
    ap = argparse.ArgumentParser(description="Resume POV at the first missing checkpoint")
    ap.add_argument("project", nargs="?", help="project folder name; omit to use newest incomplete project")
    ap.add_argument("--flow-profiles", default=os.getenv("POV_FLOW_PROFILES", ""))
    ap.add_argument("--flow-browser-profile", default=os.getenv("POV_FLOW_BROWSER_PROFILE", ""))
    args = ap.parse_args()
    try:
        import povconfig
        root = povconfig.projects_dir()
    except Exception:
        root = Path(os.getenv("POV_PROJECTS_DIR", str(HERE / "projects"))).expanduser()
    if args.project:
        project = root / args.project
    else:
        candidates = [p for p in root.iterdir() if p.is_dir() and (p / "state" / "manifest.json").exists()] if root.exists() else []
        incomplete = [p for p in candidates if not nonempty(p, "output_pro")]
        if not incomplete:
            print("[resume] no incomplete POV project found")
            return 1
        project = max(incomplete, key=lambda p: p.stat().st_mtime)
    if not project.is_dir():
        print(f"[resume] project not found: {project}", file=sys.stderr)
        return 2
    extra = ["--flow-profiles", args.flow_profiles] if args.flow_profiles else []
    if args.flow_browser_profile:
        extra += ["--flow-browser-profile", args.flow_browser_profile]
    agents = ["00_RESEARCH_NOTES.txt", "01_SCRIPT_RAW.txt", "05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt", "04_THUMBNAIL/THUMBNAIL_PROMPT.txt", "02_SCRIPT_ELEVENLABS.txt", "07_METADATA.txt", "COMPLETENESS_REPORT.txt"]
    if not all(nonempty(project, f) for f in agents):
        rc = run(project, "agents", extra)
        if rc: return rc
    if not nonempty(project, "06_AUDIO"):
        rc = run(project, "tts", extra)
        if rc: return rc
    # Always invoke images on resume. The image stage is itself checkpointed and skips completed files.
    rc = run(project, "images", extra)
    if rc: return rc
    if not nonempty(project, "04_THUMBNAIL/thumbnail.png"):
        rc = run(project, "thumb", extra)
        if rc: return rc
    if not nonempty(project, "output_pro"):
        rc = run(project, "assemble", extra)
        if rc: return rc
    print(f"[resume] checkpoints complete: {project.name}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
