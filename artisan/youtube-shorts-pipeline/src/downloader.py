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

PHASE 4: AUDIO-ONLY DISCOVERY + SECTION FETCH
---------------------------------------------
The full-video download was the second-biggest cost in the pipeline after
transcription, and almost all of it was wasted:

* **Discovery only ever needs the audio.** Finding the highlights is a
  transcription job. Pulling 1-2 GB of 1080p H.264 to decide *where* the good
  moments are, and then throwing 95% of those frames away, is pure overhead.
  ``DOWNLOAD_AUDIO_ONLY=true`` fetches ``bestaudio`` instead: ~40 MB for an
  hour of podcast rather than 1-2 GB, and no ffmpeg audio-extraction pass
  afterwards because the download *is* the audio.
* **Rendering only ever needs the chosen ranges.** ``DOWNLOAD_SECTIONS=true``
  fetches each selected clip as its own small file via yt-dlp's
  ``download_ranges``, so a 51-minute source costs ~5 x 40s of video instead
  of 51 minutes of it.

THE KEYFRAME-DRIFT TRAP (and why this design is immune to it)
------------------------------------------------------------
A stream-copy cut cannot start mid-GOP, so a section file actually begins at
the **keyframe preceding** the requested start -- an offset we do not know in
advance and which varies per source (commonly 0-10s). The naive composition of
"fetch section" + "captions from the full-source transcript" desyncs *every*
caption by that unknown drift, silently.

Two things make that impossible here:

1. ``download_section`` measures the drift instead of guessing it. The section
   is requested with ``SECTION_PADDING`` slack on both sides, then the file's
   real duration is probed: whatever exceeds the requested span is the lead-in
   the keyframe added. That yields ``clip_start_in_file`` -- where the wanted
   clip actually begins inside the file we just downloaded.
2. The renderer cuts the clip at ``clip_start_in_file`` **and** rebases the
   captions by the same number, both derived from the same file. So even if the
   drift estimate is off, the video and its captions move *together* and stay
   in sync. Sync no longer depends on the estimate being right.

