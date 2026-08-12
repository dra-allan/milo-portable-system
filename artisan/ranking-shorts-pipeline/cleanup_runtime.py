"""Remove disposable ranking build inputs.

The pipeline now does this automatically after every successful build (see
src/cleanup.py). This script stays for the panel's manual purge and for
cleaning up after a crashed run.

Keeps final exports, plans, database, logs and OAuth tokens. Deletes downloaded
candidate clips, per-build voice files and FFmpeg stage/temp files.
"""
from src.cleanup import disk_report, purge_runtime


def clean() -> int:
    print(f'before: {disk_report()}')
    removed = purge_runtime(reason='manual', force=True)
    print(f'after:  {disk_report()}')
    print(f'CLEANUP complete: removed {removed} disposable runtime item(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(clean())
