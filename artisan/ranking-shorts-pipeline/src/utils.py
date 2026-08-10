"""Logging, path and FFmpeg helpers.

Every FFmpeg call in this pipeline goes through :func:`run_ffmpeg` so that a
failure produces the command *and* the tail of stderr in the log. Diagnosing a
broken filtergraph from "returned non-zero" alone is not possible, and
filtergraphs are where all the risk lives here.
"""

import json
import logging
import os
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


def which_ffmpeg() -> str:
    exe = os.getenv('FFMPEG_BINARY') or shutil.which('ffmpeg')
    if not exe:
        raise RuntimeError(
            'ffmpeg not found on PATH. Install it and re-run '
            '(`choco install ffmpeg` on Windows).')
    return exe


def which_ffprobe() -> str:
    exe = os.getenv('FFPROBE_BINARY') or shutil.which('ffprobe')
    if not exe:
        raise RuntimeError('ffprobe not found on PATH.')
    return exe


def run_ffmpeg(args: Sequence[str], timeout: int = 1800) -> bool:
    """Run ffmpeg with the given args (binary is prepended). True on success."""
    cmd = [which_ffmpeg(), '-hide_banner', '-nostdin', '-y'] + list(args)
    logger.debug('ffmpeg %s', ' '.join(cmd[1:]))
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error('ffmpeg timed out after %ss', timeout)
        return False
    if proc.returncode != 0:
        err = proc.stderr.decode('utf-8', 'replace').strip().splitlines()
        logger.error('ffmpeg failed (rc=%s)', proc.returncode)
        for line in err[-12:]:
            logger.error('  | %s', line)
        logger.error('  cmd: %s', ' '.join(cmd))
        return False
    return True


def run_ffmpeg_capture(args: Sequence[str], timeout: int = 600) -> str:
    """Run ffmpeg and return stderr. Used by the analysis filters, which
    report their measurements on stderr rather than producing a file."""
    cmd = [which_ffmpeg(), '-hide_banner', '-nostdin'] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning('ffmpeg analysis timed out')
        return ''
    return proc.stderr.decode('utf-8', 'replace')


def ffprobe_json(path: str, extra: Optional[List[str]] = None) -> dict:
    cmd = [which_ffprobe(), '-v', 'error', '-print_format', 'json',
           '-show_format', '-show_streams'] + (extra or []) + [str(path)]
    try:
        out = subprocess.check_output(cmd, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning('ffprobe failed for %s: %s', path, exc)
        return {}
    try:
        return json.loads(out.decode('utf-8', 'replace'))
    except json.JSONDecodeError:
        return {}


def probe_duration(path: str) -> float:
    info = ffprobe_json(path)
    try:
        return float(info.get('format', {}).get('duration', 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def probe_media(path: str) -> dict:
    """Return {duration, width, height, fps, has_audio}.

    ``has_audio`` is load-bearing: a silent source (very common on scraped
    clips) has no audio stream at all, and mapping ``[0:a]`` on one of those
    fails the whole render. The assembler substitutes silence instead.
    """
    info = ffprobe_json(path)
    out = {'duration': 0.0, 'width': 0, 'height': 0, 'fps': 0.0,
           'has_audio': False}
    try:
        out['duration'] = float(info.get('format', {}).get('duration') or 0.0)
    except (TypeError, ValueError):
        pass
    for stream in info.get('streams', []):
        kind = stream.get('codec_type')
        if kind == 'video' and not out['width']:
            out['width'] = int(stream.get('width') or 0)
            out['height'] = int(stream.get('height') or 0)
            rate = stream.get('avg_frame_rate') or stream.get('r_frame_rate')
            if rate and '/' in str(rate):
                num, den = str(rate).split('/')
                try:
                    out['fps'] = float(num) / float(den) if float(den) else 0.0
                except (ValueError, ZeroDivisionError):
                    out['fps'] = 0.0
        elif kind == 'audio':
            out['has_audio'] = True
    return out


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_slug(text: str, limit: int = 48) -> str:
    keep = [c.lower() if c.isalnum() else '_' for c in (text or '')]
    slug = ''.join(keep)
    while '__' in slug:
        slug = slug.replace('__', '_')
    return slug.strip('_')[:limit] or 'untitled'
