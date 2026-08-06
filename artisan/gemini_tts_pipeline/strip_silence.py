import os
import sys
import json
from pathlib import Path
import warnings

# Suppress pydub warnings about ffmpeg during startup
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub")

try:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False

# Default Config
CONFIG = {
    "remove_silence": True,
    "min_silence_len": 1000,
    "silence_thresh": -45,
    "keep_silence": 300,
    "output_format": "mp3"
}

# Load config.json if exists
CONFIG_PATH = Path("config.json")
if CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH, "r") as f:
            CONFIG.update(json.load(f))
    except:
        pass

def setup_ffmpeg():
    """Locates ffmpeg and adds its directory to the system PATH."""
    possible_paths = [
        "ffmpeg/bin/ffmpeg.exe",
        "bin/ffmpeg.exe",
        "ffmpeg.exe"
    ]
    root = Path(".")
    for folder in root.glob("ffmpeg-*"):
        if folder.is_dir():
            possible_paths.append(str(folder / "bin" / "ffmpeg.exe"))
    
    for p in possible_paths:
        full_p = Path(p).absolute()
        if full_p.exists():
            AudioSegment.converter = str(full_p)
            # Add the directory to PATH so pydub can find ffprobe.exe
            bin_dir = str(full_p.parent)
            if bin_dir not in os.environ["PATH"]:
                os.environ["PATH"] += os.pathsep + bin_dir
            return True
    return False

def strip_silence(file_path):
    if not HAS_LIBS:
        print("❌ Error: 'pydub' not installed. Run 'pip install pydub'")
        return

    if not setup_ffmpeg():
        print("❌ Error: FFmpeg not found. Ensure it is in the project folder.")
        return

    path = Path(file_path)
    if not path.exists():
        print(f"❌ Error: File not found: {file_path}")
        return

    print(f"🔊 Loading {path.name}...")
    audio = AudioSegment.from_file(str(path))
    
    print(f"✂️  Trimming silence (Threshold: {CONFIG['silence_thresh']}dB, Min Len: {CONFIG['min_silence_len']}ms)...")
    chunks = split_on_silence(
        audio,
        min_silence_len=CONFIG["min_silence_len"],
        silence_thresh=CONFIG["silence_thresh"],
        keep_silence=CONFIG["keep_silence"]
    )
    
    if not chunks:
        print("⚠️ Warning: Entire file was considered silence! Nothing saved.")
        return

    combined = AudioSegment.empty()
    for chunk in chunks:
        combined += chunk
    
    output_path = path.parent / f"{path.stem}_cleaned.{CONFIG['output_format']}"
    combined.export(str(output_path), format=CONFIG["output_format"])
    
    reduction = len(audio) - len(combined)
    print(f"✅ Success!")
    print(f"  Original Length: {len(audio)/1000:.2f}s")
    print(f"  Cleaned Length:  {len(combined)/1000:.2f}s")
    print(f"  Removed:         {reduction/1000:.2f}s of dead air")
    print(f"  Saved to:        {output_path.name}")

if __name__ == "__main__":
    print("--- Standalone Silence Remover ---")
    if len(sys.argv) > 1:
        # File passed as argument
        strip_silence(sys.argv[1])
    else:
        # Ask for path
        file_input = input("Enter path to audio file (or drag & drop): ").strip().strip('"')
        if file_input:
            strip_silence(file_input)
        else:
            print("No file provided.")
    input("\nPress Enter to exit...")
