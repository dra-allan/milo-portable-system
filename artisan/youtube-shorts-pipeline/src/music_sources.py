"""Self-contained instrumental background music auto-sync for YouTube Shorts pipeline.

Uses yt-dlp (imported via Python library) to fetch copyright-free INSTRUMENTAL
background music beds (no vocals) from YouTube / NCS sources into config.music_dir.

HARD RULES:
- Max track duration: 600 seconds (10 minutes). Anything longer is a stream/compilation.
- Max file size: 50 MB. Safety net against multi-hour lofi streams.
- Instrumental only: reject titles with vocals/features/lyrics.
"""

import os
import re
import random
import time
from pathlib import Path
from typing import List, Optional

try:
    from .config import config
    from .utils import setup_logger
except ImportError:  # pragma: no cover
    from config import config
    from utils import setup_logger

logger = setup_logger(__name__)

# ── Duration & size gates ──────────────────────────────────────────
MAX_TRACK_DURATION_SECS = 600   # 10 min — real NCS tracks are 2-5 min
MAX_FILESIZE_BYTES = 50_000_000  # 50 MB — catches anything that slipped past duration

# Search queries targeting SHORT, INDIVIDUAL instrumental tracks (not compilations/streams)
NCS_MUSIC_SOURCES = [
    "ytsearch15:NCS instrumental no copyright short track",
    "ytsearch15:copyright free lofi instrumental short beat",
    "ytsearch15:royalty free ambient background music short",
    "ytsearch15:NCS release instrumental 2024",
    "ytsearch15:free instrumental beat no vocals background",
]

MUSIC_EXTENSIONS = ('.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.opus')


def _safe_str(s: str) -> str:
    """Strip non-ASCII characters that crash cp1252 Windows terminals."""
    return s.encode('ascii', errors='replace').decode('ascii')


def get_existing_music_tracks(music_dir: str | Path) -> List[Path]:
    """Return all valid music files currently present in music_dir."""
    d = Path(music_dir)
    if not d.exists():
        return []
    tracks = []
    for p in d.iterdir():
        if p.is_file() and p.suffix.lower() in MUSIC_EXTENSIONS and p.stat().st_size > 0:
            tracks.append(p)
    return tracks


def is_instrumental_title(title: str) -> bool:
    """Return True if title indicates instrumental / background audio suitable for voiceovers."""
    low = title.lower()
    # Hard reject: anything clearly vocal
    vocal_signals = ['feat.', 'ft.', 'vocals', 'lyric', 'lyrics', 'singing',
                     'official video', 'music video', 'live performance']
    for sig in vocal_signals:
        if sig in low:
            return False
    # Hard reject: compilations / mixes / streams (they bypass duration filter at metadata stage)
    compilation_signals = ['hour', 'hours', 'compilation', 'mix 20', 'mix 30',
                           'mega mix', '1 hr', '2 hr', '3 hr', 'live stream',
                           'study music', '24/7']
    for sig in compilation_signals:
        if sig in low:
            return False
    # Positive signals (not required, but boost confidence)
    return True


