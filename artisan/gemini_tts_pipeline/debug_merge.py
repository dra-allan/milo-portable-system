import os
import sys
from pathlib import Path
import warnings

# Suppress pydub warnings about ffmpeg during startup
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pydub")

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

def check_ffmpeg():
    possible_paths = [
        "ffmpeg/bin/ffmpeg.exe",
        "ffmpeg-8.1.1/bin/ffmpeg.exe",
        "bin/ffmpeg.exe",
        "ffmpeg.exe"
    ]
    
    found_path = None
    for p in possible_paths:
        full_p = Path(p).absolute()
        if full_p.exists():
            found_path = str(full_p)
            break
            
    if not found_path:
        try:
            import subprocess
            subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            found_path = "SYSTEM_PATH"
        except:
            pass
            
    return found_path

print(f"Python Version: {sys.version}")
print(f"HAS_PYDUB: {HAS_PYDUB}")
ffmpeg_status = check_ffmpeg()
print(f"FFMPEG Status: {ffmpeg_status if ffmpeg_status else 'NOT FOUND'}")

if HAS_PYDUB and ffmpeg_status:
    if ffmpeg_status != "SYSTEM_PATH":
        AudioSegment.converter = ffmpeg_status
    print("Pre-requisites for merging seem OK.")
else:
    print("Pre-requisites for merging are MISSING.")
