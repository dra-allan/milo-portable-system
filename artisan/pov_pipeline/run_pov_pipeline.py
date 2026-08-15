#!/usr/bin/env python3
"""
POV Pipeline Orchestrator - curated channel or URL to a published video.

Usage:
  python run_pov_pipeline.py <youtube_url|transcript_file> [--name TITLE] [--skip-tts]
  python run_pov_pipeline.py --project <NAME> --stage <agents|gate|tts|images|thumb|assemble|video|upload>
  python run_pov_pipeline.py --discover [--niche <name>] [--channels a,b]
  python run_pov_pipeline.py --once
  python run_pov_pipeline.py --daemon
  python run_pov_pipeline.py --check-profiles

What it does:
  0. `--discover` pulls fresh candidates from the curated channels in
     config/pov_channels.yaml, filters + dedupes them and appends them to the
     work queue (see discovery.py). Nothing is processed.
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
     via Google Flow (opencli flow images) into 05_IMAGES/<SEG_ID>.jpeg -
     resume-safe, skips images that already exist.
  6. Stage `thumb` generates the thumbnail from 04_THUMBNAIL/THUMBNAIL_PROMPT.txt.
  7. Stage `assemble` runs the assembler (01_SCRIPT_RAW + 06_AUDIO + 05_IMAGES
     -> output_pro/). Stage `video` = images + thumb + assemble in one shot.
  8. Stage `upload` posts the assembled MP4 + thumbnail + 07_METADATA.txt to
     YouTube (see uploader.py). `--dry-run-upload` prints the payload only.
  9. `--once` / `--daemon` run the whole thing off the queue (see daemon.py).

Config:
  POV_PROJECTS_DIR   where projects live. Defaults to the Windows dev path
                     in povconfig.py; set it on the Linux VPS.
  config/pov_channels.yaml   curated sources, filters, cadence, privacy.
  config/notify.env          Telegram credentials (placeholders by default).
  See agent_runner.py for the agent-chain env overrides (opencode binary,
  model, timeouts, gate retries, Milo memory project).

Exit codes:
  0 = success
  1 = error
  2 = usage error
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENTS_DIR = ROOT / "agents"
TTS_DIR = ROOT / "tts"
SCRIPTS_DIR = ROOT / "scripts"

import povconfig  # noqa: E402  (the sys.path setup above must run first)


def projects_dir() -> Path:
    """Where project folders live. Env-configurable for the Linux VPS."""
    return povconfig.projects_dir()


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
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180,
    )
    # The scraper writes the transcript to stdout and a save note to stderr.
    text = (result.stdout or "").strip()
    if not text and result.returncode == 0:
        # Some node installs echo everything to stderr; fall back to it.
        text = (result.stderr or "").strip()
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
    print(f"[scrape] OK - {len(text)} chars -> {src.name}")
    return src


def copy_transcript_file(path: Path, project_dir: Path) -> Path:
    src = project_dir / "00_SOURCE_SCRIPT.txt"
    shutil.copyfile(path, src)
    print(f"[input] Transcript copied -> {src.name}")
    return src


def run_agents(project_dir: Path, *, model: str = None, gate_retries: int = None,
               timeout: int = None, use_memory: bool = True,
               dry_run: bool = False, notify=None,
               flow_profiles: str = None) -> bool:
    """Run the 7 agents HEADLESS via the opencode CLI.

    The heavy lifting lives in agent_runner.run_agent_chain(): manifest
    refresh, structured briefs, output verification, Milo memory, the gate
    retry loop and NEEDS_REVIEW parking. The existing script_gate is passed
    in rather than reimplemented, so the gate thresholds stay in one place.

    ``flow_profiles`` is accepted and ignored here: the daemon carries every
    pipeline option in one bag and the images stage reads that key later.

    Returns True when every expected output file is present.
    """
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
        eprint(f"[agents] {'NEEDS_REVIEW' if chain.needs_review else 'FAILED'} - {chain.reason}")
        eprint(f"[agents] log: {project_dir / 'state' / 'pipeline.log'}")
    print("-" * 60)
    return chain.ok


def _extract_narration(body: str) -> str:
    """Pull narration text out of a script body, supporting both formats:
    the contract table format (narration is the SUMMARY column of BODY/OUTRO
    rows) and the standalone-marker format (marker on its own line, text
    follows). Header/transition rows, segment metadata and [NAR-###]/[VOICE]
    markers never count as narration."""
    parts = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            fields = [f.strip() for f in line.split("|")]
            if len(fields) >= 6:
                role = fields[1]
                if role in ("BODY", "OUTRO"):
                    summary = re.sub(r"^\[[^\[\]]*\]\s*", "", fields[5]).strip()
                    if summary:
                        parts.append(summary)
                continue
        stripped = re.sub(r"^\[[^\[\]]*\]\s*", "", line)
        if not stripped:
            continue
        if stripped.startswith("POV-") and "The Listener" in stripped:
            continue
        parts.append(stripped)
    return " ".join(parts)


def script_gate(project_dir: Path):
    """SCRIPT GATE: wordcount + rewrite-originality. Cheap check BEFORE TTS.

    Returns ``(ok, report)``. The report is specific enough for the
    scriptwriter to act on (actual wordcount vs budget, per-segment guidance,
    overlap hits) instead of re-dispatching blind.
    """
    print("\n" + "=" * 60)
    print("  SCRIPT GATE")
    print("=" * 60)
    script_path = project_dir / "01_SCRIPT_RAW.txt"
    source_path = project_dir / "00_SOURCE_SCRIPT.txt"
    report_lines = []
    ok = True

    # 1. Wordcount (BODY/OUTRO narration text only, not metadata or headers).
    if not script_path.exists():
        msg = "01_SCRIPT_RAW.txt missing"
        eprint("[gate] FAIL - " + msg)
        return False, "Script gate FAIL: " + msg
    raw = script_path.read_text(encoding="utf-8")
    body = raw.split("=== END MANIFEST ===")[-1]
    narration_text = _extract_narration(body)
    words = len(re.findall(r"[A-Za-z0-9']+", narration_text))
    lo, hi = WORD_BUDGET
    print(f"[gate] wordcount (narration only): {words} (target {lo}-{hi})")
    if not (lo <= words <= hi):
        eprint("[gate] FAIL - outside budget. Expand/cut then re-run.")
        ok = False
        report_lines.append(
            f"Script gate FAIL wordcount: {words} narration words "
            f"(target {lo}-{hi}). Current count is "
            f"{lo - words if words < lo else words - hi} words "
            f"{'SHORT' if words < lo else 'OVER'}."
        )
    else:
        report_lines.append(f"Script gate wordcount PASS: {words} (target {lo}-{hi}).")

    # Per-segment guidance so the writer knows how much to add/cut.
    segs = re.findall(r"(?:^|\n)\s*[A-Za-z0-9_-]+\s*\|\s*(BODY|OUTRO)\s*\|[^|]*\|[^|]*\|[^|]*\|\s*\[?[^|\n]*",
                      "\n" + body)
    if len(segs) >= 5:
        avg = words / max(1, len(segs))
        report_lines.append(
            f"{len(segs)} BODY/OUTRO segments, avg {avg:.0f} words each; "
            f"aim ~{lo // max(1, len(segs))}-{hi // max(1, len(segs))} words "
            f"per segment."
        )

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
            eprint(f"[gate] FAIL - script too close to source ({len(hits)} runs):")
            for h in hits[:10]:
                eprint(f"       \"...{h}...\"")
            ok = False
            report_lines.append(
                f"Script gate FAIL overlap: {len(hits)} matching {n}-word runs "
                f"against the source: {'; '.join(hits[:5])}."
            )
        elif hits:
            print(f"[gate] WARN - {len(hits)} runs to eyeball:")
            for h in hits[:10]:
                print(f"       \"...{h}...\"")
    else:
        print("[gate] no source file - originality check skipped")

    print(f"[gate] {'PASS' if ok else 'FAIL'}")
    return ok, "\n".join(report_lines)


def run_tts(project_dir: Path) -> bool:
    """Run Gemini TTS on the voice-engineer output."""
    print("\n" + "=" * 60)
    print("  TTS GENERATION (voice: Fenrir)")
    print("=" * 60)
    voice_script = project_dir / "02_SCRIPT_ELEVENLABS.txt"
    if not voice_script.exists():
        eprint("[tts] FAIL - 02_SCRIPT_ELEVENLABS.txt missing")
        return False

    tts_py = TTS_DIR / "gemini_tts.py"
    py = TTS_DIR / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = shutil.which("python")
    if not py:
        eprint("[tts] FAIL - no python (tried .venv then PATH)")
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
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=3600)
    print(result.stdout[-3000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-1500:])
    return result.returncode == 0


def _resolve_browser_profile(explicit: str = ""):
    """Pick the Browser Bridge profile for opencli's global ``--profile``.

    Google Flow only works through one connected Chrome Browser Bridge
    profile at a time; when several are connected, opencli fails with
    BROWSER_CONNECT ("Multiple Browser Bridge profiles are connected") unless
    a profile is selected. ``explicit`` (from POV_FLOW_BROWSER_PROFILE or
    --flow-browser-profile) wins; otherwise the first connected profile is
    chosen so the images stage never dies on the ambiguity error.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    opencli = shutil.which("opencli")
    if not opencli:
        return None
    try:
        result = subprocess.run([opencli, "profile", "list"],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
# "  n83jffs4 flow-3 — connected v1.0.0" -> prefer the alias, else the id.
    # Split on whitespace and find the token right before "connected": that is
    # the profile id, or the alias when one precedes the id.
    for line in (result.stdout or "").splitlines():
        tokens = line.split()
        idx = next((i for i, t in enumerate(tokens)
                    if t.lower() == "connected"), -1)
        if idx < 1 or tokens[idx - 1].lower() == "not":
            continue  # headers or "not connected" lines
        name = None
        for candidate in tokens[idx - 1::-1]:
            if len(candidate) > 1 and candidate.lower() != "connected":
                name = candidate
                break
        if name:
            return name
    return None


def run_flow_images(project_dir: Path, profiles: str = "",
                    browser_profile: str = "") -> bool:
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
    bridge = _resolve_browser_profile(browser_profile)
    if bridge:
        cmd = [opencli, "--profile", bridge, "flow", "images",
               "--file", str(batch)]
    if profiles:
        cmd += ["--profiles", profiles]
    if bridge:
        print(f"[images] browser bridge profile: {bridge}")

    print("[images] " + " ".join(str(c) for c in cmd[:4]) + " ...")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=7200)
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


def check_flow_profiles(expected: str = "") -> bool:
    """Preflight: are the Chrome Browser Bridge profiles connected?

    Google Flow only generates images while its Chrome profiles are OPEN; a
    closed profile fails with BROWSER_CONNECT deep inside the images stage,
    after the pipeline has already spent an agent chain and a TTS run. This
    asks `opencli profile list` up front so the failure is cheap and legible.

    Never launches or logs in to anything: that stays a human, one-time step.
    """
    print("\n" + "=" * 60)
    print("  CHROME BRIDGE PREFLIGHT")
    print("=" * 60)
    opencli = shutil.which("opencli")
    if not opencli:
        eprint("[profiles] FAIL - 'opencli' not on PATH")
        return False
    try:
        result = subprocess.run([opencli, "profile", "list"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        eprint(f"[profiles] FAIL - could not run 'opencli profile list': {exc}")
        return False

    output = (result.stdout or "") + (result.stderr or "")
    print(output.strip()[:2000] or "(no output)")
    if result.returncode != 0:
        eprint(f"[profiles] FAIL - 'opencli profile list' exited {result.returncode}")
        return False

    wanted = [p.strip() for p in (expected or "").split(",") if p.strip()]
    if not wanted:
        print("[profiles] no --flow-profiles given; listing only.")
        return True

    low = output.lower()
    missing = [p for p in wanted if p.lower() not in low]
    if missing:
        eprint(f"[profiles] FAIL - not connected: {', '.join(missing)}")
        eprint("[profiles] Open them first: scripts/flow_profiles_up.ps1 "
               "(Windows) or scripts/flow_profiles_up.sh (VPS).")
        return False
    print(f"[profiles] OK - {len(wanted)} profile(s) connected.")
    return True


def run_thumbnail(project_dir: Path, browser_profile: str = "") -> bool:
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
    bridge = _resolve_browser_profile(browser_profile)
    cmd = [
        opencli, "flow", "image-gen",
        "--prompt", prompt,
        "--aspect", "16:9",
        "--out", str(out_file),
        "--yes",
    ]
    if bridge:
        cmd = [
            opencli, "--profile", bridge, "flow", "image-gen",
            "--prompt", prompt,
            "--aspect", "16:9",
            "--out", str(out_file),
            "--yes",
        ]
        print(f"[thumb] browser bridge profile: {bridge}")
    print("[thumb] " + " ".join(str(c) for c in cmd[:3]) + " ...")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600)
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
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=10800)
    print(result.stdout[-4000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-2000:])
    return result.returncode == 0


def run_upload(project_dir: Path, *, channel: str = "explaination",
               privacy: str = None, published_at: str = None,
               dry_run: bool = False, notify=None) -> bool:
    """Stage `upload`: push the assembled video to YouTube (M3)."""
    import uploader

    cfg = povconfig.load_config()
    defaults = cfg.get("defaults") or {}
    result = uploader.upload_project(
        project_dir,
        channel=channel or defaults.get("upload_channel") or "explaination",
        privacy=privacy or defaults.get("privacy") or "unlisted",
        published_at=(published_at if published_at is not None
                      else defaults.get("published_at")),
        dry_run=dry_run,
        notify=notify,
    )
    if result.ok and result.video_id:
        # Close the loop on the queue so discovery never re-offers this source.
        try:
            from discovery import PovDB, extract_video_id

            url_file = project_dir / "00_SOURCE_URL.txt"
            source_url = (url_file.read_text(encoding="utf-8-sig", errors="replace").strip()
                          if url_file.exists() else "")
            vid = extract_video_id(source_url)
            if vid:
                with PovDB() as db:
                    db.mark(vid, "done", project=project_dir.name, reason=result.url)
        except Exception as exc:
            eprint(f"[upload] queue bookkeeping skipped: {type(exc).__name__}: {exc}")
    return result.ok


def print_handoff(project_dir: Path, source: Path | None):
    print("\n" + "=" * 60)
    print("  POV PIPELINE HANDOFF")
    print("=" * 60)
    print(f"  Project:  {project_dir}")
    print("  Status:   agents + TTS done (if run with TTS)")
    print("\n  NEXT:")
    print("  1. Generate images + thumbnail + assemble in one shot:")
    print(f"     python run_pov_pipeline.py --project {project_dir.name} --stage video")
    print("     (or run the stages separately: images | thumb | assemble)")
    print("  2. Upload with the metadata from 07_METADATA.txt:")
    print(f"     python run_pov_pipeline.py --project {project_dir.name} --stage upload")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser(description="POV Pipeline Orchestrator")
    ap.add_argument("input", nargs="?", help="YouTube URL or path to a transcript file (for scrape phase)")
    ap.add_argument("--project", default=None,
                    help="Existing project folder name (for agents/gate/tts phase)")
    ap.add_argument("--stage", choices=["scrape", "agents", "gate", "tts", "images",
                                        "thumb", "assemble", "video", "upload"],
                    help="Which phase to run (default: scrape when input given, else agents+gate+tts)")
    ap.add_argument("--name", default=None, help="Project title (used as folder name)")
    ap.add_argument("--skip-tts", action="store_true", help="Run gate only, stop before TTS")
    ap.add_argument("--flow-profiles", default=None,
                    help="Google Flow profiles to rotate through on rate limits (e.g. flow-1,flow-2)")
    ap.add_argument("--flow-browser-profile", default=None,
                    help="Chrome Browser Bridge profile for opencli --profile (default: POV_FLOW_BROWSER_PROFILE or first connected)")
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
    # Discovery (M2).
    ap.add_argument("--discover", action="store_true",
                    help="Find new source videos from config/pov_channels.yaml and queue them")
    ap.add_argument("--niche", action="append", default=None,
                    help="Limit discovery to this niche (repeatable)")
    ap.add_argument("--channels", default=None,
                    help="Limit discovery to these @handles (comma separated)")
    ap.add_argument("--max-channels", type=int, default=None,
                    help="Channels to touch in one discovery run (default 5)")
    ap.add_argument("--queue", action="store_true",
                    help="Print the current work queue and exit")
    # Upload (M3).
    ap.add_argument("--privacy", default=None,
                    choices=["private", "unlisted", "public"],
                    help="Upload privacy (default: config, unlisted)")
    ap.add_argument("--published-at", default=None,
                    help="ISO8601 scheduled publish time (stays private until then)")
    ap.add_argument("--upload-channel", default=None,
                    help="Channel key for the OAuth token (default: explaination)")
    ap.add_argument("--dry-run-upload", action="store_true",
                    help="Print the upload payload without calling the API")
    # Daemon (M4).
    ap.add_argument("--once", action="store_true",
                    help="Process the next queue item end to end, then exit")
    ap.add_argument("--daemon", action="store_true",
                    help="Loop: process the queue on a schedule (VPS mode)")
    ap.add_argument("--interval", type=int, default=None,
                    help="Daemon minutes between ticks (default: config)")
    ap.add_argument("--ignore-window", action="store_true",
                    help="--once only: run even outside the posting window")
    ap.add_argument("--skip-upload", action="store_true",
                    help="--once/--daemon: stop after assembly")
    # Notifications (M5) + preflight.
    ap.add_argument("--no-notify", action="store_true",
                    help="Disable Telegram notifications for this run")
    ap.add_argument("--check-profiles", action="store_true",
                    help="Chrome Browser Bridge preflight, then exit")
    a = ap.parse_args()

    from notify import make_notifier, null_notifier

    notifier = null_notifier() if a.no_notify else make_notifier()

    # -- Preflight: Chrome bridge --------------------------------------
    if a.check_profiles:
        sys.exit(0 if check_flow_profiles(a.flow_profiles or "") else 1)

    proj_root = projects_dir()
    proj_root.mkdir(parents=True, exist_ok=True)

    agent_opts = dict(
        model=a.model,
        gate_retries=a.gate_retries,
        timeout=a.agent_timeout,
        use_memory=not a.no_memory,
        dry_run=a.dry_run_agents,
    )

    # -- Queue inspection ----------------------------------------------
    if a.queue:
        from discovery import PovDB

        with PovDB() as db:
            rows = db.queue(limit=50)
            counts = db.counts()
            print(f"  db: {db.path}")
            for row in rows:
                print(f"  {row['score']:.2f}  {row['status']:<11} "
                      f"{row['video_id']}  {(row['title'] or '')[:60]}")
            print("  " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                          or "queue empty"))
        sys.exit(0)

    # -- M2: discovery ---------------------------------------------------
    if a.discover:
        import discovery

        cfg = povconfig.load_config()
        channels = [c for c in (a.channels or "").split(",") if c.strip()]
        found = discovery.discover(cfg, niches=a.niche, channels=channels,
                                   max_channels=a.max_channels, notify=notifier)
        discovery.print_summary(found)
        sys.exit(0)

    # -- M4: queue-driven modes ------------------------------------------
    if a.once or a.daemon:
        import daemon as pov_daemon

        cfg = povconfig.load_config()
        opts = dict(
            skip_upload=a.skip_upload,
            dry_run_upload=a.dry_run_upload,
            agent_opts={**agent_opts, "flow_profiles": a.flow_profiles or "",
                        "flow_browser_profile": a.flow_browser_profile or ""},
        )
        if a.privacy:
            opts["privacy"] = a.privacy
        if a.upload_channel:
            opts["channel"] = a.upload_channel
        if a.daemon:
            sys.exit(pov_daemon.run_daemon(cfg, notify=notifier,
                                           interval_minutes=a.interval, **opts))
        result = pov_daemon.run_once(cfg, notify=notifier,
                                     ignore_window=a.ignore_window, **opts)
        sys.exit(0 if result.ok else 1)

    # -- Phase: SCRAPE ---------------------------------------------------
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
            # Manual ingestion still goes through the ledger, so discovery
            # never re-queues a URL a human already fed in.
            try:
                from discovery import PovDB

                with PovDB() as db:
                    db.mark_url_processed(a.input, project_name)
            except Exception as exc:
                eprint(f"[queue] bookkeeping skipped: {type(exc).__name__}: {exc}")
        else:
            source = copy_transcript_file(Path(a.input), project_dir)

        if a.stage == "scrape":
            print("\n" + "=" * 60)
            print("  NEXT: run the headless agent chain:")
            print(f"    python run_pov_pipeline.py --project {project_name} --stage agents")
            print("=" * 60)
            sys.exit(0)

        # No explicit stage: keep going straight into the agent chain.
        if not run_agents(project_dir, notify=notifier, **agent_opts):
            sys.exit(1)
        if not a.skip_tts and not run_tts(project_dir):
            eprint("[error] TTS failed (check above). Re-run to resume (it skips existing segments).")
            sys.exit(1)
        print_handoff(project_dir, source)
        sys.exit(0)

    # -- Phase: work on an existing project -------------------------------
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
        if not run_agents(project_dir, notify=notifier, **agent_opts):
            sys.exit(1)
        ran_chain = True
        if a.stage == "agents":
            print_handoff(project_dir, None)
            sys.exit(0)

    if a.stage == "gate" or (a.stage is None and not ran_chain):
        gate_ok, _ = script_gate(project_dir)
        if not gate_ok:
            eprint("[error] Script gate failed - fix the script, then re-run.")
            sys.exit(1)
        if a.stage == "gate":
            sys.exit(0)

    if a.stage == "tts" or (a.stage is None and not a.skip_tts):
        ok = run_tts(project_dir)
        if not ok:
            eprint("[error] TTS failed (check above). Re-run to resume (it skips existing segments).")
            sys.exit(1)

    if a.stage in ("images", "video"):
        ok = run_flow_images(project_dir, profiles=a.flow_profiles or "",
                             browser_profile=a.flow_browser_profile or "")
        if not ok:
            notifier("images.failed",
                     f"POV {project_dir.name}: image generation incomplete "
                     "(check the Chrome Browser Bridge)")
            eprint("[error] Image generation failed (check above). Re-run to resume (it skips existing images).")
            sys.exit(1)
        notifier("images.done", f"POV {project_dir.name}: all segment images generated")

    if a.stage in ("thumb", "video"):
        ok = run_thumbnail(project_dir, browser_profile=a.flow_browser_profile or "")
        if not ok:
            eprint("[error] Thumbnail generation failed (check above).")
            sys.exit(1)

    if a.stage in ("assemble", "video"):
        ok = run_assembler(project_dir)
        if not ok:
            eprint("[error] Assembly failed (check above).")
            sys.exit(1)
        notifier("video.assembled", f"POV {project_dir.name}: video assembled")

    if a.stage == "upload":
        ok = run_upload(project_dir, channel=a.upload_channel or "explaination",
                        privacy=a.privacy, published_at=a.published_at,
                        dry_run=a.dry_run_upload, notify=notifier)
        if not ok:
            eprint("[error] Upload failed (check above).")
            sys.exit(1)
        sys.exit(0)

    print_handoff(project_dir,
                  project_dir / "00_SOURCE_SCRIPT.txt"
                  if (project_dir / "00_SOURCE_SCRIPT.txt").exists() else None)
    sys.exit(0)


if __name__ == "__main__":
    main()
