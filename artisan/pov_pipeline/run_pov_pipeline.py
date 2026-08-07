#!/usr/bin/env python3
"""
POV Pipeline Orchestrator — URL to POV project + TTS in one command.

Usage:
  python run_pov_pipeline.py <youtube_url|transcript_file> [--name TITLE] [--skip-tts]

What it does:
  1. Scrapes the transcript from a YouTube URL (or reads a transcript file)
     into the project folder as 00_SOURCE_SCRIPT.txt.
  2. Runs the 7 POV agents in order as subagents (the agent .md files are
     the prompts). Each agent reads the project files and writes its stage
     output. Stage outputs are checked for existence before the next agent
     runs.
  3. Runs the SCRIPT GATE (rewrite-originality + wordcount) before TTS.
  4. Auto-runs Gemini TTS (voice Fenrir) to generate 06_AUDIO/<SEG>.mp3.
  5. Stops with a handoff report. YOU still generate images + thumbnail
     (Google Flow) from the prompts, then run the assembler.

Exit codes:
  0 = success
  1 = error
  2 = usage error
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = ROOT / "agents"
TTS_DIR = ROOT / "tts"
SCRIPTS_DIR = ROOT / "scripts"
PROJECTS_DIR = Path(r"C:\Users\user\Desktop\Milo Video Factory\pov\projects")

# Agent order + output file each one must produce.
PIPELINE_AGENTS = [
    ("POV-researcher",       "00_RESEARCH_NOTES.txt"),
    ("POV-scriptwriter",     "01_SCRIPT_RAW.txt"),
    ("POV-image-director",   "05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt"),
    ("POV-thumbnail-artist", "04_THUMBNAIL/THUMBNAIL_PROMPT.txt"),
    ("POV-voice-engineer",   "02_SCRIPT_ELEVENLABS.txt"),
    ("POV-seo-specialist",   "07_METADATA.txt"),
    ("POV-archive-manager",  "COMPLETENESS_REPORT.txt"),
]

WORD_BUDGET = (1620, 2025)  # short-form: 12-15 min at 135 WPM
OVERLAP_SCAN = 6            # matching word-run that triggers a flag


def eprint(*a, **kw):
    print(*a, **kw, file=sys.stderr)


def now_stamp():
    return datetime.now().strftime("%Y%m%d")


def make_project_name(url_or_name: str) -> str:
    """Create a slug from a video id or a provided title."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})", url_or_name)
    if m:
        return f"{m.group(1)}_{now_stamp()}"
    slug = re.sub(r"[^A-Za-z0-9]+", "_", url_or_name).strip("_")[:40]
    return f"{slug or 'POV'}_{now_stamp()}"


def scrape_transcript(url: str, project_dir: Path) -> Path:
    """Scrape a YouTube transcript. No video download."""
    scraper = SCRIPTS_DIR / "youtube-transcript.cjs"
    if not scraper.exists():
        sys.exit(f"[error] Scraper not found: {scraper}")
    node = shutil.which("node")
    if not node:
        sys.exit("[error] node not on PATH (needed for the transcript scraper)")

    src = project_dir / "00_SOURCE_SCRIPT.txt"
    print(f"[scrape] {url}")
    result = subprocess.run(
        [node, str(scraper), url, "en"],
        capture_output=True, text=True, timeout=180,
    )
    # The scraper writes the transcript to stdout and a save note to stderr.
    text = result.stdout.strip()
    if not text and result.returncode == 0:
        # Some node installs echo everything to stderr; fall back to it.
        text = result.stderr.strip()
    # Strip the "[saved] ..." preamble if it leaked into stdout capture.
    text = re.sub(r"^\[saved\][^\n]*\n?", "", text).strip()

    if not text or result.returncode != 0:
        eprint("[error] Transcript scrape failed:")
        eprint((result.stderr or result.stdout or "no output")[:600])
        return None

    src.write_text(text, encoding="utf-8")
    print(f"[scrape] OK — {len(text)} chars -> {src.relative_to(ROOT)}")
    return src


def copy_transcript_file(path: Path, project_dir: Path) -> Path:
    src = project_dir / "00_SOURCE_SCRIPT.txt"
    shutil.copyfile(path, src)
    print(f"[input] Transcript copied -> {src.relative_to(ROOT)}")
    return src


