"""Self-contained background music auto-sync for YouTube Shorts pipeline.

Uses yt-dlp (imported via Python library) to fetch copyright-free background
music beds from NoCopyrightSounds (NCS) YouTube channels / playlists into
config.music_dir.
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

# Default NCS / copyright-free music sources
NCS_MUSIC_SOURCES = [
    "https://www.youtube.com/@NoCopyrightSounds/videos",
    "https://www.youtube.com/results?search_query=NoCopyrightSounds+background+music",
    "https://www.youtube.com/@ncsops/videos",
]

MUSIC_EXTENSIONS = ('.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.opus')


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


def sync_ncs_music(music_dir: Optional[str | Path] = None, min_tracks: int = 5, max_new_tracks: int = 5) -> List[Path]:
    """Ensure music_dir has at least `min_tracks` available.

    If fewer than `min_tracks` exist, uses yt-dlp to download audio from NCS
    sources until the count is met or `max_new_tracks` have been fetched.
    """
    target_dir = Path(music_dir or getattr(config, 'music_dir', 'data/music'))
    target_dir.mkdir(parents=True, exist_ok=True)

    existing = get_existing_music_tracks(target_dir)
    if len(existing) >= min_tracks:
        logger.debug("Music dir %s has %d tracks (min required: %d)", target_dir, len(existing), min_tracks)
        return existing

    logger.info(
        "Music dir %s has only %d track(s) (target min %d). Auto-syncing background music from NCS...",
        target_dir, len(existing), min_tracks
    )

    try:
        import yt_dlp
    except ImportError as exc:
        logger.error("yt-dlp is not available for music auto-sync: %s", exc)
        return existing

    clients = [c.strip() for c in (os.getenv('YTDLP_PLAYER_CLIENTS') or 'android_vr,ios,web_safari').split(',') if c.strip()]

    # Pick a source URL
    source_url = random.choice(NCS_MUSIC_SOURCES)

    opts = {
        'format': 'bestaudio/best',
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'playlistend': 20,
        'extractor_args': {'youtube': {'player_client': clients}},
    }

    cookies_file = (os.getenv('YTDLP_COOKIES_FILE') or '').strip()
    if cookies_file and Path(cookies_file).exists():
        opts['cookiefile'] = cookies_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source_url, download=False)
    except Exception as exc:
        logger.warning("Failed to list music tracks from %s: %s", source_url, exc)
        return existing

    entries = (info or {}).get('entries') or []
    if not entries:
        logger.warning("No entries found in music source %s", source_url)
        return existing

    # Shuffle candidates to get variety
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
        safe_title = re.sub(r'[^\w\s-]', '', raw_title)[:40].strip()
        out_filename = f"ncs_{vid}_{safe_title}.mp3"
        out_path = target_dir / out_filename

        if out_path.exists() and out_path.stat().st_size > 0:
            continue

        dl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(target_dir / f"ncs_{vid}_%(title).40s.%(ext)s"),
            'quiet': True,
            'no_warnings': True,
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
            logger.info("Downloading NCS background music bed: %s (%s)", raw_title, vid)
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([video_url])
            downloaded += 1
            time.sleep(1.0)
        except Exception as exc:
            logger.warning("Failed downloading music track %s: %s", vid, exc)
            continue

    updated = get_existing_music_tracks(target_dir)
    logger.info("Music auto-sync completed: %d total tracks in %s", len(updated), target_dir)
    return updated


if __name__ == '__main__':
    # Quick standalone CLI verification
    tracks = sync_ncs_music(min_tracks=3, max_new_tracks=3)
    print(f"Music tracks sync finished. Found {len(tracks)} tracks:")
    for t in tracks:
        print(f" - {t.name}")
