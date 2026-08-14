"""Disk hygiene.

Asymmetric on purpose:

* **temp is disposable immediately.** Text sheets and per-clip work dirs have no
  value once the clip is rendered and validated.
* **outputs are not disposable at upload.** The MP4 has to survive until its
  link is accepted by the campaign board, which is a step *after* upload and one
  that can fail on its own. Deleting on upload, the way the ranking lane does,
  would leave nothing to retry or review with.
* **sources are kept by default.** A campaign content folder can disappear or be
  rotated by the advertiser, and re-downloading 30 files to make one more clip is
  slow. They are purged only on request.
"""

import shutil
from pathlib import Path
from typing import Dict, Optional

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__)


def _size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def _human(size: int) -> str:
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.1f}{unit}'
        value /= 1024
    return f'{value:.1f}GB'


def purge_temp(campaign_id: Optional[str] = None) -> int:
    target = (config.campaign_temp_dir(campaign_id) if campaign_id
              else config.temp_dir)
    freed = _size(target)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    logger.info('PURGE_TEMP freed=%s dir=%s', _human(freed), target)
    return freed


def purge_sources(campaign_id: str) -> int:
    target = config.campaign_source_dir(campaign_id)
    freed = _size(target)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    logger.info('PURGE_SOURCES campaign=%s freed=%s', campaign_id,
                _human(freed))
    return freed


def purge_submitted(db) -> int:
    """Delete local MP4s whose links are already accepted by the board.

    The database row survives (campaign, window, caption, video id), so the
    reuse guard and the history stay intact after the file is gone.
    """
    freed = 0
    for row in db.clips_by_status('submitted', limit=500):
        path = Path(row.get('local_path') or '')
        if path.exists():
            freed += path.stat().st_size
            try:
                path.unlink()
            except OSError:
                pass
    logger.info('PURGE_SUBMITTED freed=%s', _human(freed))
    return freed


def disk_report() -> Dict[str, str]:
    report = {name: _human(_size(path)) for name, path in (
        ('sources', config.sources_dir), ('assets', config.assets_dir),
        ('temp', config.temp_dir), ('output', config.output_dir),
        ('data', config.data_dir))}
    for name, value in report.items():
        logger.info('DISK %-8s %s', name, value)
    return report


def after_build(campaign_id: str) -> None:
    if config.cleanup_after_build:
        purge_temp(campaign_id)
