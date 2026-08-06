#!/usr/bin/env python3
"""
MM Pipeline Orchestrator — topic to MP4 in one command.
Usage: python run_pipeline.py TOPIC_NAME [--music MUSIC_FILE] [--skip-tts] [--skip-video]
"""

import os, sys, subprocess, re, shutil, textwrap, json
from pathlib import Path

ROOT = Path(__file__).parent
FFMPEG = r"C:\Users\user\Desktop\AGENTIC WORK\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\bin\ffmpeg.exe"
FFPROBE = FFMPEG.replace("ffmpeg.exe", "ffprobe.exe")
PIPELINE_AGENTS = [
    ("01", "ResearchAnalyst",  "00_RESEARCH_NOTES.txt"),
    ("02", "ScriptEngineer",   "01_SCRIPT_RAW.txt"),
    ("03", "VoiceEngineer",    "02_SCRIPT_TTS.txt"),
    ("04", "VisualDirector",   "03_VISUALS.txt"),
    ("05", "ThumbnailCopywriter", "04_THUMBNAIL_PROMPT.txt"),
    ("06", "MetadataLibrarian",   "05_METADATA.txt"),
]


def eprint(*a, **kw): print(*a, **kw, file=sys.stderr)


def check_tools():
    missing = []
    for tool in [FFMPEG, FFPROBE]:
        if not os.path.exists(tool):
            missing.append(tool)
    if missing:
        eprint(f"[ERR] Missing tools: {missing}")
        sys.exit(1)


def get_agent_prompt(agent_name: str) -> str:
    """Read the agent prompt file from agents/ directory."""
    agent_dir = ROOT / "agents"
    if not agent_dir.exists():
        return ""
    for f in agent_dir.iterdir():
        if f.stem.lower() == agent_name.lower():
            return f.read_text(encoding="utf-8")
    return ""


def print_section(title: str):
    w = shutil.get_terminal_size().columns
    sep = "=" * w
    print(f"\n{sep}\n  {title}\n{sep}")


def check_topic_state(topic: str) -> dict:
    """Inspect topic folder and report which artifacts exist."""
    topic_dir = ROOT / topic
    if not topic_dir.exists():
        return {"exists": False, "artifacts": {}}

    artifacts = {}
    for _, name, filename in PIPELINE_AGENTS:
        artifacts[name] = (topic_dir / filename).exists()
    artifacts["TTS_segments"] = (topic_dir / "tts_segments").exists()
    artifacts["Mixed_WAV"] = (topic_dir / f"{topic}_FINAL.wav").exists()
    artifacts["FINAL_MP4"] = (topic_dir / f"{topic}_FINAL.mp4").exists()
    artifacts["Completeness"] = (topic_dir / "COMPLETENESS_REPORT.txt").exists()
    return {"exists": True, "artifacts": artifacts}


def run_tts(topic: str) -> bool:
    """Run Gemini TTS for all segments."""
    topic_dir = ROOT / topic
    tts_script = ROOT / "gemini_tts.py"
    if not tts_script.exists():
        eprint("[TTS] gemini_tts.py not found, skipping TTS")
        return False

    print_section(f"TTS Generation — {topic}")
    result = subprocess.run(
        [sys.executable, str(tts_script), topic],
        cwd=ROOT, capture_output=True, text=True, timeout=600
    )
    print(result.stdout)
    if result.returncode != 0:
        eprint(f"[TTS] Failed:\n{result.stderr}")
        return False
    return True


def assemble_video(topic: str, music_file: str | None = None) -> bool:
    """Run the video assembler."""
    assembler = ROOT / "mm_video_assembler.py"
    if not assembler.exists():
        eprint("[Assembler] mm_video_assembler.py not found")
        return False

    print_section(f"Video Assembly — {topic}")
    cmd = [sys.executable, str(assembler), topic]
    if music_file:
        cmd.extend(["--music", os.path.abspath(music_file)])
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    print(result.stdout)
    if result.returncode != 0:
        eprint(f"[Assembler] Failed:\n{result.stderr}")
        return False
    return True


def print_report(topic: str):
    w = shutil.get_terminal_size().columns
    state = check_topic_state(topic)
    if not state["exists"]:
        print(f"No artifacts for topic: {topic}")
        return

    topic_dir = ROOT / topic
    artifacts = state["artifacts"]
    present = [k for k, v in artifacts.items() if v]
    missing = [k for k, v in artifacts.items() if not v]

    files = {}
    for f in topic_dir.iterdir():
        if f.is_file():
            sz = f.stat().st_size
            files[f.name] = f"{sz/1024:.0f} KB" if sz < 1024*1024 else f"{sz/1024/1024:.1f} MB"

    sep = "-" * w
    print(f"\n{sep}")
    print(f"  Pipeline Report - {topic}")
    print(sep)
    print(f"  Present: {', '.join(present) or 'none'}")
    if missing:
        print(f"  Missing: {', '.join(missing)}")
    print(sep)
    for name, size in files.items():
        print(f"    {name:<40s} {size:>8s}")
    print(f"{sep}\n")


def main():
    check_tools()

    if not sys.argv[1:]:
        print(__doc__)
        print("\nAvailable topics:")
        for d in ROOT.iterdir():
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
                state = check_topic_state(d.name)
                mp4 = "MP4" if state.get("artifacts", {}).get("FINAL_MP4") else ""
                wav = "WAV" if state.get("artifacts", {}).get("Mixed_WAV") else ""
                print(f"  {d.name}/")
        return

    topic = sys.argv[1]
    music_file = None
    skip_tts = False
    skip_video = False

    args = iter(sys.argv[2:])
    for a in args:
        if a == "--music":
            music_file = next(args, None)
        elif a == "--skip-tts":
            skip_tts = True
        elif a == "--skip-video":
            skip_video = True

    state = check_topic_state(topic)

    if not state["exists"]:
        eprint(f"[ERR] Topic folder not found: {ROOT / topic}")
        eprint("First run the creative agents (ResearchAnalyst through MetadataLibrarian)")
        eprint("then call this orchestrator for TTS + video assembly.")
        sys.exit(1)

    print_section(f"MM Pipeline Orchestrator - {topic}")
    print(f"  Working dir: {ROOT / topic}")
    print(f"  Music: {music_file or 'none'}")
    print()

    # Step 1: TTS
    if not skip_tts:
        has_tts_script = (ROOT / "gemini_tts.py").exists()
        has_wav = state["artifacts"].get("Mixed_WAV", False)
        has_segments = state["artifacts"].get("TTS_segments", False)

        if has_wav and has_segments:
            print("[TTS] Complete WAV already exists, skipping")
        elif has_tts_script:
            if not run_tts(topic):
                eprint("[TTS] failed, aborting")
                sys.exit(1)

    # Step 2: Video assembly
    if not skip_video:
        has_mp4 = state["artifacts"].get("FINAL_MP4", False)
        if has_mp4:
            print("[Video] MP4 already exists, skipping")
        else:
            if not assemble_video(topic, music_file):
                eprint("[Video] failed, aborting")
                sys.exit(1)

    # Report
    print_report(topic)


if __name__ == "__main__":
    main()