Combined with the caption pass transcribing the section's own audio (see
``transcriber.transcribe_file``), captions are correct by construction rather
than by arithmetic.
"""

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:  # package-relative first (python -m src.main)
    from .utils import get_temp_dir, setup_logger, sanitize_filename
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import get_temp_dir, setup_logger, sanitize_filename
    from config import config

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logger = setup_logger(__name__)

# Containers we accept as a finished, playable download.
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v', '.flv', '.ts'}
# Audio-only containers. '.webm' and '.mp4' are deliberately absent even though
# YouTube serves audio in them: an extension cannot distinguish an audio-only
# webm from a video webm. Audio downloads are instead kept in their own
# directory, so the *location* classifies them and no guessing is required.
AUDIO_EXTENSIONS = {'.m4a', '.opus', '.mp3', '.ogg', '.oga', '.aac', '.wav',
                    '.flac', '.weba'}
# Never treat these as a video: partial downloads and sidecars.
SKIP_EXTENSIONS = {'.part', '.ytdl', '.json', '.vtt', '.srt', '.ass', '.txt',
                   '.jpg', '.jpeg', '.png', '.webp', '.description', '.temp'}
# A "video" smaller than this is a stub or a failed download, not media.
MIN_VIDEO_BYTES = 64 * 1024
# Audio is ~1/30th the size, so the video floor would reject valid audio.
MIN_AUDIO_BYTES = 8 * 1024

# Filenames are "<video_id>__<safe title>.<ext>" so the ID survives renaming.
ID_SEPARATOR = '__'

# Subdirectories of temp_dir. Keeping audio and clip sections apart from full
# downloads means find_local_video() can never mistake a 40 MB audio file or a
# 40-second clip for the full source.
AUDIO_SUBDIR = 'audio'
SECTIONS_SUBDIR = 'sections'


class YouTubeDownloader:
    def __init__(self):
        self.temp_dir = Path(get_temp_dir())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        # Audio and clip sections live in their own directories so that the
        # resume scan for a *full* download cannot pick them up by mistake.
        self.audio_dir = self.temp_dir / AUDIO_SUBDIR
        self.sections_dir = self.temp_dir / SECTIONS_SUBDIR
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.sections_dir.mkdir(parents=True, exist_ok=True)
        self.library_path = Path(config.data_dir) / 'library.json'
        # Dead-channel cache: channels that failed to list (wrong ID, no videos
        # tab, 404 handle) get remembered here so every sweep doesn't hammer
        # YouTube and re-log the same ERROR. Re-probed after a cooldown.
        self.dead_channels_path = Path(config.data_dir) / 'dead_channels.json'
        self._dead_channels = self._load_dead_channels()
        self.dead_channel_cooldown = int(
            getattr(config, 'dead_channel_cooldown_days', 14) or 14
        )
        self.ffprobe = os.getenv('MILO_FFPROBE') or shutil.which('ffprobe') or 'ffprobe'

        height = int(getattr(config, 'download_height', 1080) or 1080)
        # THE root cause of "the output video is low quality": this used to be
        # hard-coded to `18/best[height<=N]/best`. Format 18 is 640x360, and
        # because it is listed first yt-dlp picked it EVERY time regardless of
        # DOWNLOAD_HEIGHT. Every Short was therefore built from a 360p source
        # and upscaled to 1080x1920 -- no amount of encoder tuning downstream
        # can recover detail that was never downloaded.
        #
        # Correct order: best separate video+audio streams at or below the
        # configured height (this is where 1080p actually lives on YouTube;
        # progressive/combined formats stop at 360p), then progressive as a
        # fallback, then anything. `-S` style sorting is expressed via
        # `format_sort` so ties break toward higher bitrate rather than
        # whichever the extractor happened to list first.
        format_string = (
            f'bestvideo[height<={height}][vcodec!*=av01]+bestaudio/'
            f'bestvideo[height<={height}]+bestaudio/'
            f'best[height<={height}]/best'
        )
        self.ydl_opts = {
            # Format selection: try specific format first, then fall back
            'format': format_string,
            # Prefer higher resolution, then higher bitrate, and avoid AV1
            # (many ffmpeg builds decode it slowly or not at all).
            'format_sort': ['res', 'vbr', 'abr'],
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
            # Enhanced retry and fragmentation handling
            'extractor_retries': 5,
            'fragment_retries': 10,
            'retry_sleep': lambda n: min(60, 2 ** n),  # longer exponential backoff
            'sleep_interval': 5,
            'max_sleep_interval': 20,
            # Try to bypass age restrictions and regional blocks
            'age_limit': None,
            'bypass_geoblock': True,
            # Prefer formats that don't require DRM
            'prefer_free_formats': True,
            # Additional robustness flags
            'keep_video': True,
        }

    # ------------------------------------------------------------------
    # Shared yt-dlp option builders
    # ------------------------------------------------------------------
    def _audio_opts(self) -> Dict:
        """Options for the audio-only discovery fetch.

        No ``writeautosub`` and no re-encode: the goal is the smallest number
        of bytes that Whisper can read. faster-whisper decodes via ffmpeg, so
        the native m4a/opus stream is used as-is -- transcoding it to wav here
        would add an ffmpeg pass over the whole file for no benefit.
        """
        return {
            'format': 'bestaudio/best',
            'outtmpl': str(self.audio_dir / f'%(id)s{ID_SEPARATOR}%(title).80B.%(ext)s'),
            'writeinfojson': True,
            'skip_unavailable_fragments': True,
            'restrictfilenames': True,
            'continuedl': True,
            'noprogress': True,
            'quiet': True,
            'no_warnings': True,
        }

    def _section_opts(self, video_id: str, start: float, end: float) -> Dict:
        """Options for fetching a single clip range as its own small file.

        ``force_keyframes_at_cuts`` is deliberately NOT set. It makes yt-dlp
        re-encode the section so it starts exactly on the requested frame,
        which costs a full transcode of the range and defeats the point of
        fetching a small piece. Instead we accept the keyframe lead-in and
        *measure* it (see ``download_section``), which is free and exact.
        """
        from yt_dlp.utils import download_range_func

        height = int(getattr(config, 'download_height', 1080) or 1080)
        return {
            # Same reasoning as the full-download format above: prefer separate
            # streams (where the high-resolution renditions are) and sort ties
            # toward resolution then bitrate.
            'format': (f'bestvideo[height<={height}][vcodec!*=av01]+bestaudio/'
                       f'bestvideo[height<={height}]+bestaudio/'
                       f'best[height<={height}]/best'),
            'format_sort': ['res', 'vbr', 'abr'],
            'outtmpl': str(
                self.sections_dir
                / f'{video_id}{ID_SEPARATOR}sec_{int(round(start))}_{int(round(end))}.%(ext)s'
            ),
            'download_ranges': download_range_func(None, [(start, end)]),
            'merge_output_format': 'mp4',
            'skip_unavailable_fragments': True,
            'restrictfilenames': True,
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
            with open(self.library_path, 'r', encoding='utf-8-sig') as f:
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
                    with open(info_file, 'r', encoding='utf-8-sig') as f:
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
        # Search the media file's own directory first, then temp_dir. Audio
        # downloads live in temp_dir/audio, so a temp_dir-only search would
        # miss their sidecar and silently fall back to filename-derived titles.
        search_dirs = []
        for d in (video_path.parent, self.temp_dir):
            if d not in search_dirs:
                search_dirs.append(d)

        candidates = [video_path.with_suffix('.info.json')]
        for d in search_dirs:
            candidates.append(d / f"{video_path.stem}.info.json")
            candidates.append(d / f"{video_id}.info.json")
        for candidate in candidates:
            if candidate.exists():
                return candidate

        for d in search_dirs:
            for candidate in d.glob(f"{video_id}*.info.json"):
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
                with open(info_path, 'r', encoding='utf-8-sig') as f:
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
    # Audio-only discovery fetch
    # ------------------------------------------------------------------
    def _is_usable_audio(self, path: Path) -> bool:
        """A complete audio container -- not a sidecar or a partial download.

        Extension checking alone is not enough: YouTube serves audio in
        ``.webm`` and ``.mp4`` containers too, which are indistinguishable from
        video by name. Anything inside ``audio_dir`` that is not a known
        sidecar is therefore accepted, and the *directory* does the
        classifying.
        """
        try:
            if not path.is_file():
                return False
            if any(s.lower() in SKIP_EXTENSIONS for s in path.suffixes):
                return False
            suffix = path.suffix.lower()
            if suffix not in AUDIO_EXTENSIONS and suffix not in VIDEO_EXTENSIONS:
                return False
            return path.stat().st_size >= MIN_AUDIO_BYTES
        except OSError:
            return False

    def find_local_audio(self, video_id: str) -> Optional[Path]:
        """Return an already-downloaded audio file for this ID, or None."""
        candidates = [p for p in self.audio_dir.glob(f"{video_id}*")
                      if self._is_usable_audio(p)]
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
        return candidates[0]

    def download_audio(self, video_id: str,
                       force_redownload: bool = False) -> Optional[Dict]:
        """Fetch audio only, for the discovery/transcription pass.

        Returns the same metadata shape as ``download_video`` with an extra
        ``audio_path`` and ``audio_only=True``, and with ``video_path`` left
        empty -- there is no video yet. Callers render from clip sections
        fetched later, so nothing downstream needs the full source.

        This is the single biggest byte saving in the pipeline: ~40 MB for an
        hour of podcast instead of 1-2 GB, and it removes the ffmpeg audio
        extraction pass over the full source as a side effect.
        """
        if not force_redownload:
            existing = self.find_local_audio(video_id)
            if existing:
                metadata = self._audio_metadata(video_id, existing)
                if metadata:
                    logger.info(
                        "Resume: reusing downloaded audio for %s (%.1f MB) "
                        "-- skipping download",
                        video_id, existing.stat().st_size / (1024 * 1024),
                    )
                    return metadata

        try:
            import yt_dlp
        except ImportError as exc:
            logger.error("yt-dlp is not installed: %s (pip install yt-dlp)", exc)
            return None

        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("Fetching audio only for %s (discovery pass)", video_id)
        started = time.time()

        try:
            with yt_dlp.YoutubeDL(self._audio_opts()) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:
            logger.error("Audio download failed for %s: %s", video_id, exc)
            salvaged = self.find_local_audio(video_id)
            if salvaged:
                logger.warning("Using a previously downloaded audio copy of %s", video_id)
                return self._audio_metadata(video_id, salvaged)
            return None

        audio_path = self.find_local_audio(video_id)
        if not audio_path:
            reported = (info or {}).get('requested_downloads') or [{}]
            reported_path = reported[0].get('filepath')
            if reported_path and Path(reported_path).exists():
                audio_path = Path(reported_path)
        if not audio_path:
            logger.error("Audio download reported success but no file was found "
                         "for %s in %s", video_id, self.audio_dir)
            return None

        size_mb = audio_path.stat().st_size / (1024 * 1024)
        logger.info(
            "Audio ready: %s (%.1f MB in %.1fs)",
            audio_path.name, size_mb, time.time() - started,
        )

        metadata = self._audio_metadata(video_id, audio_path, info or {})
        if metadata:
            self._remember_audio(video_id, audio_path, metadata)
        return metadata

    def _audio_metadata(self, video_id: str, audio_path: Path,
                        info: Optional[Dict] = None) -> Dict:
        """Metadata for an audio-only fetch, from yt-dlp info or the cache."""
        if info is None:
            info = {}
            info_path = self._find_info_json(audio_path, video_id)
            if info_path:
                try:
                    with open(info_path, 'r', encoding='utf-8-sig') as f:
                        info = json.load(f) or {}
                except Exception as exc:
                    logger.warning("Could not read cached metadata %s: %s",
                                   info_path.name, exc)
            if not info:
                entry = self._load_library().get(video_id) or {}
                info = {
                    'title': entry.get('title', ''),
                    'duration': entry.get('duration', 0),
                    'uploader': entry.get('uploader', ''),
                    'upload_date': entry.get('upload_date'),
                }

        duration = info.get('duration') or self._probe_duration(audio_path)
        return {
            'id': video_id,
            'title': info.get('title') or _title_from_filename(audio_path, video_id),
            'duration': duration,
            'upload_date': info.get('upload_date'),
            'description': info.get('description', '') or '',
            'uploader': info.get('uploader', '') or '',
            # No full video on disk: rendering uses clip sections instead.
            'video_path': '',
            'audio_path': str(audio_path),
            'audio_only': True,
            'subtitle_path': None,
            'thumbnail': info.get('thumbnail', '') or '',
            'tags': info.get('tags') or [],
            'from_cache': False,
        }

    def _remember_audio(self, video_id: str, audio_path: Path,
                        metadata: Dict) -> None:
        """Record the audio fetch without clobbering a known full video path."""
        library = self._load_library()
        entry = dict(library.get(video_id) or {})
        entry.update({
            'audio_path': str(audio_path),
            'title': metadata.get('title', '') or entry.get('title', ''),
            'duration': metadata.get('duration', 0) or entry.get('duration', 0),
            'uploader': metadata.get('uploader', '') or entry.get('uploader', ''),
            'upload_date': metadata.get('upload_date') or entry.get('upload_date'),
        })
        entry.setdefault('video_path', '')
        library[video_id] = entry
        self._save_library(library)

    # ------------------------------------------------------------------
    # Section fetch (only the chosen clip ranges)
    # ------------------------------------------------------------------
    def _section_path(self, video_id: str, req_start: float,
                      req_end: float) -> Optional[Path]:
        """Find an already-downloaded section file for this exact request."""
        stem = f"{video_id}{ID_SEPARATOR}sec_{int(round(req_start))}_{int(round(req_end))}"
        for p in self.sections_dir.glob(f"{glob_escape(stem)}.*"):
            if self._is_usable_video(p):
                return p
        return None

    def download_section(self, video_id: str, start: float, end: float,
                         padding: Optional[float] = None,
                         force_redownload: bool = False) -> Optional[Dict]:
        """Fetch just [start, end] of a video as its own small file.

        Returns a dict describing where the wanted clip sits *inside the
        downloaded file*:

            {'path', 'clip_start_in_file', 'clip_duration',
             'file_duration', 'requested_start', 'requested_end', 'lead_in'}

        WHY THE OFFSET IS MEASURED, NOT ASSUMED
        ---------------------------------------
        A stream copy cannot begin mid-GOP, so the file we get back starts at
        the keyframe *preceding* the requested start. That lead-in is unknown
        up front and varies per source. Assuming it is zero shifts every clip
        (and every caption) by up to ~10 seconds.

        So the offset is derived from the file itself: we ask for the range
        with ``padding`` of slack on each side, then probe the real duration.
        Anything longer than the span we asked for is lead-in the keyframe
        added, and:

            clip_start_in_file = lead_in + padding_before

        Both the render cut and the caption rebase use this one number, so the
        video and its captions always move together -- sync does not depend on
        the estimate being exactly right.
        """
        if end <= start:
            logger.error("Invalid section bounds %.2f-%.2f for %s", start, end, video_id)
            return None

        pad = float(config.section_padding if padding is None else padding)
        req_start = max(0.0, float(start) - pad)
        req_end = float(end) + pad
        # How much padding actually survived the clamp at zero. Without this,
        # a clip starting at 3s with 8s padding would think it had 8s of
        # lead-in when only 3s was available -- shifting the cut by 5s.
        pad_before = float(start) - req_start

        existing = None if force_redownload else self._section_path(video_id, req_start, req_end)
        if existing:
            logger.info("Resume: reusing section %s", existing.name)
            return self._describe_section(existing, start, end, req_start, req_end,
                                          pad_before)

        try:
            import yt_dlp
        except ImportError as exc:
            logger.error("yt-dlp is not installed: %s (pip install yt-dlp)", exc)
            return None

        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(
            "Fetching section %.1f-%.1fs (+/-%.0fs padding) of %s",
            start, end, pad, video_id,
        )
        started = time.time()

        try:
            with yt_dlp.YoutubeDL(self._section_opts(video_id, req_start, req_end)) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as exc:
            logger.error("Section download failed for %s [%.1f-%.1f]: %s",
                         video_id, start, end, exc)
            return None

        path = self._section_path(video_id, req_start, req_end)
        if not path:
            logger.error(
                "Section download reported success but produced no usable file "
                "for %s [%.1f-%.1f] in %s", video_id, start, end, self.sections_dir,
            )
            return None

        logger.info(
            "Section ready: %s (%.1f MB in %.1fs)",
            path.name, path.stat().st_size / (1024 * 1024), time.time() - started,
        )
        return self._describe_section(path, start, end, req_start, req_end, pad_before)

    def _describe_section(self, path: Path, start: float, end: float,
                          req_start: float, req_end: float,
                          pad_before: float) -> Dict:
        """Locate the wanted clip inside a downloaded section file."""
        clip_duration = float(end) - float(start)
        file_duration = self._probe_duration(path)
        requested_span = float(req_end) - float(req_start)

        # Whatever the file has beyond the span we requested is the keyframe
        # lead-in that yt-dlp could not avoid.
        lead_in = max(0.0, file_duration - requested_span) if file_duration > 0 else 0.0
        clip_start_in_file = lead_in + pad_before

        # A section can also come back SHORTER than requested (end of video, or
        # the range was clamped). Never point the cut past the end of the file.
        if file_duration > 0:
            clip_start_in_file = min(clip_start_in_file,
                                     max(0.0, file_duration - 0.05))
            clip_duration = min(clip_duration, file_duration - clip_start_in_file)

        if lead_in > 0.05:
            logger.info(
                "  keyframe lead-in measured at %.2fs; clip starts %.2fs into "
                "the section file", lead_in, clip_start_in_file,
            )

        return {
            'path': str(path),
            'video_id': _id_from_filename(path) or '',
            'clip_start_in_file': clip_start_in_file,
            'clip_duration': clip_duration,
            'file_duration': file_duration,
            'requested_start': float(req_start),
            'requested_end': float(req_end),
            'source_start': float(start),
            'source_end': float(end),
            'lead_in': lead_in,
        }

    def download_sections(self, video_id: str,
                          ranges: Sequence[Tuple[float, float]],
                          padding: Optional[float] = None,
                          concurrency: Optional[int] = None,
                          force_redownload: bool = False) -> List[Optional[Dict]]:
        """Fetch several clip ranges, up to ``concurrency`` at a time.

        Section fetches are network-bound and rendering is CPU-bound, so these
        two overlap almost perfectly -- that is where the real wall-clock win
        is, not in running more encodes at once (measured: parallel encodes
        give only 1.02-1.06x on a 2-core box; see BENCHMARKS.md).

        Results are returned in the same order as ``ranges``, with None for any
        range that failed, so callers can keep the index alignment.
        """
        ranges = list(ranges)
        if not ranges:
            return []

        workers = int(concurrency if concurrency is not None
                      else config.download_concurrency)
        workers = max(1, min(workers, len(ranges)))

        def fetch(rng):
            return self.download_section(video_id, rng[0], rng[1], padding=padding,
                                         force_redownload=force_redownload)

        if workers == 1:
            return [fetch(r) for r in ranges]

        logger.info("Fetching %d section(s) with %d parallel download(s)",
                    len(ranges), workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(fetch, ranges))

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
    def _load_dead_channels(self) -> Dict[str, float]:
        """Load the dead-channel cache {channel_key: first_failed_epoch}."""
        try:
            if self.dead_channels_path.exists():
                raw = json.loads(self.dead_channels_path.read_text(encoding='utf-8'))
                if isinstance(raw, dict):
                    return {k: float(v) for k, v in raw.items()}
        except Exception as exc:
            logger.warning("Could not load dead-channel cache %s: %s",
                           self.dead_channels_path, exc)
        return {}

    def _save_dead_channels(self) -> None:
        try:
            self.dead_channels_path.parent.mkdir(parents=True, exist_ok=True)
            self.dead_channels_path.write_text(
                json.dumps(self._dead_channels), encoding='utf-8')
        except Exception as exc:
            logger.warning("Could not save dead-channel cache: %s", exc)

    def _channel_is_dead(self, channel_key: str) -> bool:
        """True if this channel failed listing within the cooldown window.

        A channel past the cooldown is re-probed once; if it fails again it
        gets a fresh timestamp (and one log line), but every mid-cooldown sweep
        stays silent instead of spamming the same ERROR over and over.
        """
        failed_at = self._dead_channels.get(channel_key)
        if not failed_at:
            return False
        age_days = (time.time() - failed_at) / 86400.0
        if age_days >= self.dead_channel_cooldown:
            self._dead_channels.pop(channel_key, None)
            self._save_dead_channels()
            return False
        return True

    def _mark_channel_dead(self, channel_key: str) -> None:
        if channel_key not in self._dead_channels:
            self._dead_channels[channel_key] = time.time()
            self._save_dead_channels()

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

        # Skip a channel that recently failed to list (dead ID / no videos tab
        # / 404 handle). One INFO line per sweep at most, not an ERROR every time.
        if self._channel_is_dead(channel_id):
            logger.info("Channel %s is in the dead-channel cache -- skipping "
                        "listing (auto re-probes after %d days)",
                        channel_id, self.dead_channel_cooldown)
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
            # A channel that can't list is either dead or temporarily broken.
            # Remember it so the next sweep is quiet; it's re-probed after the
            # cooldown and self-heals if the channel comes back.
            self._mark_channel_dead(channel_id)
            logger.warning(
                "Channel listing failed for %s: %s (cached as dead for %d days)",
                url, exc, self.dead_channel_cooldown,
            )
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
                'view_count': entry.get('view_count') or 0,
                'url': entry.get('url') or f"https://www.youtube.com/watch?v={vid}",
                'channel_id': channel_id,
            })
            if len(results) >= max_results:
                break

        # Listing worked -- the channel is alive again. Drop any cached failure
        # so it's not skipped on the next sweep.
        if channel_id in self._dead_channels:
            self._dead_channels.pop(channel_id, None)
            self._save_dead_channels()

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
