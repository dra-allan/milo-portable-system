"""Logging, path and FFmpeg helpers for the ranking pipeline."""
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

# When running under pythonw.exe (scheduled daemon), child processes like
# ffmpeg and yt-dlp are console apps, so each spawn flashes a console window
# on the desktop. Force CREATE_NO_WINDOW on every subprocess in this process.
# Must stay a Popen *subclass* (not a wrapper function) because asyncio and
# other stdlib code subclass subprocess.Popen.
if os.name == 'nt' and not getattr(subprocess, '_milo_no_window', False):
    class _NoWindowPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen
    subprocess._milo_no_window = True
from typing import List, Optional, Sequence
_LOG_CONFIGURED=False

def setup_logger(name: str, log_file: Optional[Path]=None)->logging.Logger:
    global _LOG_CONFIGURED
    logger=logging.getLogger(name)
    if not _LOG_CONFIGURED:
        level=getattr(logging,os.getenv('LOG_LEVEL','INFO').upper(),logging.INFO)
        fmt=logging.Formatter('%(asctime)s %(levelname)-7s [%(name)s] %(message)s',datefmt='%H:%M:%S')
        root=logging.getLogger(); root.setLevel(level); stream=logging.StreamHandler(sys.stdout); stream.setFormatter(fmt); root.addHandler(stream)
        if log_file:
            log_file.parent.mkdir(parents=True,exist_ok=True); fh=logging.FileHandler(log_file,encoding='utf-8'); fh.setFormatter(fmt); root.addHandler(fh)
        _LOG_CONFIGURED=True
    return logger
logger=setup_logger(__name__)

def _configured_binary(primary: str, legacy: str, name: str) -> Optional[str]:
    """Use the same override names as the main Shorts pipeline.

    Shorts uses MILO_FFMPEG/MILO_FFPROBE; FFMPEG_BINARY/FFPROBE_BINARY remain
    accepted for compatibility. This prevents the ranking renderer from
    silently using a different PATH installation.
    """
    value=os.getenv(primary) or os.getenv(legacy)
    if value:
        path=str(Path(value).expanduser())
        if Path(path).exists(): return path
        raise RuntimeError(f'{name} override does not exist: {path}')
    return shutil.which(name)

def which_ffmpeg()->str:
    exe=_configured_binary('MILO_FFMPEG','FFMPEG_BINARY','ffmpeg')
    if not exe: raise RuntimeError('ffmpeg not found. Set MILO_FFMPEG to the shared FFmpeg binary or add it to PATH.')
    return exe

def which_ffprobe()->str:
    exe=_configured_binary('MILO_FFPROBE','FFPROBE_BINARY','ffprobe')
    if not exe: raise RuntimeError('ffprobe not found. Set MILO_FFPROBE to the shared FFprobe binary or add it to PATH.')
    return exe

def run_ffmpeg(args: Sequence[str], timeout: int=1800)->bool:
    cmd=[which_ffmpeg(),'-hide_banner','-nostdin','-y']+list(args)
    logger.debug('ffmpeg %s',' '.join(cmd[1:]))
    try: proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    except subprocess.TimeoutExpired: logger.error('FFMPEG_TIMEOUT seconds=%s',timeout); return False
    if proc.returncode!=0:
        err=proc.stderr.decode('utf-8','replace').strip().splitlines(); logger.error('FFMPEG_FAILED exit=%s',proc.returncode)
        for line in err[-12:]: logger.error('  | %s',line)
        logger.error('FFMPEG_COMMAND %s',' '.join(cmd)); return False
    return True

def run_ffmpeg_capture(args: Sequence[str], timeout: int=600)->str:
    cmd=[which_ffmpeg(),'-hide_banner','-nostdin']+list(args)
    try: proc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=timeout)
    except subprocess.TimeoutExpired: logger.warning('FFMPEG_ANALYSIS_TIMEOUT seconds=%s',timeout); return ''
    return proc.stderr.decode('utf-8','replace')

def ffprobe_json(path: str, extra: Optional[List[str]]=None)->dict:
    cmd=[which_ffprobe(),'-v','error','-print_format','json','-show_format','-show_streams']+(extra or [])+[str(path)]
    try: out=subprocess.check_output(cmd,timeout=120)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc: logger.warning('FFPROBE_FAILED path=%s error=%s',path,exc); return {}
    try: return json.loads(out.decode('utf-8','replace'))
    except json.JSONDecodeError: return {}

def probe_duration(path: str)->float:
    try: return float(ffprobe_json(path).get('format',{}).get('duration',0.0) or 0.0)
    except (TypeError,ValueError): return 0.0

def probe_media(path: str)->dict:
    info=ffprobe_json(path); out={'duration':0.0,'width':0,'height':0,'fps':0.0,'has_audio':False}
    try: out['duration']=float(info.get('format',{}).get('duration') or 0.0)
    except (TypeError,ValueError): pass
    for stream in info.get('streams',[]):
        if stream.get('codec_type')=='video' and not out['width']:
            out['width']=int(stream.get('width') or 0); out['height']=int(stream.get('height') or 0); rate=stream.get('avg_frame_rate') or stream.get('r_frame_rate')
            if rate and '/' in str(rate):
                num,den=str(rate).split('/'); out['fps']=float(num)/float(den) if float(den) else 0.0
        elif stream.get('codec_type')=='audio': out['has_audio']=True
    return out

def ensure_dir(path)->Path:
    p=Path(path); p.mkdir(parents=True,exist_ok=True); return p

def safe_slug(text: str,limit: int=48)->str:
    slug=''.join(c.lower() if c.isalnum() else '_' for c in (text or ''))
    while '__' in slug: slug=slug.replace('__','_')
    return slug.strip('_')[:limit] or 'untitled'
