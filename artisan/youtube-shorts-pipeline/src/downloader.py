"""YouTube download + a resumable local media library.

WHY THIS WAS REWRITTEN
----------------------
Allan's report: "the pipeline hasn't run end to end and it keeps on failing
even after downloading a full video. We need to make sure that the process can
also resume so that we use an already downloaded video."

Both halves of that were real bugs, and they compounded each other:

1. **The resume check could never succeed.** ``download_video()`` looked for
   existing files with ``temp_dir.glob(f"{video_id}.*")``, but immediately
   after downloading it *renamed the file to the video title*
   (``_rename_to_title``). So the file on disk was ``Never Gonna Give You
   Up.mp4`` while the next run searched for ``dQw4w9WgXcQ.*`` and found
   nothing. Every single run re-downloaded the full video. Reproduced:
   file on disk 'Never Gonna Give You Up.mp4', lookup for 'dQw4w9WgXcQ'
   returns [].

2. **When the glob *did* match, it picked the wrong file.** yt-dlp writes
   ``<id>.info.json``, ``<id>.en.vtt``, ``<id>.mp4`` and possibly
   ``<id>.f303.webm`` / ``<id>.part``. The code took the most recently
   *modified* match, and the ``.info.json`` is written last -- so
   ``video_path`` pointed at a JSON file. ffmpeg was then handed a JSON file
   as a video, audio extraction failed, and the run died *after* a successful
   download. Reproduced: glob returns ['..info.json', '..en.vtt', '..mp4',
   '..part'] and the code picked '.info.json'.
   ``.part`` files (aborted downloads) were also treated as finished videos.

3. **Every run hit the network even when nothing was needed.** Metadata was
   always fetched with ``extract_info`` before the existence check, so an
   offline/rate-limited/age-gated box could not process an already-downloaded
   file at all.

THE FIX
-------
A small JSON-backed media library (``data/library.json``) maps video_id ->
{path, title, metadata}. Files are stored as ``<video_id>__<safe title>.<ext>``
so the ID is always recoverable from the filename itself even if the index is
deleted. Lookup order:

    library index -> id-prefixed filename scan -> sidecar .info.json scan

Only real video containers count, ``.part``/``.ytdl`` are ignored, and a size
floor rejects truncated downloads. Metadata is served from the cached
``.info.json`` when present, so a resumed run needs no network at all.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

try:  # package-relative first (python -m src.main)
    from .utils import get_temp_dir, setup_logger, sanitize_filename
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import get_temp_dir, setup_logger, sanitize_filename
    from config import config

logger = setup_logger(__name__)

# Containers we accept as a finished, playable download.
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v', '.flv', '.ts'}
# Never treat these as a video: partial downloads and sidecars.
SKIP_EXTENSIONS = {'.part', '.ytdl', '.json', '.vtt', '.srt', '.ass', '.txt',
                   '.jpg', '.jpeg', '.png', '.webp', '.description', '.temp'}
# A "video" smaller than this is a stub or a failed download, not media.
MIN_VIDEO_BYTES = 64 * 1024

# Filenames are "<video_id>__<safe title>.<ext>" so the ID survives renaming.
ID_SEPARATOR = '__'


class YouTubeDownloader:
    def __init__(self):
        self.temp_dir = Path(get_temp_dir())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.library_path = Path(config.data_dir) / 'library.json'
        self.ffprobe = os.getenv('MILO_FFPROBE') or shutil.which('ffprobe') or 'ffprobe'

        self.ydl_opts = {
            'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
            # Download straight to the id-prefixed name: no post-hoc rename,
            # so the file is findable by ID forever.
            'outtmpl': str(self.temp_dir / f'%(id)s{ID_SEPARATOR}%(title).80B.%(ext)s'),
            'writeautosub': True,
            'writeinfojson': True,
            'subtitleslangs': ['en'],
            'skip_unavailable_fragments': True,
            'merge_output_format': 'mp4',
            'restrictfilenames': True,
            'continuedl': True,          # resume a half-finished download
            'noprogress': True,
            'quiet': True,
            'no_warnings': True,
        }

    # ------------------------------------------------------------------
    # Library index
    # ------------------------------------------------------------------
    def _load_library(self) -> Dict[str, Dict]:
        if not self.library_path.exists():
            return {}
        try:
            with open(self.library_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Could not read library index %s: %s", self.library_path, exc)
            return {}

    def _save_library(self, library: Dict[str, Dict]) -> None:
        try:
            self.library_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.library_path.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(library, f, ensure_ascii=False, indent=2)
            shutil.move(str(tmp), str(self.library_path))
        except Exception as exc:
            logger.warning("Could not write library index: %s", exc)

    def _remember(self, video_id: str, video_path: Path, metadata: Dict) -> None:
        library = self._load_library()
        library[video_id] = {
            'video_path': str(video_path),
            'title': metadata.get('title', ''),
            'duration': metadata.get('duration', 0),
            'uploader': metadata.get('uploader', ''),
            'upload_date': metadata.get('upload_date'),
            'subtitle_path': metadata.get('subtitle_path'),
        }
        self._save_library(library)

    # ------------------------------------------------------------------
    # Finding an already-downloaded file
    # ------------------------------------------------------------------
    def _is_usable_video(self, path: Path) -> bool:
        """A real, complete video container -- not a sidecar or a .part."""
        try:
            if not path.is_file():
                return False
            suffix = path.suffix.lower()
            if suffix in SKIP_EXTENSIONS or suffix not in VIDEO_EXTENSIONS:
                return False
            # '.part' can appear as a double extension: 'x.mp4.part'
            if any(s.lower() in SKIP_EXTENSIONS for s in path.suffixes):
                return False
            return path.stat().st_size >= MIN_VIDEO_BYTES
        except OSError:
            return False

    def find_local_video(self, video_id: str) -> Optional[Path]:
        """Return an already-downloaded video for this ID, or None.

        Three strategies, cheapest first. This is the resume path: if it
        returns a file, the pipeline must not touch the network.
        """
        # 1. The library index.
        entry = self._load_library().get(video_id) or {}
        recorded = entry.get('video_path')
        if recorded:
            p = Path(recorded)
            if self._is_usable_video(p):
                logger.info("Resume: library index hit -> %s", p.name)
                return p
            logger.info("Library index points at a missing/invalid file (%s); rescanning", recorded)

        # 2. Any file whose name starts with the video ID. Covers both the new
        #    "<id>__<title>.<ext>" scheme and plain yt-dlp "<id>.<ext>".
        candidates = [p for p in self.temp_dir.glob(f"{video_id}*") if self._is_usable_video(p)]

        # 3. Sidecar .info.json files -- catches files renamed by older
        #    versions of this code (the bug that broke resume in the first
        #    place), because the JSON still carries the real ID.
        if not candidates:
            for info_file in self.temp_dir.glob('*.info.json'):
                try:
                    with open(info_file, 'r', encoding='utf-8') as f:
                        info = json.load(f)
                except Exception:
                    continue
                if info.get('id') != video_id:
                    continue
                stem = info_file.name[:-len('.info.json')]
                for sibling in self.temp_dir.glob(f"{glob_escape(stem)}.*"):
                    if self._is_usable_video(sibling):
                        candidates.append(sibling)

        if not candidates:
            return None

        # Largest file wins: with separate video/audio streams the muxed
        # output is the big one. (The old code took newest-mtime, which is how
        # a .info.json ended up being used as the video.)
        candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
        logger.info("Resume: found existing download -> %s", candidates[0].name)
        return candidates[0]

    def _find_subtitle(self, video_path: Path, video_id: str) -> Optional[str]:
        for pattern in (f"{glob_escape(video_path.stem)}*.vtt",
                        f"{video_id}*.vtt",
                        f"{glob_escape(video_path.stem)}*.srt"):
            for candidate in self.temp_dir.glob(pattern):
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return str(candidate)
        return None

    def _find_info_json(self, video_path: Path, video_id: str) -> Optional[Path]:
        for candidate in (
            video_path.with_suffix('.info.json'),
            self.temp_dir / f"{video_path.stem}.info.json",
            self.temp_dir / f"{video_id}.info.json",
        ):
            if candidate.exists():
                return candidate
        for candidate in self.temp_dir.glob(f"{video_id}*.info.json"):
            return candidate
        return None

    def _probe_duration(self, video_path: Path) -> float:
        """Duration straight from the file, for when there is no metadata."""
        try:
            result = subprocess.run(
                [self.ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path)],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except Exception as exc:
            logger.debug("ffprobe duration failed for %s: %s", video_path.name, exc)
        return 0.0

    def _metadata_from_cache(self, video_id: str, video_path: Path) -> Optional[Dict]:
        """Rebuild metadata from the cached .info.json -- no network."""
        info_path = self._find_info_json(video_path, video_id)
        info = {}
        if info_path:
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    info = json.load(f) or {}
            except Exception as exc:
                logger.warning("Could not read cached metadata %s: %s", info_path.name, exc)

        if not info:
            entry = self._load_library().get(video_id) or {}
            if entry:
                info = {
                    'title': entry.get('title', ''),
                    'duration': entry.get('duration', 0),
                    'uploader': entry.get('uploader', ''),
                    'upload_date': entry.get('upload_date'),
                }

        duration = info.get('duration') or self._probe_duration(video_path)
        title = info.get('title') or _title_from_filename(video_path, video_id)

        return {
            'id': video_id,
            'title': title,
            'duration': duration,
            'upload_date': info.get('upload_date'),
            'description': info.get('description', '') or '',
            'uploader': info.get('uploader', '') or '',
            'video_path': str(video_path),
            'subtitle_path': self._find_subtitle(video_path, video_id),
            'thumbnail': info.get('thumbnail', '') or '',
            'tags': info.get('tags') or [],
            'from_cache': True,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def download_video(self, video_id: str, force_redownload: bool = False) -> Optional[Dict]:
        """Return metadata for ``video_id``, downloading only if we must.

        Resume-first: if the file is already on disk we return immediately,
        without any network call, so a re-run costs nothing.
        """
        # --- resume path: already on disk -------------------------------
        if not force_redownload:
            existing = self.find_local_video(video_id)
            if existing:
                metadata = self._metadata_from_cache(video_id, existing)
                if metadata:
                    size_mb = existing.stat().st_size / (1024 * 1024)
                    logger.info(
                        "Reusing downloaded video '%s' (%.1f MB, %.0fs) -- skipping download",
                        metadata['title'] or video_id, size_mb, metadata['duration'] or 0,
                    )
                    self._remember(video_id, existing, metadata)
                    return metadata

        # --- download path ----------------------------------------------
        try:
            import yt_dlp
        except ImportError as exc:
            logger.error("yt-dlp is not installed: %s (pip install yt-dlp)", exc)
            return None

        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("Downloading %s", url)

        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:
            logger.error("Download failed for %s: %s", video_id, exc)
            # A previous partial download may still be usable.
            salvaged = self.find_local_video(video_id)
            if salvaged:
                logger.warning("Using a previously downloaded copy of %s instead", video_id)
                return self._metadata_from_cache(video_id, salvaged)
            return None

        if not info:
            logger.error("yt-dlp returned no metadata for %s", video_id)
            return None

        video_path = self.find_local_video(video_id)
        if not video_path:
            # Fall back to the exact path yt-dlp reports.
            reported = (info.get('requested_downloads') or [{}])[0].get('filepath')
            if reported and self._is_usable_video(Path(reported)):
                video_path = Path(reported)
        if not video_path:
            logger.error(
                "Download reported success but no usable video file was found for %s "
                "in %s", video_id, self.temp_dir,
            )
            return None

        metadata = {
            'id': video_id,
            'title': info.get('title', '') or _title_from_filename(video_path, video_id),
            'duration': info.get('duration', 0) or self._probe_duration(video_path),
            'upload_date': info.get('upload_date'),
            'description': info.get('description', '') or '',
            'uploader': info.get('uploader', '') or '',
            'video_path': str(video_path),
            'subtitle_path': self._find_subtitle(video_path, video_id),
            'thumbnail': info.get('thumbnail', '') or '',
            'tags': info.get('tags') or [],
            'from_cache': False,
        }

        self._write_info_json(video_path, metadata)
        self._remember(video_id, video_path, metadata)
        logger.info(
            "Downloaded '%s' (%s, %.1f MB)",
            metadata['title'], video_path.name,
            video_path.stat().st_size / (1024 * 1024),
        )
        return metadata

    def _write_info_json(self, video_path: Path, metadata: Dict) -> None:
        """Sidecar metadata, so a later run can resume with no network."""
        info_path = video_path.with_suffix('.info.json')
        if info_path.exists():
            return          # yt-dlp already wrote the richer original
        try:
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Could not write %s: %s", info_path.name, exc)

    # ------------------------------------------------------------------
    def list_library(self) -> List[Dict]:
        """Every usable downloaded video, for the 'process from library' flow."""
        seen: Dict[str, Dict] = {}

        for video_id, entry in self._load_library().items():
            p = Path(entry.get('video_path', ''))
            if self._is_usable_video(p):
                seen[video_id] = {
                    'id': video_id,
                    'title': entry.get('title') or _title_from_filename(p, video_id),
                    'duration': entry.get('duration', 0),
                    'video_path': str(p),
                    'size_mb': round(p.stat().st_size / (1024 * 1024), 1),
                }

        # Also pick up files the index does not know about yet.
        for p in sorted(self.temp_dir.iterdir()):
            if not self._is_usable_video(p):
                continue
            video_id = _id_from_filename(p)
            if not video_id or video_id in seen:
                continue
            seen[video_id] = {
                'id': video_id,
                'title': _title_from_filename(p, video_id),
                'duration': self._probe_duration(p),
                'video_path': str(p),
                'size_mb': round(p.stat().st_size / (1024 * 1024), 1),
            }

        return sorted(seen.values(), key=lambda e: e['title'].lower())

    # ------------------------------------------------------------------
    def search_videos_by_channel(self, channel_id: str, published_after: str = '',
                                 max_results: int = 10) -> List[Dict]:
        """Recent uploads for a channel, via yt-dlp's flat playlist extractor.

        No API key required: every channel exposes an ``/videos`` playlist that
        yt-dlp can enumerate with ``extract_flat``, which is metadata-only and
        downloads nothing. This replaces the old stub that logged a warning and
        returned [] -- which made discovery/scheduled mode a no-op.
        """
        try:
            import yt_dlp
        except ImportError as exc:
            logger.error("yt-dlp is not installed: %s", exc)
            return []

        channel_id = (channel_id or '').strip()
        if not channel_id or channel_id.startswith('UCXXXXX'):
            logger.warning("Skipping placeholder channel id %r", channel_id)
            return []

        if channel_id.startswith('http'):
            url = channel_id.rstrip('/')
            if not url.endswith('/videos'):
                url += '/videos'
        elif channel_id.startswith('UC'):
            url = f"https://www.youtube.com/channel/{channel_id}/videos"
        else:
            handle = channel_id if channel_id.startswith('@') else f"@{channel_id}"
            url = f"https://www.youtube.com/{handle}/videos"

        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',
            'skip_download': True,
            'playlistend': max(1, int(max_results)),
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            logger.warning("Channel listing failed for %s: %s", url, exc)
            return []

        results: List[Dict] = []
        for entry in (info or {}).get('entries') or []:
            if not entry:
                continue
            vid = entry.get('id')
            if not vid or len(vid) != 11:
                continue
            results.append({
                'id': vid,
                'title': entry.get('title', '') or '',
                'duration': entry.get('duration') or 0,
                'url': entry.get('url') or f"https://www.youtube.com/watch?v={vid}",
                'channel_id': channel_id,
            })
            if len(results) >= max_results:
                break

        logger.info("Found %d recent video(s) for channel %s", len(results), channel_id)
        return results


# ----------------------------------------------------------------------
def glob_escape(value: str) -> str:
    """Escape glob metacharacters so titles with [] or * still match."""
    out = []
    for ch in str(value):
        out.append(f'[{ch}]' if ch in '*?[]' else ch)
    return ''.join(out)


def _id_from_filename(path: Path) -> Optional[str]:
    """Recover the video ID from '<id>__<title>.<ext>' or '<id>.<ext>'."""
    stem = path.stem
    for suffix in ('.info', '.en', '.f303', '.f251'):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if ID_SEPARATOR in stem:
        head = stem.split(ID_SEPARATOR, 1)[0]
        if len(head) == 11:
            return head
    if len(stem) == 11:
        return stem
    return None


def _title_from_filename(path: Path, video_id: str) -> str:
    """Best-effort title when metadata is unavailable."""
    stem = path.stem
    if ID_SEPARATOR in stem:
        tail = stem.split(ID_SEPARATOR, 1)[1]
        if tail:
            return tail.replace('_', ' ').strip()
    if stem and stem != video_id:
        return stem.replace('_', ' ').strip()
    return video_id
