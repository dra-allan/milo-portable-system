"""Getting campaign source material onto local disk.

The pipeline works on **video files**, not links. Whatever the campaign's
content folder is (Drive share, Discord drop, a folder you filled by hand), the
job of this module is to turn it into a directory of local files plus rows in
the database, and then get out of the way.

Why two download backends
-------------------------
``rclone`` is used when a remote is configured because it does incremental sync
properly and survives the folders with 30+ files. ``gdown --folder`` is the
fallback because it needs no configuration at all, which matters the first time
you touch a new campaign. Neither is driven through a browser: Drive's web UI
is the most fragile possible way to fetch a file, and these folders are plain
shared links.

Why the local folder route is not a workaround
----------------------------------------------
Several campaigns publish their content pool inside the campaign Discord. There
is no shareable folder URL and nothing to scrape. So "drop the files in
``local_folders``" is a supported, equal ingest path, and a campaign that needs
it is marked ``manual_only`` in its spec so the run refuses early with a reason
instead of rendering nothing.
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .config import config
from .spec import CampaignSpec
from .utils import (ensure_dir, file_fingerprint, iter_images, iter_videos,
                   probe_media, setup_logger)

logger = setup_logger(__name__)

_FOLDER_ID = re.compile(r'/folders/([A-Za-z0-9_-]{10,})')
_FILE_ID = re.compile(r'/file/d/([A-Za-z0-9_-]{10,})')


def folder_id(url: str) -> Optional[str]:
    match = _FOLDER_ID.search(url or '') or _FILE_ID.search(url or '')
    return match.group(1) if match else None


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _run(cmd: List[str], timeout: int) -> bool:
    logger.info('FETCH_RUN %s', ' '.join(cmd[:3]) + ' ...')
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error('FETCH_FAILED error=%s', exc)
        return False
    if proc.returncode != 0:
        tail = proc.stdout.decode('utf-8', 'replace').strip().splitlines()
        logger.error('FETCH_FAILED exit=%s', proc.returncode)
        for line in tail[-8:]:
            logger.error('  | %s', line)
        return False
    return True


def _fetch_rclone(url: str, dest: Path) -> bool:
    fid = folder_id(url)
    if not (config.rclone_remote and fid and _have(config.rclone_bin)):
        return False
    return _run([config.rclone_bin, 'copy',
                 f'{config.rclone_remote}:', str(dest),
                 '--drive-root-folder-id', fid,
                 '--drive-shared-with-me', '--ignore-existing',
                 '--transfers', '4', '--checkers', '8'],
                timeout=config.download_timeout)


def _fetch_gdown(url: str, dest: Path) -> bool:
    if not _have(config.gdown_bin):
        logger.error('FETCH_NO_BACKEND install gdown or configure '
                     'RCLONE_REMOTE')
        return False
    folder = '/folders/' in (url or '')
    cmd = [config.gdown_bin]
    if folder:
        # gdown 6.x dropped --remaining-ok; a single failed file then fails the
        # whole run. Call gdown per-folder and let callers decide on exit code.
        cmd += ['--folder', '--continue']
    cmd += ['-O', str(dest) if folder else str(dest / 'download'), url]
    return _run(cmd, timeout=config.download_timeout)


def fetch_folder(url: str, dest: Path) -> bool:
    """Pull one share link into ``dest``. rclone first, gdown as fallback."""
    ensure_dir(dest)
    if _fetch_rclone(url, dest):
        return True
    return _fetch_gdown(url, dest)


def sync_sources(spec: CampaignSpec, db, refresh: bool = False) -> List[Dict]:
    """Ensure the campaign's source pool exists locally; return the rows.

    Skipped entirely when files are already present unless ``refresh`` is set.
    Campaign content folders are static once published, so re-pulling 30 files
    every run would dominate runtime and buy nothing.
    """
    dest = config.campaign_source_dir(spec.id)
    existing = iter_videos(dest)

    if spec.sources.content_folders and (refresh or not existing):
        for url in spec.sources.content_folders:
            if not fetch_folder(url, dest):
                logger.error('SOURCE_FETCH_FAILED campaign=%s url=%s',
                             spec.id, url)
    elif existing and not refresh:
        logger.info('SOURCE_CACHE_HIT campaign=%s files=%d', spec.id,
                    len(existing))

    # Local folders are mirrored rather than read in place so a build can never
    # be affected by the operator moving or renaming files mid-run.
    for folder in spec.sources.local_folders:
        src = Path(folder).expanduser()
        if not src.exists():
            logger.warning('LOCAL_FOLDER_MISSING campaign=%s path=%s',
                           spec.id, src)
            continue
        for video in iter_videos(src):
            target = dest / video.name
            if not target.exists():
                shutil.copy2(video, target)

    rows: List[Dict] = []
    for video in iter_videos(dest):
        media = probe_media(str(video))
        if not media['has_video'] or media['duration'] <= 0:
            logger.warning('SOURCE_UNREADABLE file=%s', video.name)
            continue
        fingerprint = file_fingerprint(video)
        db.register_source(spec.id, fingerprint, video.name, str(video),
                           media['duration'])
        rows.append({'fingerprint': fingerprint, 'filename': video.name,
                     'local_path': str(video), 'duration': media['duration'],
                     'width': media['width'], 'height': media['height'],
                     'has_audio': media['has_audio']})
    logger.info('SOURCES_READY campaign=%s usable=%d dir=%s', spec.id,
                len(rows), dest)
    return rows


def sync_logo(spec: CampaignSpec, refresh: bool = False) -> Optional[Path]:
    """Pull the campaign logo and return the best local image, if any.

    Prefers PNG: campaign logo folders usually ship a transparent PNG next to a
    flattened JPEG preview, and compositing the JPEG puts a white box on the
    clip. Largest file wins within a format as a proxy for highest resolution,
    since these overlays get scaled to ~14% of a 1080px frame and a 200px
    source looks obviously cheap.
    """
    if not spec.assets.logo_folders:
        return None
    dest = config.campaign_asset_dir(spec.id)
    images = iter_images(dest)
    if refresh or not images:
        for url in spec.assets.logo_folders:
            if not fetch_folder(url, dest):
                logger.error('LOGO_FETCH_FAILED campaign=%s url=%s',
                             spec.id, url)
        images = iter_images(dest)
    if not images:
        logger.warning('LOGO_MISSING campaign=%s dir=%s', spec.id, dest)
        return None
    png = [p for p in images if p.suffix.lower() == '.png']
    pool = png or images
    best = max(pool, key=lambda p: p.stat().st_size)
    logger.info('LOGO_READY campaign=%s file=%s', spec.id, best.name)
    return best


def adopt_file(spec: CampaignSpec, path) -> Optional[Dict]:
    """Add one arbitrary local video to a campaign's pool.

    The escape hatch for "I already have the file": a Discord attachment, a
    hand-trimmed source, anything. Same treatment as a downloaded file.
    """
    src = Path(path).expanduser()
    if not src.exists():
        logger.error('ADOPT_MISSING path=%s', src)
        return None
    dest = config.campaign_source_dir(spec.id) / src.name
    if not dest.exists():
        shutil.copy2(src, dest)
    media = probe_media(str(dest))
    return {'fingerprint': file_fingerprint(dest), 'filename': dest.name,
            'local_path': str(dest), 'duration': media['duration'],
            'width': media['width'], 'height': media['height'],
            'has_audio': media['has_audio']}
