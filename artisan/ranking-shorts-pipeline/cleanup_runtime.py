"""Remove disposable ranking build inputs after a finished build.

Keeps final exports, plans, database, logs and OAuth tokens. Deletes downloaded
candidate clips, per-build voice files and FFmpeg stage/temp files.
"""
import shutil
from pathlib import Path
from src.config import config

def clean():
    removed=0
    for path in (config.clips_dir, config.vo_dir, config.temp_dir):
        if not Path(path).exists():
            continue
        for item in list(Path(path).iterdir()):
            try:
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
                removed += 1
            except OSError:
                pass
    print(f"CLEANUP complete: removed {removed} disposable runtime item(s)")
    print(f"KEPT output: {config.output_dir}")
    print(f"KEPT plans/database/logs: {config.data_dir}")
if __name__ == '__main__': clean()