def run_agents(project_dir: Path):
    """Run the 7 agents. Each agent is dispatched by the orchestrator CLI
    (this script prints the prompt to run); in OpenCode the caller runs each
    agent .md as a subagent and the file lands in project_dir."""
    print("\n" + "=" * 60)
    print("  POV AGENT CHAIN (7 stages)")
    print("=" * 60)
    for i, (agent, outfile) in enumerate(PIPELINE_AGENTS, 1):
        prompt_path = AGENTS_DIR / f"{agent}.md"
        target = project_dir / outfile
        print(f"\n[{i}/7] {agent} -> {outfile}")
        if target.exists():
            print(f"      already present ({target.stat().st_size} bytes), skipping")
            continue
        if not prompt_path.exists():
            eprint(f"      [warn] agent prompt missing: {prompt_path}")
            continue
        print(f"      prompt: {prompt_path.name}")
        print(f"      status: WAITING for agent run (write {outfile} into the project)")
        print(f"      project: {project_dir}")


def script_gate(project_dir: Path) -> bool:
    """SCRIPT GATE: wordcount + rewrite-originality. Cheap check BEFORE TTS."""
    print("\n" + "=" * 60)
    print("  SCRIPT GATE")
    print("=" * 60)
    script_path = project_dir / "01_SCRIPT_RAW.txt"
    source_path = project_dir / "00_SOURCE_SCRIPT.txt"
    ok = True

    # 1. Wordcount (body segments only — narration text, not manifest or segment headers)
    if not script_path.exists():
        eprint("[gate] FAIL — 01_SCRIPT_RAW.txt missing")
        return False
    raw = script_path.read_text(encoding="utf-8")
    body = raw.split("=== END MANIFEST ===")[-1]
    # Count only actual narration lines: skip [NAR-###] markers, [VOICE...] markers,
    # empty lines, and the title card "POV-... The Listener."
    narration_lines = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[NAR-"):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if line.startswith("POV-") and "The Listener" in line:
            continue
        narration_lines.append(line)
    narration_text = " ".join(narration_lines)
    words = len(re.findall(r"[A-Za-z0-9']+", narration_text))
    lo, hi = WORD_BUDGET
    print(f"[gate] wordcount (narration only): {words} (target {lo}-{hi})")
    if not (lo <= words <= hi):
        eprint(f"[gate] FAIL — outside budget. Expand/cut then re-run.")
        ok = False

    # 2. Rewrite-originality (only if a source exists).
    if source_path.exists():
        source = re.sub(r"[^A-Za-z0-9' ]+", " ", source_path.read_text(encoding="utf-8")).lower()
        source_tokens = source.split()
        n = OVERLAP_SCAN
        source_ngrams = {
            " ".join(source_tokens[i:i+n])
            for i in range(len(source_tokens) - n + 1)
        } if len(source_tokens) >= n else set()

        body_clean = re.sub(r"[^A-Za-z0-9' ]+", " ", body).lower()
        body_tokens = body_clean.split()
        hits = []
        for i in range(len(body_tokens) - n + 1):
            gram = " ".join(body_tokens[i:i+n])
            if gram in source_ngrams:
                hits.append(gram)
        hits = list(dict.fromkeys(hits))  # dedupe, keep order
        print(f"[gate] rewrite overlap: {len(hits)} matching {n}-word runs")
        if len(hits) >= 4:
            eprint(f"[gate] FAIL — script too close to source ({len(hits)} runs):")
            for h in hits[:10]:
                eprint(f"       \"...{h}...\"")
            ok = False
        elif hits:
            print(f"[gate] WARN — {len(hits)} runs to eyeball:")
            for h in hits[:10]:
                print(f"       \"...{h}...\"")
    else:
        print("[gate] no source file — originality check skipped")

    print(f"[gate] {'PASS' if ok else 'FAIL'}")
    return ok


def run_tts(project_dir: Path) -> bool:
    """Run Gemini TTS on the voice-engineer output."""
    print("\n" + "=" * 60)
    print("  TTS GENERATION (voice: Fenrir)")
    print("=" * 60)
    voice_script = project_dir / "02_SCRIPT_ELEVENLABS.txt"
    if not voice_script.exists():
        eprint("[tts] FAIL — 02_SCRIPT_ELEVENLABS.txt missing")
        return False

    tts_py = TTS_DIR / "gemini_tts.py"
    py = TTS_DIR / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = shutil.which("python")
    if not py:
        eprint("[tts] FAIL — no python (tried .venv then PATH)")
        return False

    audio_dir = project_dir / "06_AUDIO"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Load keys from the pipeline .env so the TTS subprocess sees them.
    env = os.environ.copy()
    env_path = TTS_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())

    cmd = [
        str(py), str(tts_py),
        "--script", str(voice_script),
        "--audio-dir", str(audio_dir),
        "--format", "wav",
        "--voice", "Fenrir",
    ]
    print("[tts] " + " ".join(str(c) for c in cmd[:4]) + " ...")
    result = subprocess.run(cmd, cwd=str(TTS_DIR), env=env,
                            capture_output=True, text=True, timeout=3600)
    print(result.stdout[-3000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-1500:])
    return result.returncode == 0


