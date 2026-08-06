import os
import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT_DIR = HERE.parent.parent.parent / "output"

MM_SCRIBE = HERE / "money_matrix_scribe.py"
GEMINI_TTS = HERE / "gemini_tts.py"
MERGE_SCRIPT = HERE / "merge_with_ffmpeg.py"

FINAL_AUDIO_DIR = OUTPUT_DIR / "audio"


def run_pipeline(topic_key: str = "index_funds", duration_minutes: int = 10, subtitle: str = "") -> dict:
    result = {"topic": topic_key, "status": "started", "artifacts": {}}

    try:
        print(f"\n{'='*60}")
        print(f"  MONEY MATRIX PIPELINE")
        print(f"  Topic: {topic_key}")
        print(f"  Duration: {duration_minutes} min")
        print(f"{'='*60}\n")

        # Step 1: Generate script
        print("[1/3] Generating script...")
        import importlib.util
        spec = importlib.util.spec_from_file_location("mm_scribe", MM_SCRIBE)
        mm_scribe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mm_scribe)
        script_path = mm_scribe.generate_script(topic_key, duration_minutes, subtitle)
        result["artifacts"]["script"] = script_path
        print(f"  -> {script_path}")

        # Step 2: Generate TTS audio segments
        print("\n[2/3] Generating TTS audio segments...")
        audio_dir = OUTPUT_DIR / "audio" / "gemini_tts"
        audio_dir.mkdir(parents=True, exist_ok=True)

        tts_cmd = [
            sys.executable,
            str(GEMINI_TTS),
            "--script", script_path,
            "--audio-dir", str(audio_dir),
            "--format", "wav",
        ]
        env = os.environ.copy()
        env["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "")

        result_tts = subprocess.run(tts_cmd, cwd=str(HERE), capture_output=True, text=True, timeout=600, env=env)
        print(result_tts.stdout)
        if result_tts.stderr:
            print("STDERR:", result_tts.stderr[:500], file=sys.stderr)

        # Step 3: Merge all audio segments via ffmpeg
        print("\n[3/3] Merging audio segments...")
        merge_mod = importlib.util.module_from_spec(
            importlib.util.spec_from_file_location("merge_ffmpeg", MERGE_SCRIPT)
        )
        merge_mod.__spec__.loader.exec_module(merge_mod)

        video_id = ""
        with open(script_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VIDEO_ID:"):
                    video_id = line.split(":", 1)[1].strip()
                    break

        seg_dir = audio_dir / video_id if video_id else audio_dir
        FINAL_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        merged_path = FINAL_AUDIO_DIR / f"{video_id}.wav"

        if seg_dir.exists():
            ok = merge_mod.merge_audio_files(seg_dir, merged_path)
            if ok:
                result["artifacts"]["voiceover"] = str(merged_path)
        else:
            result["artifacts"]["voiceover"] = str(seg_dir) if seg_dir.exists() else "no audio generated"

        result["status"] = "complete"
        print(f"\n{'='*60}")
        print(f"  Pipeline complete!")
        print(f"{'='*60}\n")

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        print(f"\n[ERROR] Pipeline failed: {e}")

    return result


if __name__ == "__main__":
    import json
    topic = sys.argv[1] if len(sys.argv) > 1 else "index_funds"
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    subtitle = sys.argv[3] if len(sys.argv) > 3 else ""
    result = run_pipeline(topic, duration, subtitle)
    # JSON result line for parent process parsing
    print(f"\nMM_RESULT:{json.dumps(result)}")
