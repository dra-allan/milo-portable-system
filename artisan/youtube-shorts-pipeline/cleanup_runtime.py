"""Clean disposable source assets after a completed Shorts activity.

Keeps rendered Shorts, transcripts, clip plans, database, logs and the local
library. Deletes audio-only discovery files and fetched clip sections, which
are regenerable and are the large temporary assets that cause disk pressure.
"""
import shutil
from pathlib import Path
from src.config import config

def clean():
    removed=0
    for name in ('audio','sections'):
        path=Path(config.temp_dir)/name
        if not path.exists(): continue
        for item in list(path.iterdir()):
            try:
                if item.is_dir(): shutil.rmtree(item)
                else: item.unlink()
                removed += 1
            except OSError: pass
    print(f"CLEANUP complete: removed {removed} disposable audio/section item(s)")
    print(f"KEPT rendered Shorts: {config.shorts_dir}")
    print(f"KEPT library, transcripts, plans, database and logs: {config.data_dir}")
if __name__ == '__main__': clean()