def print_handoff(project_dir: Path, source: Path | None):
    print("\n" + "=" * 60)
    print("  POV PIPELINE HANDOFF")
    print("=" * 60)
    print(f"  Project:  {project_dir}")
    print(f"  Status:   agents + TTS done (if run with TTS)")
    print(f"\n  YOU DO:")
    print(f"  1. Generate images from 05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt")
    print(f"     (Google Flow / your image tool). Name files <SEG_ID>.png")
    print(f"     and put them in {project_dir / '05_IMAGES'}")
    print(f"  2. Generate thumbnail from 04_THUMBNAIL/THUMBNAIL_PROMPT.txt")
    print(f"  3. Run the assembler:")
    print(f"     python scripts/pov_assembler_pro.py \\")
    print(f"       --script {project_dir / '01_SCRIPT_RAW.txt'} \\")
    print(f"       --audio  {project_dir / '06_AUDIO'} \\")
    print(f"       --images {project_dir / '05_IMAGES'} \\")
    print(f"       --output {project_dir / 'output_pro'} \\")
    print(f"       --cpu-preset light")
    print(f"  4. Post with metadata from 07_METADATA.txt")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser(description="POV Pipeline Orchestrator")
    ap.add_argument("input", nargs="?", help="YouTube URL or path to a transcript file (for scrape phase)")
    ap.add_argument("--project", default=None,
                    help="Existing project folder name (for gate/tts phase)")
    ap.add_argument("--stage", choices=["scrape", "gate", "tts"],
                    help="Which phase to run: scrape | gate | tts (default: scrape when input given)")
    ap.add_argument("--name", default=None, help="Project title (used as folder name)")
    ap.add_argument("--skip-tts", action="store_true", help="Run gate only, stop before TTS")
    a = ap.parse_args()

    projects_dir = PROJECTS_DIR
    projects_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase: SCRAPE ──────────────────────────────────────────────────
    if a.input and (a.stage is None or a.stage == "scrape"):
        is_url = bool(re.match(r"https?://|youtu\.be/|(?:www\.)?youtube\.com", a.input))
        if is_url:
            project_name = make_project_name(a.input)
            if a.name:
                project_name = f"{re.sub(r'[^A-Za-z0-9]+', '_', a.name).strip('_')[:40]}_{now_stamp()}"
        else:
            p = Path(a.input)
            if not p.exists():
                sys.exit(f"[error] File not found: {p}")
            project_name = f"{p.stem[:40]}_{now_stamp()}"

        project_dir = projects_dir / project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"[init] Project: {project_dir}")

        source = None
        if is_url:
            source = scrape_transcript(a.input, project_dir)
            if source is None:
                sys.exit(1)
        else:
            source = copy_transcript_file(Path(a.input), project_dir)

        print("\n" + "=" * 60)
        print("  NEXT: run the 7 agents in order (each agent .md is the prompt)")
        print("  Write each stage output into the project folder above.")
        print("  Then run:")
        print(f"    python run_pov_pipeline.py --project {project_name} --stage gate [--skip-tts]")
        print("  to run the SCRIPT GATE + TTS.")
        print("=" * 60)
        sys.exit(0)

    # ── Phase: GATE + TTS on an existing project ───────────────────────
    if not a.project:
        ap.print_help()
        sys.exit(2)
    project_dir = projects_dir / a.project
    if not project_dir.exists():
        sys.exit(f"[error] Project not found: {project_dir}")

    print(f"[init] Project: {project_dir}")
    run_agents(project_dir)

    if a.stage in ("gate", None):
        if not script_gate(project_dir):
            eprint("[error] Script gate failed — fix the script, then re-run.")
            sys.exit(1)

    if a.stage == "tts" or (a.stage is None and not a.skip_tts):
        ok = run_tts(project_dir)
        if not ok:
            eprint("[error] TTS failed (check above). Re-run to resume (it skips existing segments).")
            sys.exit(1)

    print_handoff(project_dir, project_dir / "00_SOURCE_SCRIPT.txt" if (project_dir / "00_SOURCE_SCRIPT.txt").exists() else None)
    sys.exit(0)


if __name__ == "__main__":
    main()
