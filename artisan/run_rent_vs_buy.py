#!/usr/bin/env python3
r"""
Runner script for RENT_VS_BUY Money Matrix video.
Orchestrates: TTS generation -> Audio merge -> Video assembly.

Prerequisites:
- GEMINI_API_KEY environment variable set (for TTS)
- PEXELS_API_KEY environment variable set (for stock footage, optional)
- FFmpeg at C:\Users\user\Desktop\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\bin\ffmpeg.exe
- Python packages: google-genai, pydub, numpy, pillow, matplotlib, python-dotenv, tqdm, requests
"""

import os
import sys
import subprocess
from pathlib import Path

# Add project paths
ARTISAN = Path(__file__).parent
GEMINI_TTS_DIR = ARTISAN / "gemini_tts_pipeline"
MM_PIPELINE_DIR = ARTISAN / "mm_pipeline"
TOPIC_DIR = MM_PIPELINE_DIR / "RENT_VS_BUY"
OUTPUT_DIR = ARTISAN.parent.parent / "output"

# Scripts
GEMINI_TTS = GEMINI_TTS_DIR / "gemini_tts.py"
MERGE_SCRIPT = GEMINI_TTS_DIR / "merge_with_ffmpeg.py"
MM_ASSEMBLER = MM_PIPELINE_DIR / "mm_video_assembler.py"

# Input files
SCRIPT_TTS = TOPIC_DIR / "02_SCRIPT_TTS.txt"
VISUALS = TOPIC_DIR / "03_VISUALS.txt"

def check_prerequisites():
    """Check that all required files and tools exist."""
    missing = []
    
    for f in [GEMINI_TTS, MERGE_SCRIPT, MM_ASSEMBLER, SCRIPT_TTS, VISUALS]:
        if not f.exists():
            missing.append(str(f))
    
    ffmpeg = Path(r"C:\Users\user\Desktop\ffmpeg-2026-05-18-git-b4d11dffbf-full_build\bin\ffmpeg.exe")
    if not ffmpeg.exists():
        missing.append(f"FFmpeg: {ffmpeg}")
    
    if not os.environ.get("GEMINI_API_KEY"):
        print("[WARN] GEMINI_API_KEY not set - TTS will fail")
    
    if missing:
        print("[ERROR] Missing prerequisites:")
        for m in missing:
            print(f"  - {m}")
        return False
    
    return True


def run_tts():
    """Run Gemini TTS to generate audio segments."""
    print("\n" + "="*60)
    print("  STEP 1: Generating TTS Audio Segments")
    print("="*60)
    
    audio_dir = OUTPUT_DIR / "audio" / "gemini_tts"
    audio_dir.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        sys.executable, str(GEMINI_TTS),
        "--script", str(SCRIPT_TTS),
        "--audio-dir", str(audio_dir),
        "--format", "wav",
        "--voice", "Charon",
        "--force",
    ]
    
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "")
    
    print(f"[TTS] Running: {' '.join(cmd[:4])} ...")
    result = subprocess.run(cmd, cwd=str(GEMINI_TTS_DIR), env=env,
                           capture_output=True, text=True, timeout=3600)
    
    print(result.stdout[-3000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-1500:])
    
    if result.returncode != 0:
        print("[TTS] FAILED")
        return False
    
    # Copy segment WAVs to project's tts_segments directory for the assembler
    video_id = ""
    with open(SCRIPT_TTS, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("VIDEO_ID:"):
                video_id = line.split(":", 1)[1].strip()
                break
    
    if video_id:
        src_seg_dir = audio_dir / video_id
        dst_seg_dir = TOPIC_DIR / "tts_segments" / video_id
        dst_seg_dir.mkdir(parents=True, exist_ok=True)
        
        import shutil
        for wav_file in src_seg_dir.glob("*.wav"):
            if not wav_file.name.startswith("_"):
                shutil.copy2(wav_file, dst_seg_dir / wav_file.name)
                print(f"[TTS] Copied {wav_file.name} -> {dst_seg_dir}")
    
    print("[TTS] SUCCESS")
    return True


def merge_audio():
    """Merge audio segments into single WAV."""
    print("\n" + "="*60)
    print("  STEP 2: Merging Audio Segments")
    print("="*60)
    
    # Extract VIDEO_ID from script
    video_id = ""
    with open(SCRIPT_TTS, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("VIDEO_ID:"):
                video_id = line.split(":", 1)[1].strip()
                break
    
    if not video_id:
        print("[MERGE] ERROR: Could not find VIDEO_ID in script")
        return False
    
    audio_dir = OUTPUT_DIR / "audio" / "gemini_tts"
    seg_dir = audio_dir / video_id
    FINAL_AUDIO_DIR = OUTPUT_DIR / "audio"
    FINAL_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    merged_path = FINAL_AUDIO_DIR / f"{video_id}.wav"
    
    if not seg_dir.exists():
        print(f"[MERGE] ERROR: Segment directory not found: {seg_dir}")
        return False
    
    # Import and run merge
    import importlib.util
    spec = importlib.util.spec_from_file_location("merge_ffmpeg", MERGE_SCRIPT)
    merge_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge_mod)
    
    ok = merge_mod.merge_audio_files(seg_dir, merged_path)
    if ok:
        print(f"[MERGE] SUCCESS: {merged_path}")
        # Also copy to project directory for reference
        import shutil
        project_merged = TOPIC_DIR / f"{video_id}.wav"
        shutil.copy2(merged_path, project_merged)
        print(f"[MERGE] Copied to project: {project_merged}")
        return True
    else:
        print("[MERGE] FAILED")
        return False


def assemble_video():
    """Run the video assembler."""
    print("\n" + "="*60)
    print("  STEP 3: Assembling Video")
    print("="*60)
    
    # The mm_video_assembler expects the topic directory name
    cmd = [sys.executable, str(MM_ASSEMBLER), "RENT_VS_BUY"]
    
    print(f"[VIDEO] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(MM_PIPELINE_DIR),
                           capture_output=True, text=True, timeout=3600)
    
    print(result.stdout[-5000:] if result.stdout else "")
    if result.stderr:
        print("STDERR:", result.stderr[-2000:])
    
    if result.returncode != 0:
        print("[VIDEO] FAILED")
        return False
    
    print("[VIDEO] SUCCESS")
    return True


def main():
    print("="*60)
    print("  MONEY MATRIX PIPELINE: RENT VS BUY")
    print("="*60)
    print(f"Topic directory: {TOPIC_DIR}")
    print(f"Output directory: {OUTPUT_DIR}")
    
    if not check_prerequisites():
        sys.exit(1)
    
    # Step 1: TTS
    if not run_tts():
        print("\n[ERROR] TTS generation failed. Check GEMINI_API_KEY.")
        sys.exit(1)
    
    # Step 2: Merge audio
    if not merge_audio():
        print("\n[ERROR] Audio merge failed.")
        sys.exit(1)
    
    # Step 3: Video assembly
    if not assemble_video():
        print("\n[ERROR] Video assembly failed.")
        sys.exit(1)
    
    print("\n" + "="*60)
    print("  PIPELINE COMPLETE!")
    print("="*60)
    print(f"Output video: {OUTPUT_DIR / 'RENT_VS_BUY_FINAL.mp4'}")
    print(f"Audio: {OUTPUT_DIR / 'audio' / 'MM-RENTBUY-2026-001.wav'}")


if __name__ == "__main__":
    main()