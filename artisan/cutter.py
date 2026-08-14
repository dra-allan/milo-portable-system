import os
import shutil
import subprocess
import json
from pathlib import Path


def _find_ffmpeg() -> str:
    env_path = os.environ.get("MILO_FFMPEG", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    legacy = Path.home() / "Desktop" / "AGENTIC WORK" / "ffmpeg-2026-05-18-git-b4d11dffbf-full_build" / "ffmpeg-2026-05-18-git-b4d11dffbf-full_build" / "bin" / "ffmpeg.exe"
    if legacy.exists():
        return str(legacy)
    return "ffmpeg"


def _find_ffprobe() -> str:
    env_path = os.environ.get("MILO_FFPROBE", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    found = shutil.which("ffprobe")
    if found:
        return found
    return _find_ffmpeg().replace("ffmpeg.exe", "ffprobe.exe")


FFMPEG_PATH = _find_ffmpeg()
FFPROBE_PATH = _find_ffprobe()

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "videos"


def assemble_video(audio_path: str, image_paths: list[str], output_name: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{output_name}.mp4"

    ffmpeg_cmd = [
        FFMPEG_PATH, "-y",
    ]

    for img in image_paths:
        ffmpeg_cmd.extend(["-loop", "1", "-i", img])

    ffmpeg_cmd.extend(["-i", audio_path])

    filter_complex = ""
    for i in range(len(image_paths)):
        filter_complex += f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}];"

    filter_complex += "".join(f"[v{i}]" for i in range(len(image_paths)))
    filter_complex += f"concat=n={len(image_paths)}:v=1:a=0[v]"

    overlay_idx = len(image_paths)
    ffmpeg_cmd.extend([
        "-filter_complex", filter_complex,
        "-map", f"[v]",
        "-map", f"{overlay_idx}:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        "-pix_fmt", "yuv420p",
        str(output_path)
    ])

    try:
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        return f"ERROR: ffmpeg failed: {e.stderr.decode()[:200]}"
    except FileNotFoundError:
        return f"ERROR: ffmpeg not found at {FFMPEG_PATH}"

    return str(output_path)
