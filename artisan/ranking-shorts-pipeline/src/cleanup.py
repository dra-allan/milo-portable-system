"""Disk hygiene.

Two separate jobs, both automatic, because the pipeline is a disk hog by
design: a five-clip build downloads five 1080p sources, renders five stage
files, then stitches a sixth. Nothing removed them, so every run left ~150MB
of material that can never be reused (used clips are retired in the database,
so they are dead weight the moment the stitch succeeds).

* ``purge_runtime()`` - after a build. Deletes downloaded candidate clips,
  per-build voice files and FFmpeg stage/temp renders. Keeps output, plans,
  database, logs and OAuth tokens.
* ``delete_local_video()`` - after an upload. The finished mp4 has a YouTube
  id, so the local copy is a duplicate. The build row (title, plan, youtube
  id) survives, so history is intact without the bytes.

Both are toggles, not policy: RANKING_CLEANUP_AFTER_BUILD and
RANKING_DELETE_AFTER_UPLOAD (or cleanup_after_build / delete_after_upload in
ranking.yaml). Turn the first one off when you want ``--mode assemble`` to
re-render from a saved plan, which needs the source clips still on disk.
"""

import shutil
from pathlib import Path
from typing import Iterable, Optional, Union

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__, config.log_dir / 'ranking.log')


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob('*'):
            if item.is_file():
                total += item.stat().st_size
    except OSError:
        pass
    return total


def _purge_dir(path: Path, keep: Iterable[str] = ()) -> int:
    """Empty one directory without removing the directory itself."""
    if not path.exists():
        return 0
    keep = set(keep)
    removed = 0
    for item in list(path.iterdir()):
        if item.name in keep:
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except OSError as exc:  # a file still held by ffmpeg, next run gets it
            logger.warning('CLEANUP_SKIP %s: %s', item, exc)
    return removed


def purge_runtime(reason: str = 'build', force: bool = False) -> int:
    """Delete disposable build inputs. Returns the number of items removed."""
    if not (force or config.cleanup_after_build):
        logger.info('CLEANUP_DISABLED reason=%s (set '
                    'RANKING_CLEANUP_AFTER_BUILD=true to reclaim space)',
                    reason)
        return 0

    targets = [Path(p) for p in (config.clips_dir, config.vo_dir,
                                config.temp_dir)]
    freed = sum(_dir_bytes(p) for p in targets)
    removed = sum(_purge_dir(p) for p in targets)
    logger.info('CLEANUP_RUNTIME reason=%s removed=%d freed_mb=%.1f',
                reason, removed, freed / 1048576)
    return removed


def delete_local_video(path: Optional[Union[str, Path]],
                       force: bool = False) -> bool:
    """Delete one finished export whose upload is confirmed."""
    if not path:
        return False
    if not (force or config.delete_after_upload):
        return False
    target = Path(path)
    try:
        size = target.stat().st_size
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning('CLEANUP_WARN %s: %s', target, exc)
        return False
    logger.info('CLEANUP_UPLOADED removed=%s freed_mb=%.1f',
                target.name, size / 1048576)
    return True


def disk_report() -> str:
    """One line of what the runtime directories are currently holding."""
    parts = []
    for label, path in (('clips', config.clips_dir), ('vo', config.vo_dir),
                       ('temp', config.temp_dir),
                       ('output', config.output_dir)):
        parts.append(f'{label}={_dir_bytes(Path(path)) / 1048576:.1f}MB')
    return ' '.join(parts)


if __name__ == '__main__':
    print(disk_report())
    purge_runtime(reason='manual', force=True)
    print(disk_report())