def sync_ncs_music(music_dir: Optional[str | Path] = None, min_tracks: int = 5, max_new_tracks: int = 5) -> List[Path]:
    """Ensure music_dir has at least `min_tracks` instrumental tracks available.

    If fewer than `min_tracks` exist, uses yt-dlp to download instrumental audio
    from NCS/copyright-free sources. Enforces duration and filesize limits.
    """
    target_dir = Path(music_dir or getattr(config, 'music_dir', 'data/music'))
    target_dir.mkdir(parents=True, exist_ok=True)

    existing = get_existing_music_tracks(target_dir)
    if len(existing) >= min_tracks:
        logger.debug("Music dir %s has %d tracks (min required: %d)", target_dir, len(existing), min_tracks)
        return existing

    logger.info(
        "Music dir %s has only %d instrumental track(s) (target min %d). Auto-syncing...",
        target_dir, len(existing), min_tracks
    )

    try:
        import yt_dlp
    except ImportError as exc:
        logger.error("yt-dlp is not available for music auto-sync: %s", exc)
        return existing

    clients = [c.strip() for c in (os.getenv('YTDLP_PLAYER_CLIENTS') or 'android_vr,ios,web_safari').split(',') if c.strip()]

    source_query = random.choice(NCS_MUSIC_SOURCES)

    # Phase 1: list candidates (metadata only, no download)
    list_opts = {
        'format': 'bestaudio/best',
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': clients}},
    }

    cookies_file = (os.getenv('YTDLP_COOKIES_FILE') or '').strip()
    if cookies_file and Path(cookies_file).exists():
        list_opts['cookiefile'] = cookies_file

    try:
        with yt_dlp.YoutubeDL(list_opts) as ydl:
            info = ydl.extract_info(source_query, download=False)
    except Exception as exc:
        logger.warning("Failed to list music tracks from %s: %s", source_query, exc)
        return existing

    entries = (info or {}).get('entries') or []
    if not entries:
        logger.warning("No entries found in music source: %s", source_query)
        return existing

    random.shuffle(entries)

    downloaded = 0
    needed = max(1, min(min_tracks - len(existing), max_new_tracks))

    for entry in entries:
        if downloaded >= needed:
            break
        vid = entry.get('id')
        if not vid or len(vid) != 11:
            continue

        raw_title = entry.get('title') or vid

        # Duration gate (metadata-level, before downloading)
        duration = entry.get('duration')
        if duration and duration > MAX_TRACK_DURATION_SECS:
            logger.debug("Skipping too-long track (%ds): %s", duration, _safe_str(raw_title))
            continue

        # Instrumental/title check
        if not is_instrumental_title(raw_title):
            logger.debug("Skipping vocal/compilation track: %s", _safe_str(raw_title))
            continue

        safe_title = re.sub(r'[^\w\s-]', '', raw_title)[:40].strip()
        out_filename = f"ncs_instrumental_{vid}_{safe_title}.mp3"
        out_path = target_dir / out_filename

        if out_path.exists() and out_path.stat().st_size > 0:
            continue

        # Phase 2: download individual track with hard duration + size limits
        dl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(target_dir / f"ncs_instrumental_{vid}_%(title).40s.%(ext)s"),
            'quiet': True,
            'no_warnings': True,
            # ── HARD SAFETY GATES ──
            'match_filter': yt_dlp.utils.match_filter_func(
                f'duration <= {MAX_TRACK_DURATION_SECS}'
            ),
            'max_filesize': MAX_FILESIZE_BYTES,
            # ── Post-processing ──
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'extractor_args': {'youtube': {'player_client': clients}},
        }
        if cookies_file and Path(cookies_file).exists():
            dl_opts['cookiefile'] = cookies_file

        video_url = f"https://www.youtube.com/watch?v={vid}"
        try:
            logger.info("Downloading instrumental bed: %s (%s)", _safe_str(raw_title), vid)
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([video_url])
            downloaded += 1
            time.sleep(1.5)
        except Exception as exc:
            logger.warning("Failed downloading music track %s: %s", vid, exc)
            continue

    # Clean up any partial .part files left by interrupted downloads
    for p in target_dir.glob('*.part'):
        try:
            p.unlink()
            logger.debug("Cleaned up partial download: %s", p.name)
        except OSError:
            pass

    updated = get_existing_music_tracks(target_dir)
    logger.info("Music auto-sync done: %d instrumental tracks in %s", len(updated), target_dir)
    return updated


if __name__ == '__main__':
    # Quick standalone CLI verification
    tracks = sync_ncs_music(min_tracks=5, max_new_tracks=5)
    print(f"Instrumental music sync finished. Found {len(tracks)} tracks:")
    for t in tracks:
        print(f" - {t.name}")
