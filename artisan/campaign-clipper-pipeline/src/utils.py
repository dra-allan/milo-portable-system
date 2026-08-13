"""Logging, path and FFmpeg helpers for the campaign clipper.

The FFmpeg resolution contract is copied from the ranking and Shorts lanes on
purpose: all three must use the *same* binary. A clipper that renders with a
different FFmpeg build than the one the render tests were measured against will
quietly produce different output for identical config.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

_LOG_CONFIGURED = False


def setup_logger(name: str, log_file: Optional[Path] = None) -> logging.Logger:
    global _LOG_CONFIGURED
    logger = logging.getLogger(name)
    if not _LOG_CONFIGURED:
        level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(),
                        logging.INFO)
        fmt = logging.Formatter(
            '%(asctime)s %(levelname)-7s [%(name)s] %(message)s',
            datefmt='%H:%M:%S')
        root = logging.getLogger()
        root.setLevel(level)
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        root.addHandler(stream)
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding='utf-8')
            fh.setFormatter(fmt)
            root.addHandler(fh)
        _LOG_CONFIGURED = True
    return logger


logger = setup_logger(__name__)


def _configured_binary(primary: str, legacy: str,
                       name: str) -> Optional[str]:
    value = os.getenv(primary) or os.getenv(legacy)
    if value:
        path = str(Path(value).expanduser())
        if Path(path).exists():
            return path
        raise RuntimeError(f'{name} override does not exist: {path}')
    return shutil.which(name)


def which_ffmpeg() -> str:
    exe = _configured_binary('MILO_FFMPEG', 'FFMPEG_BINARY', 'ffmpeg')
    if not exe:
        raise RuntimeError('ffmpeg not found. Set MILO_FFMPEG to the shared '
                           'FFmpeg binary or add it to PATH.')
    return exe


def which_ffprobe() -> str:
    exe = _configured_binary('MILO_FFPROBE', 'FFPROBE_BINARY', 'ffprobe')
    if not exe:
        raise RuntimeError('ffprobe not found. Set MILO_FFPROBE to the shared '
                           'FFprobe binary or add it to PATH.')
    return exe


def run_ffmpeg(args: Sequence[str], timeout: int = 1800) -> bool:
    cmd = [which_ffmpeg(), '-hide_banner', '-nostdin', '-y'] + list(args)
    logger.debug('ffmpeg %s', ' '.join(cmd[1:]))
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error('FFMPEG_TIMEOUT seconds=%s', timeout)
        return False
    if proc.returncode != 0:
        err = proc.stderr.decode('utf-8', 'replace').strip().splitlines()
        logger.error('FFMPEG_FAILED exit=%s', proc.returncode)
        for line in err[-12:]:
            logger.error('  | %s', line)
        logger.error('FFMPEG_COMMAND %s', ' '.join(cmd))
        return False
    return True


def run_ffmpeg_capture(args: Sequence[str], timeout: int = 600) -> str:
    """Run FFmpeg for its stderr only; analysis filters print there."""
    cmd = [which_ffmpeg(), '-hide_banner', '-nostdin'] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning('FFMPEG_ANALYSIS_TIMEOUT seconds=%s', timeout)
        return ''
    return proc.stderr.decode('utf-8', 'replace')


def ffprobe_json(path: str, extra: Optional[List[str]] = None) -> dict:
    cmd = [which_ffprobe(), '-v', 'error', '-print_format', 'json',
           '-show_format', '-show_streams'] + (extra or []) + [str(path)]
    try:
        out = subprocess.check_output(cmd, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning('FFPROBE_FAILED path=%s error=%s', path, exc)
        return {}
    try:
        return json.loads(out.decode('utf-8', 'replace'))
    except json.JSONDecodeError:
        return {}


def probe_media(path: str) -> dict:
    """Duration / geometry / codec / audio presence for one file.

    Every compliance fact that can be measured from the container is measured
    here, because the validator must never trust what the renderer *intended*.
    """
    info = ffprobe_json(path)
    out = {'duration': 0.0, 'width': 0, 'height': 0, 'fps': 0.0,
           'has_audio': False, 'has_video': False, 'vcodec': '',
           'acodec': '', 'size': 0}
    try:
        out['duration'] = float(info.get('format', {}).get('duration') or 0.0)
    except (TypeError, ValueError):
        pass
    try:
        out['size'] = int(info.get('format', {}).get('size') or 0)
    except (TypeError, ValueError):
        pass
    for stream in info.get('streams', []):
        if stream.get('codec_type') == 'video' and not out['width']:
            out['has_video'] = True
            out['width'] = int(stream.get('width') or 0)
            out['height'] = int(stream.get('height') or 0)
            out['vcodec'] = str(stream.get('codec_name') or '')
            rate = stream.get('avg_frame_rate') or stream.get('r_frame_rate')
            if rate and '/' in str(rate):
                num, den = str(rate).split('/')
                out['fps'] = float(num) / float(den) if float(den) else 0.0
        elif stream.get('codec_type') == 'audio':
            out['has_audio'] = True
            out['acodec'] = out['acodec'] or str(stream.get('codec_name') or '')
    return out


def probe_duration(path: str) -> float:
    return probe_media(path).get('duration', 0.0)


_SCENE_RE = re.compile(r'pts_time:([0-9.]+)')


def scene_times(path: str, threshold: float = 0.25,
                timeout: int = 900) -> List[float]:
    """Timestamps where the picture changes hard, as cheaply as possible.

    Decoded at 160px wide: scene-change detection only needs gross frame
    difference, and a full-resolution decode of a long source is the single
    most expensive thing this pipeline could do. Same reasoning as the ranking
    lane's 480p proxies.
    """
    out = run_ffmpeg_capture(
        ['-i', str(path), '-vf',
         'scale=160:-2,' + "select='gt(scene," + f'{threshold}' + ")',"
         'metadata=print',
         '-an', '-f', 'null', '-'], timeout=timeout)
    times: List[float] = []
    for match in _SCENE_RE.finditer(out):
        try:
            times.append(float(match.group(1)))
        except ValueError:
            continue
    return sorted(set(times))


_LOUD_RE = re.compile(r'astats\.Overall\.RMS_level=(-?[0-9.]+)')


def window_loudness(path: str, start: float, duration: float) -> float:
    """Mean RMS dB for one window; -99 when silent or audio-less.

    Used to prefer windows where something is actually happening. This is a
    ranking signal, never a compliance check.
    """
    out = run_ffmpeg_capture(
        ['-ss', f'{start:.3f}', '-t', f'{duration:.3f}', '-i', str(path),
         '-vn', '-af', 'astats=metadata=1:reset=0,ametadata=print',
         '-f', 'null', '-'], timeout=180)
    values = []
    for match in _LOUD_RE.finditer(out):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue
    return max(values) if values else -99.0


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_slug(text: str, limit: int = 48) -> str:
    slug = ''.join(c.lower() if c.isalnum() else '_' for c in (text or ''))
    while '__' in slug:
        slug = slug.replace('__', '_')
    return slug.strip('_')[:limit] or 'untitled'


def file_fingerprint(path, chunk: int = 1024 * 1024) -> str:
    """Cheap stable identity for a source file: size + head/tail digest.

    Full-file hashing a folder of 40 MB sources on every run is wasted IO, and
    campaign content folders are static once published.
    """
    p = Path(path)
    h = hashlib.sha256()
    size = p.stat().st_size
    h.update(str(size).encode())
    with p.open('rb') as fh:
        h.update(fh.read(chunk))
        if size > chunk * 2:
            fh.seek(-chunk, os.SEEK_END)
            h.update(fh.read(chunk))
    return h.hexdigest()[:32]


def write_json(path, payload) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, default=str),
                   encoding='utf-8')
    tmp.replace(p)


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return default


def quote_filter_path(path) -> str:
    """Quote a path for a filtergraph option value.

    FFmpeg's filtergraph parser strips single quotes before the option splitter
    runs, so a bare ``C:/...`` still breaks on the drive-letter colon. The
    verified form is single quotes AND a backslash-escaped colon.
    """
    return "'" + str(path).replace('\\', '/').replace(':', '\\:') + "'"


VIDEO_EXTS = ('.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi', '.mpg',
              '.mpeg', '.ts')
IMAGE_EXTS = ('.png', '.webp', '.jpg', '.jpeg')


def iter_videos(folder) -> List[Path]:
    root = Path(folder)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob('*')
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def iter_images(folder) -> List[Path]:
    root = Path(folder)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob('*')
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
