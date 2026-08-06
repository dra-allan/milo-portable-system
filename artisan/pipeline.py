import asyncio
import subprocess
import sys
from pathlib import Path
from milo.artisan.scribe import generate_script
from milo.artisan.echo import generate_voiceover
from milo.artisan.cutter import assemble_video
from milo.artisan.canvas import create_thumbnail

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output"
MM_PIPELINE_DIR = Path(__file__).parent / "gemini_tts_pipeline"


def run_pipeline(topic: str, style: str = "educational", duration: int = 10, tts_mode: str = "standard") -> dict:
    result = {"topic": topic, "status": "started", "artifacts": {}}

    try:
        if tts_mode == "premium":
            result = _run_premium_pipeline(topic, style, duration, result)
        else:
            result = _run_standard_pipeline(topic, style, duration, result)

        # Common step: thumbnail
        thumb_path = create_thumbnail(topic, topic.replace(" ", "_")[:30])
        result["artifacts"]["thumbnail"] = thumb_path
        print(f"  [Canvas] Thumbnail created: {thumb_path}")

        # Common step: video assembly
        voice_path = result["artifacts"].get("voiceover", "")
        if voice_path:
            image_dir = OUTPUT_DIR / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            placeholder = image_dir / "placeholder.png"
            if not placeholder.exists():
                from PIL import Image
                img = Image.new("RGB", (1920, 1080), "#0A1628")
                img.save(str(placeholder))

            images = [str(placeholder)] * 5
            clean_name = "".join(c if c.isalnum() or c in " -_" else "" for c in topic)[:30]
            video_path = assemble_video(voice_path, images, clean_name)
            result["artifacts"]["video"] = video_path
            if video_path.startswith("ERROR"):
                result["status"] = "partial"
                print(f"  [Cutter] Warning: {video_path}")
            else:
                print(f"  [Cutter] Video assembled: {video_path}")

        if result["status"] == "started":
            result["status"] = "complete"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    return result


def _run_standard_pipeline(topic: str, style: str, duration: int, result: dict) -> dict:
    script_path = generate_script(topic, style, duration)
    result["artifacts"]["script"] = script_path
    print(f"  [Scribe] Script written: {script_path}")

    voice_path = asyncio.run(generate_voiceover(script_path))
    result["artifacts"]["voiceover"] = voice_path
    print(f"  [Echo] Voiceover generated: {voice_path}")
    return result


def _run_premium_pipeline(topic: str, style: str, duration: int, result: dict) -> dict:
    import json
    mm_runner = MM_PIPELINE_DIR / "run_mm_pipeline.py"
    cmd = [sys.executable, str(mm_runner), topic, str(duration), style]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print(proc.stdout)
    if proc.stderr:
        print("STDERR:", proc.stderr[:500], file=sys.stderr)

    for line in proc.stdout.splitlines():
        if line.startswith("MM_RESULT:"):
            mm_result = json.loads(line[len("MM_RESULT:"):])
            result["artifacts"].update(mm_result.get("artifacts", {}))
            result["status"] = mm_result.get("status", "failed")
            break

    if "voiceover" not in result["artifacts"]:
        raise RuntimeError("Premium pipeline failed to produce voiceover")
    return result
