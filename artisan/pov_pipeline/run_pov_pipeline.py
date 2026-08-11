#!/usr/bin/env python3
"""
POV Pipeline Orchestrator — URL to finished POV video.

Usage:
  python run_pov_pipeline.py <youtube_url|transcript_file> [--name TITLE] [--skip-tts]
  python run_pov_pipeline.py --project <NAME> --stage <agents|gate|tts|images|thumb|assemble|video>

What it does:
  1. Scrapes the transcript from a YouTube URL (or reads a transcript file)
     into the project folder as 00_SOURCE_SCRIPT.txt.
  2. Runs the 7 POV agents in order, HEADLESS, via the opencode CLI
     (see agent_runner.py). Each agent .md is the prompt; each stage output
     is verified to exist and be non-empty before the next agent runs.
     The SCRIPT GATE is wired into that chain: a FAIL re-dispatches the
     scriptwriter with the failure report (up to 3 times), then parks the
     project as NEEDS_REVIEW instead of crashing the batch.
  3. Runs the SCRIPT GATE (rewrite-originality + wordcount) before TTS.
  4. Auto-runs Gemini TTS (voice Fenrir) to generate 06_AUDIO/<SEG>.mp3.
  5. Stage `images` generates every image from 05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt
     via Google Flow (opencli flow images) into 05_IMAGES/<SEG_ID>.jpeg —
     resume-safe, skips images that already exist.
  6. Stage `thumb` generates the thumbnail from 04_THUMBNAIL/THUMBNAIL_PROMPT.txt.
  7. Stage `assemble` runs the assembler (01_SCRIPT_RAW + 06_AUDIO + 05_IMAGES
     → output_pro/). Stage `video` = images + thumb + assemble in one shot.

Config:
  POV_PROJECTS_DIR   where projects live. Defaults to the Windows dev path
                     below; set it on the Linux VPS.
  See agent_runner.py for the agent-chain env overrides (opencode binary,
  model, timeouts, gate retries, Milo memory project).

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

# Windows dev default. The VPS overrides it with POV_PROJECTS_DIR — this is
# the one documented absolute path left in the codebase.
DEFAULT_PROJECTS_DIR = Path(r"C:\Users\user\Desktop\Milo Video Factory\pov\projects")


def projects_dir() -> Path:
    """Where project folders live. Env-configurable for the Linux VPS."""
    override = os.environ.get("POV_PROJECTS_DIR", "").strip()
    return Path(override).expanduser() if override else DEFAULT_PROJECTS_DIR


# Kept as a module constant for backwards compatibility with anything that
# imported it. Prefer projects_dir().
PROJECTS_DIR = projects_dir()

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
    # Remember where this came from: the agent-runner manifest, the discovery
    # dedupe (M2) and the uploader (M3) all want the source URL.
    (project_dir / "00_SOURCE_URL.txt").write_text(url.strip() + "\n", encoding="utf-8")
    print(f"[scrape] OK — {len(text)} chars -> {src.name}")
    return src


def copy_transcript_file(path: Path, project_dir: Path) -> Path:
    src = project_dir / "00_SOURCE_SCRIPT.txt"
    shutil.copyfile(path, src)
    print(f"[input] Transcript copied -> {src.name}")
    return src


def run_agents(project_dir: Path, *, model: str = None, gate_retries: int = None,
               timeout: int = None, use_memory: bool = True,
               dry_run: bool = False, notify=None) -> bool:
    """Run the 7 agents HEADLESS via the opencode CLI.

    The heavy lifting lives in agent_runner.run_agent_chain(): manifest
    refresh, structured briefs, output verification, Milo memory, the gate
    retry loop and NEEDS_REVIEW parking. The existing script_gate is passed
    in rather than reimplemented, so the gate thresholds stay in one place.

    Returns True when every expected output file is present.
    """
    try:
        from agent_runner import run_agent_chain
    except ImportError:  # running from a copy without the module next to it
        sys.path.insert(0, str(ROOT))
        from agent_runner import run_agent_chain

    chain = run_agent_chain(
        project_dir,
        PIPELINE_AGENTS,
        agents_dir=AGENTS_DIR,
        gate_fn=script_gate,
        gate_after="POV-scriptwriter",
        gate_retries=gate_retries,
        model=model,
        timeout=timeout,
        use_memory=use_memory,
        notify=notify,
        dry_run=dry_run,
    )

    print("\n" + "-" * 60)
    print(f"  AGENT CHAIN: {chain.summary()}")
    if not chain.ok:
        eprint(f"[agents] {'NEEDS_REVIEW' if chain.needs_review else 'FAILED'} — {chain.reason}")
        eprint(f"[agents] log: {project_dir / 'state' / 'pipeline.log'}")
    print("-" * 60)
    return chain.ok


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


def run_flow_images(project_dir: Path, profiles: str = "") -> bool:
    """Generate all segment images via Google Flow (opencli flow images)."""
    print("\n" + "=" * 60)
    print("  IMAGE GENERATION (Google Flow)")
    print("=" * 60)
    batch = project_dir / "05_IMAGES" / "IMAGE_PROMPTS_BATCH_FINAL.txt"
    if not batch.exists():
        eprint(f"[images] FAIL - {batch.name} missing (run the image-director agent first)")
        return False

    opencli = shutil.which("opencli")
    if not opencli:
        eprint("[images] FAIL - 'opencli' not on PATH (needed for Google Flow image generation)")
        return False

    cmd = [opencli, "flow", "images", "--file", str(batch)]
    if profiles:
        cmd += ["--profiles", profiles]

    print("[images] " + " ".join(str(c) for c in cmd[:4]) + " ...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    print(result.stdout[-3000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-1500:])

    missing = _missing_image_segments(project_dir, batch)
    if missing:
        eprint(f"[images] PARTIAL - {len(missing)} image(s) missing:")
        eprint("    " + ", ".join(missing[:15]) + (" ..." if len(missing) > 15 else ""))
        eprint("[images] Fix the failures above, then re-run --stage images to resume (existing images are skipped).")
        return False
    print(f"[images] OK - all {_expected_image_segments(batch)} segment image(s) present.")
    return True


_SEG_BATCH_RE = re.compile(r"^\s*\[([A-Z0-9]{2,8}-\d{3}(?:-[A-E])?)\]\s*(.*)$")
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def _expected_image_segments(batch: Path) -> list[str]:
    """Parse every [SEG_ID] block in the batch file (mirrors flow-cli images.ts)."""
    ids: list[str] = []
    seen: set[str] = set()
    for block in batch.read_text(encoding="utf-8-sig", errors="replace").split("\n\n"):
        m = _SEG_BATCH_RE.match(block.strip())
        if not m:
            continue
        body = re.sub(r"^\[[^\]]+\]\s*-\s*[^|]*\|\s*", "", block.strip()).strip()
        if len(body) < 10 or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        ids.append(m.group(1))
    return ids


def _missing_image_segments(project_dir: Path, batch: Path) -> list[str]:
    images_dir = project_dir / "05_IMAGES"
    missing = []
    for seg in _expected_image_segments(batch):
        if not any((images_dir / f"{seg}{ext}").exists() for ext in _IMG_EXTS):
            missing.append(seg)
    return missing


def run_thumbnail(project_dir: Path) -> bool:
    """Generate the thumbnail via Google Flow (opencli flow image-gen)."""
    print("\n" + "=" * 60)
    print("  THUMBNAIL GENERATION (Google Flow)")
    print("=" * 60)
    prompt_file = project_dir / "04_THUMBNAIL" / "THUMBNAIL_PROMPT.txt"
    if not prompt_file.exists():
        eprint(f"[thumb] FAIL - {prompt_file.name} missing (run the thumbnail-artist agent first)")
        return False

    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        eprint("[thumb] FAIL - thumbnail prompt is empty")
        return False

    opencli = shutil.which("opencli")
    if not opencli:
        eprint("[thumb] FAIL - 'opencli' not on PATH")
        return False

    out_file = project_dir / "04_THUMBNAIL" / "thumbnail.png"
    cmd = [
        opencli, "flow", "image-gen",
        "--prompt", prompt,
        "--aspect", "16:9",
        "--out", str(out_file),
        "--yes",
    ]
    print("[thumb] " + " ".join(str(c) for c in cmd[:3]) + " ...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print(result.stdout[-2000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-1500:])
    return result.returncode == 0 and out_file.exists()


def run_assembler(project_dir: Path) -> bool:
    """Run the POV assembler to build the final video."""
    print("\n" + "=" * 60)
    print("  VIDEO ASSEMBLY")
    print("=" * 60)
    script = project_dir / "01_SCRIPT_RAW.txt"
    audio = project_dir / "06_AUDIO"
    images = project_dir / "05_IMAGES"
    if not script.exists() or not audio.exists() or not images.exists():
        eprint("[assemble] FAIL - need 01_SCRIPT_RAW.txt, 06_AUDIO/, 05_IMAGES/ all present")
        return False

    py = shutil.which("python")
    if not py:
        eprint("[assemble] FAIL - no python on PATH")
        return False

    assembler = SCRIPTS_DIR / "pov_assembler_pro.py"
    if not assembler.exists():
        eprint(f"[assemble] FAIL - assembler not found: {assembler}")
        return False

    cmd = [
        str(py), str(assembler),
        "--script", str(script),
        "--audio", str(audio),
        "--images", str(images),
        "--output", str(project_dir / "output_pro"),
        "--cpu-preset", "light",
    ]
    print("[assemble] " + " ".join(str(c) for c in cmd[:5]) + " ...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10800)
    print(result.stdout[-4000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-2000:])
    return result.returncode == 0


def print_handoff(project_dir: Path, source: Path | None):
    print("\n" + "=" * 60)
    print("  POV PIPELINE HANDOFF")
    print("=" * 60)
    print(f"  Project:  {project_dir}")
    print(f"  Status:   agents + TTS done (if run with TTS)")
    print(f"\n  NEXT:")
    print(f"  1. Generate images + thumbnail + assemble in one shot:")
    print(f"     python run_pov_pipeline.py --project {project_dir.name} --stage video")
    print(f"     (or run the stages separately: images | thumb | assemble)")
    print(f"  2. Post with metadata from 07_METADATA.txt")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser(description="POV Pipeline Orchestrator")
    ap.add_argument("input", nargs="?", help="YouTube URL or path to a transcript file (for scrape phase)")
    ap.add_argument("--project", default=None,
                    help="Existing project folder name (for agents/gate/tts phase)")
    ap.add_argument("--stage", choices=["scrape", "agents", "gate", "tts", "images", "thumb", "assemble", "video"],
                    help="Which phase to run: scrape | agents | gate | tts | images | thumb | assemble | video (default: scrape when input given, else agents+gate+tts)")
    ap.add_argument("--name", default=None, help="Project title (used as folder name)")
    ap.add_argument("--skip-tts", action="store_true", help="Run gate only, stop before TTS")
    ap.add_argument("--flow-profiles", default=None,
                    help="Google Flow account profiles to rotate through on rate limits (e.g. flow-account-1,flow-account-2)")
    # Agent-chain controls (M1).
    ap.add_argument("--model", default=None,
                    help="Model for the headless agent runs, as provider/model")
    ap.add_argument("--gate-retries", type=int, default=None,
                    help="Scriptwriter re-dispatches after a gate FAIL (default 3)")
    ap.add_argument("--agent-timeout", type=int, default=None,
                    help="Per-agent timeout in seconds (default: per-agent budget)")
    ap.add_argument("--no-memory", action="store_true",
                    help="Do not write pipeline events to Milo's memory")
    ap.add_argument("--dry-run-agents", action="store_true",
                    help="Print the exact opencode invocation per agent, run nothing")
    a = ap.parse_args()

    proj_root = projects_dir()
    proj_root.mkdir(parents=True, exist_ok=True)

    agent_opts = dict(
        model=a.model,
        gate_retries=a.gate_retries,
        timeout=a.agent_timeout,
        use_memory=not a.no_memory,
        dry_run=a.dry_run_agents,
    )

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

        project_dir = proj_root / project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"[init] Project: {project_dir}")

        source = None
        if is_url:
            source = scrape_transcript(a.input, project_dir)
            if source is None:
                sys.exit(1)
        else:
            source = copy_transcript_file(Path(a.input), project_dir)

        if a.stage == "scrape":
            print("\n" + "=" * 60)
            print("  NEXT: run the headless agent chain:")
            print(f"    python run_pov_pipeline.py --project {project_name} --stage agents")
            print("=" * 60)
            sys.exit(0)

        # No explicit stage: keep going straight into the agent chain.
        if not run_agents(project_dir, **agent_opts):
            sys.exit(1)
        if not a.skip_tts and not run_tts(project_dir):
            eprint("[error] TTS failed (check above). Re-run to resume (it skips existing segments).")
            sys.exit(1)
        print_handoff(project_dir, source)
        sys.exit(0)

    # ── Phase: work on an existing project ─────────────────────────────
    if not a.project:
        ap.print_help()
        sys.exit(2)
    project_dir = proj_root / a.project
    if not project_dir.exists():
        sys.exit(f"[error] Project not found: {project_dir}")

    print(f"[init] Project: {project_dir}")

    # The agent chain is expensive, so it only runs when it was asked for.
    # It owns the script gate internally (retry loop), so `gate` is not run
    # a second time in the same invocation.
    ran_chain = False
    if a.stage in ("agents", None):
        if not run_agents(project_dir, **agent_opts):
            sys.exit(1)
        ran_chain = True
        if a.stage == "agents":
            print_handoff(project_dir, None)
            sys.exit(0)

    if a.stage == "gate" or (a.stage is None and not ran_chain):
        if not script_gate(project_dir):
            eprint("[error] Script gate failed — fix the script, then re-run.")
            sys.exit(1)
        if a.stage == "gate":
            sys.exit(0)

    if a.stage == "tts" or (a.stage is None and not a.skip_tts):
        ok = run_tts(project_dir)
        if not ok:
            eprint("[error] TTS failed (check above). Re-run to resume (it skips existing segments).")
            sys.exit(1)

    if a.stage in ("images", "video"):
        ok = run_flow_images(project_dir, profiles=a.flow_profiles or "")
        if not ok:
            eprint("[error] Image generation failed (check above). Re-run to resume (it skips existing images).")
            sys.exit(1)

    if a.stage in ("thumb", "video"):
        ok = run_thumbnail(project_dir)
        if not ok:
            eprint("[error] Thumbnail generation failed (check above).")
            sys.exit(1)

    if a.stage in ("assemble", "video"):
        ok = run_assembler(project_dir)
        if not ok:
            eprint("[error] Assembly failed (check above).")
            sys.exit(1)

    print_handoff(project_dir, project_dir / "00_SOURCE_SCRIPT.txt" if (project_dir / "00_SOURCE_SCRIPT.txt").exists() else None)
    sys.exit(0)


if __name__ == "__main__":
    main()
