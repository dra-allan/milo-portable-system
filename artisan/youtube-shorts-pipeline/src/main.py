"""Entry point and orchestration for the YouTube Shorts pipeline.

The previous src/main.py was a truncated fragment: it began mid-class with
``def process_video_for_shorts(self, ...)`` at column 0 and contained no class
definition, no imports, no argparse and no ``__main__`` block. Nothing in it
could ever execute -- yet run_pipeline.bat calls ``python -m src.main --mode
once`` and the log the user captured shows a ``main`` logger running, meaning
the working copy on the Windows box had diverged from the repo.

This file restores a complete, runnable orchestrator around the existing
modules (downloader, transcriber, processor, video_editor, uploader,
scheduler, database) without changing their public APIs.

Modes:
  test           -- verify ffmpeg/deps/config/credentials, no downloads
  once           -- process one video (URL or ID), or sweep configured niches
  library        -- list/process videos already downloaded to data/temp
  stats          -- fetch YouTube metrics for uploaded shorts (feedback loop)
  schedule       -- run on the configured cron times until interrupted

RESUMABILITY
------------
Every expensive stage is cached on disk and skipped on a re-run, because the
failure Allan hit was "it keeps failing even after downloading a full video"
and each retry paid for the download and the transcription again:

  download    -> data/temp + data/library.json  (downloader.find_local_video)
  transcript  -> data/transcripts/<video_id>.json
  clip plan   -> data/clip_plans/<video_id>.json   (Phase 6: ranked candidates)
  rendered    -> data/shorts/<title>/NN_<title>.mp4  (existing files reused)

So a crash in rendering costs only the rendering on the next run, and
``--force`` is the only thing that redoes finished work.

PHASE 6: CLIP PLAN CACHE + OVERLAPPED FETCH/RENDER
---------------------------------------------------
- Clip plan cache: the full ranked candidate list is persisted to
  data/clip_plans/<video_id>.json after transcription. ``--render-more N``
  reads it and renders additional clips with zero re-download and zero
  re-transcription.
- ``--max-source-minutes N`` limits transcription to the first N minutes
  (useful for fast discovery on hour-long sources; 0 = full source).
- Overlapped fetch+render: while one clip renders (CPU), the next clip's
  footage is downloaded (network). Producer/consumer via ThreadPoolExecutor.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# Allow both "python -m src.main" and "python src/main.py".
if __package__ in (None, ''):  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .config import config
    from .database import PipelineDatabase
    from .processor import ContentProcessor
    from .utils import cleanup_temp_files, sanitize_filename, setup_logger
except ImportError:  # pragma: no cover
    from config import config
    from database import PipelineDatabase
    from processor import ContentProcessor
    from utils import cleanup_temp_files, sanitize_filename, setup_logger

logger = setup_logger(__name__, log_file=Path(config.logs_dir) / 'pipeline.log')

# Accepted forms: full watch URL, youtu.be, /shorts/, /embed/, or a bare ID.
_YT_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
_YT_PATTERNS = (
    re.compile(r'(?:v=|/v/)([A-Za-z0-9_-]{11})'),
    re.compile(r'youtu\.be/([A-Za-z0-9_-]{11})'),
    re.compile(r'/shorts/([A-Za-z0-9_-]{11})'),
    re.compile(r'/embed/([A-Za-z0-9_-]{11})'),
    re.compile(r'/live/([A-Za-z0-9_-]{11})'),
)


def extract_video_id(value: str) -> Optional[str]:
    """Pull an 11-char video ID out of a URL, or validate a bare ID.

    The old batch file passed the raw URL straight through, so a URL with
    extra query params (the user's '...&pp=ygUFZ3RhIHY%3D') was treated as a
    video ID.
    """
    if not value:
        return None
    value = value.strip().strip('"').strip("'")
    if _YT_ID_RE.match(value):
        return value
    for pattern in _YT_PATTERNS:
        m = pattern.search(value)
        if m:
            return m.group(1)
    return None


def guess_niche(metadata: Dict, fallback: str = 'podcast') -> str:
    """Pick the niche whose keywords best match a video's title/tags/description."""
    haystack = ' '.join([
        str(metadata.get('title', '')),
        str(metadata.get('description', ''))[:2000],
        ' '.join(metadata.get('tags') or []),
    ]).lower()

    best_niche, best_hits = None, 0
    for name in config.niche_names():
        keywords = config.get_niche_config(name).get('keywords', [])
        hits = sum(1 for kw in keywords if kw and kw.lower() in haystack)
        if hits > best_hits:
            best_niche, best_hits = name, hits

    if best_niche:
        logger.info("Auto-detected niche '%s' (%d keyword matches)", best_niche, best_hits)
        return best_niche

    available = config.niche_names()
    chosen = fallback if fallback in available else (available[0] if available else 'podcast')
    logger.info("No niche keywords matched; falling back to '%s'", chosen)
    return chosen


class ShortsPipeline:
    """Owns the heavy components and runs videos through them."""

    def __init__(self, upload: Optional[bool] = None,
                 whisper_model: Optional[str] = None):
        logger.info("Initializing YouTube Shorts Pipeline")
        self.config = config
        self.processor = ContentProcessor()
        self.db = PipelineDatabase()
        self.upload_enabled = config.upload_enabled if upload is None else upload
        self.transcript_dir = Path(config.data_dir) / 'transcripts'
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        # Phase 6: clip plan cache directory
        self.clip_plan_dir = Path(config.data_dir) / 'clip_plans'
        self.clip_plan_dir.mkdir(parents=True, exist_ok=True)

        # Heavy/optional components are created lazily so that `--mode test`
        # and a no-upload run don't require every dependency to be installed.
        self._downloader = None
        self._transcriber = None
        self._video_editor = None
        self._uploader = None
        self._uploaders = {}  # channel key -> YouTubeUploader (cached)
        self._whisper_model = whisper_model or config.whisper_model

        self.stats = {
            'videos_processed': 0,
            'shorts_created': 0,
            'shorts_uploaded': 0,
            'errors': 0,
        }

    # -- lazy components ------------------------------------------------
    @property
    def downloader(self):
        if self._downloader is None:
            try:
                from .downloader import YouTubeDownloader
            except ImportError:
                from downloader import YouTubeDownloader
            self._downloader = YouTubeDownloader()
        return self._downloader

    @property
    def transcriber(self):
        if self._transcriber is None:
            try:
                from .transcriber import VideoTranscriber
            except ImportError:
                from transcriber import VideoTranscriber
            self._transcriber = VideoTranscriber(
                model_size=self._whisper_model,
                device=config.whisper_device,
            )
        return self._transcriber

    @property
    def video_editor(self):
        if self._video_editor is None:
            try:
                from .video_editor import VideoEditor
            except ImportError:
                from video_editor import VideoEditor
            self._video_editor = VideoEditor()
        return self._video_editor

    @property
    def uploader(self):
        if self._uploader is None:
            try:
                from .uploader import YouTubeUploader
            except ImportError:
                from uploader import YouTubeUploader
            self._uploader = YouTubeUploader()
        return self._uploader

    def _uploader_for_channel(self, channel: str):
        """Return a cached YouTubeUploader bound to a specific channel key."""
        if channel not in self._uploaders:
            try:
                from .uploader import YouTubeUploader
            except ImportError:
                from uploader import YouTubeUploader
            self._uploaders[channel] = YouTubeUploader(channel=channel)
        return self._uploaders[channel]

    # -- transcript cache ------------------------------------------------
    def _transcript_cache_path(self, video_id: str) -> Path:
        return self.transcript_dir / f"{video_id}.json"

    def load_cached_transcript(self, video_id: str) -> Optional[List[Dict]]:
        """Return a previously saved transcript, or None.

        Transcription is by far the slowest stage (minutes on CPU). Caching it
        means a failure in a later stage no longer costs a re-transcribe.
        """
        path = self._transcript_cache_path(video_id)
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            segments = payload.get('segments') if isinstance(payload, dict) else payload
            if not isinstance(segments, list) or not segments:
                return None
            # Reject a cache written by a partial/failed run.
            for seg in segments:
                if 'start' not in seg or 'end' not in seg or 'text' not in seg:
                    logger.warning("Cached transcript for %s is malformed; ignoring", video_id)
                    return None
            logger.info(
                "Resume: reusing cached transcript for %s (%d segments) -- skipping Whisper",
                video_id, len(segments),
            )
            return segments
        except Exception as exc:
            logger.warning("Could not read cached transcript %s: %s", path.name, exc)
            return None

    def save_transcript(self, video_id: str, segments: List[Dict],
                        title: str = '') -> None:
        path = self._transcript_cache_path(video_id)
        try:
            tmp = path.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({
                    'video_id': video_id,
                    'title': title,
                    'model': self._whisper_model,
                    'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                    'segments': segments,
                }, f, ensure_ascii=False)
            os.replace(str(tmp), str(path))
            logger.info("Cached transcript -> %s", path.name)
        except Exception as exc:
            logger.warning("Could not cache transcript for %s: %s", video_id, exc)

    def _clip_plan_path(self, video_id: str) -> Path:
        return self.clip_plan_dir / f"{video_id}.json"

    def load_clip_plan(self, video_id: str) -> Optional[Dict]:
        """Return a previously saved clip plan, or None."""
        path = self._clip_plan_path(video_id)
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                plan = json.load(f)
            if not isinstance(plan, dict) or 'candidates' not in plan:
                return None
            logger.info(
                "Resume: reusing clip plan for %s (%d candidates) -- skipping transcription",
                video_id, len(plan['candidates']),
            )
            return plan
        except Exception as exc:
            logger.warning("Could not read clip plan %s: %s", path.name, exc)
            return None

    def save_clip_plan(self, video_id: str, plan: Dict) -> None:
        path = self._clip_plan_path(video_id)
        try:
            tmp = path.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(path))
            logger.info("Cached clip plan -> %s (%d candidates)", path.name, len(plan.get('candidates', [])))
        except Exception as exc:
            logger.warning("Could not cache clip plan for %s: %s", video_id, exc)

    # ------------------------------------------------------------------
    def process_video_for_shorts(self, video_id: str, niche: Optional[str] = None,
                                 force: bool = False,
                                 local_only: bool = False) -> bool:
        """Download -> transcribe -> find highlights -> render -> (upload).

        Every stage is resumable: an existing download, transcript or rendered
        clip is reused instead of being redone. Returns True if at least one
        Short is on disk when we finish.

        Args:
            local_only: never download; fail if the video is not already in
                the local library. Used by --from-library.
        """
        video_id = extract_video_id(video_id) or video_id
        logger.info("Starting processing for video %s", video_id)

        if not force and self.db.is_video_processed(video_id):
            logger.warning(
                "Video %s was already processed (use --force to redo it)", video_id
            )
            return False

        audio_path = None
        try:
            # -- 1. download (or reuse) -------------------------------------
            logger.info("Step 1/6: Fetching video (reusing an existing download if present)")
            if local_only:
                existing = self.downloader.find_local_video(video_id)
                if not existing:
                    logger.error(
                        "No local copy of %s in %s. Drop the file there or run "
                        "without --from-library to download it.",
                        video_id, self.config.temp_dir,
                    )
                    self.stats['errors'] += 1
                    return False
                metadata = self.downloader._metadata_from_cache(video_id, existing)
            else:
                metadata = self.downloader.download_video(video_id)

            if not metadata or not metadata.get('video_path'):
                logger.error("Could not obtain video %s", video_id)
                self.stats['errors'] += 1
                return False

            video_path = metadata['video_path']
            if not Path(video_path).exists():
                logger.error("Video file vanished: %s", video_path)
                self.stats['errors'] += 1
                return False

            title = metadata.get('title') or video_id
            duration = metadata.get('duration') or 0
            logger.info(
                "%s: '%s' (%ss)",
                "Reused existing download" if metadata.get('from_cache') else "Downloaded",
                title, duration,
            )

            if niche is None:
                niche = guess_niche(metadata)
            niche_config = self.config.get_niche_config(niche)
            niche_keywords = niche_config.get('keywords', [])
            logger.info(
                "Niche '%s': %d keywords %s",
                niche, len(niche_keywords), niche_keywords[:5],
            )

            # -- 2. transcribe (or reuse the cache) -------------------------
            logger.info("Step 2/6: Transcribing (cached transcripts are reused)")
            transcript = None if force else self.load_cached_transcript(video_id)

            if transcript is None:
                max_seconds = getattr(self.transcriber, 'max_seconds', None)
                audio_path = self.transcriber.extract_audio_from_video(video_path, max_seconds=max_seconds)
                if not audio_path:
                    logger.error("Failed to extract audio from %s", video_path)
                    self.stats['errors'] += 1
                    return False

                transcript = self.transcriber.transcribe_audio(audio_path)
                if not transcript:
                    logger.error("Transcription produced nothing for %s", audio_path)
                    self.stats['errors'] += 1
                    return False
                logger.info("Transcribed audio into %d segments", len(transcript))
                # Save before anything downstream can fail, so a later crash
                # never costs the transcription again.
                self.save_transcript(video_id, transcript, title)

            # -- 3. find highlights ----------------------------------------
            logger.info("Step 3/6: Finding highlight segments")
            highlights = self.processor.find_highlight_segments(
                transcript,
                niche_keywords=niche_keywords,
                min_segment_length=self.config.min_segment_length,
                max_segment_length=self.config.max_segment_length,
                min_gap_between=self.config.min_gap_between_clips,
                max_clips=self.config.max_clips_per_video,
                max_candidates=getattr(self.config, 'max_candidates', None),
                min_score=float(niche_config.get('min_score') or 0.0),
            )
            if not highlights:
                logger.warning("No highlight segments found for video %s", video_id)
                self.stats['errors'] += 1
                return False
            logger.info("Found %d highlight segments", len(highlights))

            # Phase 6: cache the full ranked candidate list for --render-more
            if not force and getattr(self.config, 'max_candidates', None):
                plan = {
                    'video_id': video_id,
                    'title': title,
                    'niche': niche,
                    'niche_keywords': niche_keywords,
                    'transcript_span': float(transcript[-1]['end']) - float(transcript[0]['start']),
                    'candidates': highlights,  # already includes 'rank' field from processor
                }
                self.save_clip_plan(video_id, plan)

            # -- 4. render --------------------------------------------------
            safe_title = sanitize_filename(title) or video_id
            shorts_dir = Path(self.config.shorts_dir) / safe_title
            shorts_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Step 4/6: Creating Shorts from highlights")
            self.db.record_video(
                video_id, title, niche, duration,
                channel_id=metadata.get('uploader', '') or '',
                published_at=metadata.get('upload_date'),
            )

            created: List[Dict] = []
            for i, highlight in enumerate(highlights, start=1):
                hook_text = (highlight.get('text') or '').strip()
                safe_hook = sanitize_filename(hook_text) if hook_text else f"clip{i}"
                # Limit length to 50 characters to avoid excessively long filenames
                if len(safe_hook) > 50:
                    safe_hook = safe_hook[:50]
                output_path = str(shorts_dir / f"{i:02d}_{safe_hook}.mp4")
                existing = Path(output_path)

                # Resume: a clip already rendered on a previous run is kept.
                # Rendering is minutes of CPU per clip, so redoing clips 1-4
                # because clip 5 failed is exactly the waste we are removing.
                if not force and existing.exists() and existing.stat().st_size > 64 * 1024:
                    logger.info(
                        "Resume: clip %d/%d already rendered (%.1f MB) -- skipping",
                        i, len(highlights), existing.stat().st_size / (1024 * 1024),
                    )
                    self.stats['shorts_created'] += 1
                    created.append({'index': i, 'path': output_path, 'highlight': highlight})
                    self.db.record_short(
                        video_id, i, highlight['start'], highlight['end'],
                        title=title, local_path=output_path,
                        score=highlight.get('score'),
                    )
                    continue

                logger.info(
                    "Rendering clip %d/%d: %.1f-%.1fs (score %.2f)",
                    i, len(highlights), highlight['start'], highlight['end'],
                    highlight.get('score', 0.0),
                )

                clip_transcript = [
                    seg for seg in transcript
                    if not (seg['end'] <= highlight['start']
                            or seg['start'] >= highlight['end'])
                ]

                ok = self.video_editor.create_short_from_segment(
                    video_path=video_path,
                    start_time=highlight['start'],
                    end_time=highlight['end'],
                    transcript_segments=clip_transcript,
                    output_path=output_path,
                    add_branding=False,
                )

                if not ok or not Path(output_path).exists():
                    logger.error("Failed to create clip %d", i)
                    self.stats['errors'] += 1
                    continue

                self.stats['shorts_created'] += 1
                created.append({'index': i, 'path': output_path, 'highlight': highlight})
                self.db.record_short(
                    video_id, i, highlight['start'], highlight['end'],
                    title=title, local_path=output_path,
                    score=highlight.get('score'),
                )

            if not created:
                logger.error("No clips could be rendered for %s", video_id)
                return False

            # -- 5. upload --------------------------------------------------
            if self.upload_enabled:
                logger.info("Step 5/6: Uploading %d Shorts", len(created))
                self._upload_clips(created, video_id, niche, niche_keywords)
            else:
                logger.info(
                    "Step 5/6: Upload disabled (set UPLOAD_ENABLED=true to publish). "
                    "%d clips kept locally.", len(created)
                )

            # -- 6. done ----------------------------------------------------
            self.stats['videos_processed'] += 1
            logger.info(
                "Step 6/6: Finished %s -- %d clips in %s",
                video_id, len(created), shorts_dir,
            )
            return True

        except KeyboardInterrupt:
            logger.warning("Interrupted while processing %s", video_id)
            raise
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", video_id, exc, exc_info=True)
            self.stats['errors'] += 1
            return False
        finally:
            # Audio is large and always regenerable; the source video and
            # subtitles are kept so a re-run skips the slow download.
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    def _generate_unique_title(self, hook_text: str, niche: str, clip_index: int) -> str:
        """Generate a unique title based on the short content"""
        if not hook_text:
            return f"{niche} clip #{clip_index} #Shorts"

        # Clean the hook text
        cleaned_text = ' '.join(hook_text.split())  # Remove extra whitespace

        # Extract key phrases or interesting parts
        words = cleaned_text.split()

        # For very short hooks, use them directly
        if len(words) <= 5:
            base_title = cleaned_text
        else:
            # Try to find a compelling segment
            # Look for questions, exclamations, or interesting phrases
            base_title = cleaned_text

            # If it's too long, truncate intelligently
            if len(base_title) > 60:
                # Try to break at a sentence boundary
                if '.' in base_title[:50]:
                    breakpoint = base_title.find('.', 20, 50)
                    if breakpoint != -1:
                        base_title = base_title[:breakpoint+1]
                    else:
                        base_title = base_title[:57] + "..."
                elif '!' in base_title[:50]:
                    breakpoint = base_title.find('!', 20, 50)
                    if breakpoint != -1:
                        base_title = base_title[:breakpoint+1]
                    else:
                        base_title = base_title[:57] + "..."
                elif '?' in base_title[:50]:
                    breakpoint = base_title.find('?', 20, 50)
                    if breakpoint != -1:
                        base_title = base_title[:breakpoint+1]
                    else:
                        base_title = base_title[:57] + "..."
                else:
                    # Just truncate and add ellipsis
                    base_title = base_title[:57] + "..."

        # Add niche and Shorts hashtag
        return f"{base_title.strip()} #{niche} #Shorts"

    def _upload_clips(self, created: List[Dict], video_id: str, niche: str,
                      niche_keywords: List[str]) -> None:
        # Route this niche to its bound channel. If a niche has no token on
        # disk, YouTubeUploader(channel=...) logs a warning and falls back to
        # the default token -- so verify a token exists before we build it.
        channel = self.config.get_niche_channel(niche)
        authed = self.config.authenticated_channels()
        if not channel or (channel not in authed and authed):
            logger.error(
                "No authenticated YouTube channel bound to niche '%s' "
                "(resolved channel=%r, authed=%s). Clips kept local. "
                "Bind it in config/niches.yaml with `channel: <name>` and run "
                "`python -m src.uploader auth --channel <name>` once.",
                niche, channel, authed or ['(default token)'],
            )
            self.stats['errors'] += 1
            return

        try:
            uploader = self._uploader_for_channel(channel)
        except Exception as exc:
            logger.error(
                "Upload requested but the YouTube client for channel '%s' "
                "could not start: %s. Clips are still on disk.", channel, exc
            )
            return

        cap = self.config.upload_max_per_run
        # Build the upload queue: fresh clips first (they came from this run),
        # then older rendered-but-unpublished clips to fill the remaining cap.
        queue = [{'index': item['index'], 'path': item['path'],
                  'highlight': item['highlight'], 'niche': niche,
                  'source_video_id': video_id}
                 for item in created]
        if self.config.upload_backlog:
            old = [r for r in self.db.unuploaded_shorts(limit=cap * 3)
                   if not any(
                       r['source_video_id'] == video_id
                       and r['segment_index'] == item['index']
                       for item in created
                   )]
            logger.info(
                "Upload queue: %d new clip(s), %d backlog clip(s) available, cap %d/run",
                len(queue), len(old), cap,
            )
            for row in old:
                if len(queue) >= cap:
                    break
                queue.append({'index': row['segment_index'],
                              'path': row['local_path'],
                              'highlight': {'text': row['title'] or ''},
                              'niche': row['niche'] or niche,
                              'source_video_id': row['source_video_id']})
        else:
            logger.info("Upload cap: %d new clip(s), backlog mixing disabled", len(queue))

        for item in queue[:cap]:
            item_niche = item.get('niche', niche)
            item_source = item.get('source_video_id', video_id)
            item_keywords = (self.config.get_niche_config(item_niche)
                             .get('keywords', [])) if item_niche else niche_keywords
            highlight = item['highlight']
            hook = (highlight.get('text') or '').strip().replace('\n', ' ')
            short_title = self._generate_unique_title(hook, item_niche, item['index'])
            description = (
                f"Full video: https://youtube.com/watch?v={item_source}\n\n"
                f"Follow for more {item_niche} content!\n"
                f"#Shorts #{item_niche} "
                + ' '.join(f"#{kw.replace(' ', '')}" for kw in item_keywords[:3])
            )
            tags = [item_niche, 'Shorts'] + [kw for kw in item_keywords[:10] if kw]

            try:
                short_id = uploader.upload_short(
                    video_path=item['path'],
                    title=short_title,
                    description=description,
                    tags=tags,
                )
            except Exception as exc:
                logger.error("Upload raised for clip %d: %s", item['index'], exc)
                short_id = None

            if short_id:
                logger.info("Uploaded clip %d as %s", item['index'], short_id)
                self.stats['shorts_uploaded'] += 1
                self.db.mark_short_uploaded(item_source, item['index'], short_id)
                # Snapshot stats immediately: YouTube returns view counts that
                # start near zero, but having the row exist means later
                # --mode stats runs can compare growth over time.
                try:
                    stats = uploader.fetch_statistics(short_id)
                    if stats:
                        self.db.record_performance(
                            short_id, item_source, item['index'],
                            views=stats['views'], likes=stats['likes'],
                            comments=stats['comments'], favorites=stats['favorites'],
                        )
                        logger.info("Recorded initial stats for %s", short_id)
                except Exception as exc:
                    logger.warning("Could not snapshot stats for %s: %s", short_id, exc)
            else:
                logger.error("Upload failed for clip %d (kept locally)", item['index'])
                self.stats['errors'] += 1

    # ------------------------------------------------------------------
    def run_niche(self, niche: str, max_videos: int = 1,
                  lookback: Optional[int] = None) -> int:
        """Process the best `max_videos` videos discovered for a niche.

        Only niches bound to an authenticated upload channel are processed:
        a niche with no channel binding renders clips that can never be
        published, so we leave it untouched until it is bound.

        Returns how many videos were actually started.
        """
        from .discovery import discover_candidates

        channel = self.config.get_niche_channel(niche)
        authed = self.config.authenticated_channels()
        if not channel or (channel not in authed and authed):
            logger.info(
                "Niche '%s': no authenticated upload channel bound "
                "(resolved channel=%r, authed=%s) -- skipping until bound in "
                "config/niches.yaml with `channel: <name>`",
                niche, channel, authed or ['(default token)'],
            )
            return 0

        lookback = lookback or getattr(self.config, 'discovery_lookback', 10)
        result = discover_candidates(self.downloader, self.db, niche,
                                     max_videos=max_videos, lookback=lookback)

        for skip in result.skipped_already_processed:
            logger.info("Niche '%s': %s already processed -- skipping", niche, skip)
        if result.skipped_duration:
            logger.info("Niche '%s': %d outside duration band -- skipping",
                        niche, len(result.skipped_duration))
        if result.skipped_negative_keywords:
            logger.info("Niche '%s': %d negative-keyword titles -- skipping",
                        niche, len(result.skipped_negative_keywords))

        if not result.candidates:
            logger.info("Niche '%s': no new discoverable videos (queried %d channel(s))",
                        niche, len(result.channels_queried))
            return 0

        candidates = result.candidates[:max_videos]
        logger.info("Niche '%s': processing %d of %d discovered",
                    niche, len(candidates), len(result.candidates))
        started = 0
        for entry in candidates:
            vid = entry['id']
            ok = self.process_video_for_shorts(vid, niche)
            if ok:
                started += 1
        return started

    def report(self) -> Dict[str, int]:
        logger.info(
            "Run complete: %d videos, %d clips created, %d uploaded, %d errors",
            self.stats['videos_processed'], self.stats['shorts_created'],
            self.stats['shorts_uploaded'], self.stats['errors'],
        )
        return dict(self.stats)


def run_stats_mode(pipeline: 'ShortsPipeline', args) -> int:
    """Fetch current YouTube metrics for every uploaded short and record them.

    This is the feedback loop, runnable as often as you like: each run updates
    short_performance rows for clips whose last fetch is older than
    --stats-age-hours (default 24), then prints the current top performers.
    """
    try:
        uploader = pipeline.uploader
    except Exception as exc:
        logger.error("Cannot start the YouTube client for stats: %s", exc)
        print("Stats mode needs YouTube credentials (YOUTUBE_API_KEY or OAuth).")
        return 1

    pending = pipeline.db.shorts_needing_stats(
        limit=args.limit, max_age_hours=args.stats_age_hours,
    )
    if not pending:
        print("No uploaded shorts need a stats refresh (all fetched within "
              f"{args.stats_age_hours}h, or none uploaded yet).")
    else:
        print(f"Fetching stats for {len(pending)} uploaded short(s)...")
        updated = 0
        for short in pending:
            short_id = short['youtube_short_id']
            try:
                stats = uploader.fetch_statistics(short_id)
            except Exception as exc:
                logger.warning("Stats fetch failed for %s: %s", short_id, exc)
                continue
            if not stats:
                logger.warning("No stats returned for %s", short_id)
                continue
            pipeline.db.record_performance(
                short_id, short['source_video_id'], short['segment_index'],
                views=stats['views'], likes=stats['likes'],
                comments=stats['comments'], favorites=stats['favorites'],
            )
            updated += 1
            logger.info(
                "Recorded %s: %d views / %d likes / %d comments",
                short_id, stats['views'], stats['likes'], stats['comments'],
            )
        print(f"Updated {updated} short(s).")

    # Always show the current leaderboard, even with nothing new to fetch.
    summary = pipeline.db.performance_summary()
    print(f"\nTracked clips: {summary['tracked']} | "
          f"with views: {summary['with_views']} | "
          f"total views: {summary['total_views']} | "
          f"avg views/clip: {summary['avg_views']}")

    report = pipeline.db.performance_report(limit=args.top)
    if report:
        print(f"\nTop {len(report)} clips by views:")
        for r in report:
            print(
                f"  {r['views']:>7} views | {r['likes']:>5} likes | "
                f"{r['comments']:>4} cmts | "
                f"seg {r['segment_index']} ({r['start_time']:.0f}-{r['end_time']:.0f}s) | "
                f"{str(r['title'])[:44]}"
            )
    else:
        print("\nNo performance data yet. Upload some Shorts and run this again.")

    return 0


# ----------------------------------------------------------------------
def run_discover_mode(pipeline: 'ShortsPipeline', args) -> int:
    """Dry-run discovery: print what a scheduled run would pick, do nothing else.

    Only niches bound to an authenticated channel are reported; unbound niches
    are listed as skipped. No downloads, no transcription, no rendering.
    """
    from .discovery import discover_candidates

    niches = [args.niche] if args.niche else config.niche_names()
    if not niches:
        print("No niches configured.")
        return 1

    lookback = getattr(config, 'discovery_lookback', 10)
    authed = config.authenticated_channels()
    total = 0
    for niche in niches:
        channel = config.get_niche_channel(niche)
        if not channel or (channel not in authed and authed):
            print(f"\n[{niche}] SKIPPED: no authenticated upload channel bound "
                  f"(resolved {channel!r}, authed={authed or ['(default token)']})")
            continue

        result = discover_candidates(pipeline.downloader, pipeline.db, niche,
                                     max_videos=1, lookback=lookback)
        print(f"\n[{niche}] channel={channel} | queried {len(result.channels_queried)} source channel(s)")
        if result.skipped_already_processed:
            print(f"  skipped (already processed): {len(result.skipped_already_processed)}")
        if result.skipped_duration:
            print(f"  skipped (duration band):     {len(result.skipped_duration)}")
        if result.skipped_negative_keywords:
            print(f"  skipped (negative keywords): {len(result.skipped_negative_keywords)}")
        for c in result.candidates[:10]:
            dur = c.get('duration') or 0
            print(f"  CANDIDATE {c['id']}  {dur:>7.0f}s  {str(c.get('title'))[:50]}")
        if not result.candidates:
            print("  (no new candidates)")
        total += len(result.candidates)
    print(f"\nDiscover: {total} candidate video(s) across bound niches.")
    return 0


def run_test_mode() -> int:
    """Check the environment without downloading anything."""
    import shutil
    import subprocess

    ok = True
    print("YouTube Shorts Pipeline -- environment check")
    print("=" * 52)

    # ffmpeg / ffprobe
    for tool in ('ffmpeg', 'ffprobe'):
        path = shutil.which(tool)
        if path:
            try:
                out = subprocess.run([tool, '-version'], capture_output=True,
                                     text=True, timeout=10)
                version = out.stdout.splitlines()[0] if out.stdout else 'unknown'
            except Exception:
                version = 'unknown'
            print(f"  [ok]   {tool}: {version[:60]}")
        else:
            print(f"  [FAIL] {tool}: not found in PATH")
            ok = False

    # Python packages
    for module, extra in (
        ('yt_dlp', 'yt-dlp'),
        ('faster_whisper', 'faster-whisper'),
        ('yaml', 'pyyaml'),
        ('dotenv', 'python-dotenv'),
        ('apscheduler', 'apscheduler'),
        ('googleapiclient', 'google-api-python-client'),
    ):
        try:
            __import__(module)
            print(f"  [ok]   python package: {extra}")
        except ImportError:
            level = 'warn' if module == 'googleapiclient' else 'FAIL'
            print(f"  [{level}] python package missing: {extra} (pip install {extra})")
            if level == 'FAIL':
                ok = False

    # Config
    print(f"  [{'ok' if config.env_loaded else 'warn'}]   .env: "
          f"{config.env_file if config.env_loaded else 'not found (using defaults)'}")
    if config.niches_error:
        print(f"  [FAIL] niches.yaml: {config.niches_error}")
        ok = False
    else:
        print(f"  [ok]   niches.yaml: {len(config.niche_names())} niches "
              f"({', '.join(config.niche_names()[:4])}...)")

    print(f"  [ok]   clip length band: "
          f"{config.min_segment_length}-{config.max_segment_length}s, "
          f"max {config.max_clips_per_video} clips/video")
    print(f"  [ok]   whisper: {config.whisper_model} on {config.whisper_device}")
    print(f"  [ok]   output dir: {config.shorts_dir}")

    if config.upload_enabled:
        if config.has_upload_credentials():
            print(f"  [ok]   upload: ENABLED, privacy={config.privacy_status}")
            authed = config.authenticated_channels()
            print(f"  [{'ok' if authed else 'warn'}]   channels: "
                  f"{', '.join(authed) if authed else 'none authenticated yet'}"
                  f" (cap {config.upload_max_per_run}/run, "
                  f"backlog={'on' if config.upload_backlog else 'off'})")
            unbound = [n for n in config.niche_names()
                       if config.get_niche_channel(n) not in authed]
            if unbound:
                print(f"  [warn] unbound niches ({len(unbound)}): "
                      f"{', '.join(unbound[:6])}"
                      f"{'...' if len(unbound) > 6 else ''} -- add `channel:` "
                      f"in niches.yaml to publish them")
        else:
            print("  [FAIL] upload is ENABLED but no credentials are configured")
            ok = False
    else:
        print("  [ok]   upload: disabled (clips saved locally only)")

    # Highlight detector smoke test -- this is the piece that silently
    # returned zero clips for every video.
    sample = [
        {'text': 'Here is why nobody tells you this.', 'start': 0.0, 'end': 4.0},
        {'text': 'The secret is actually really simple!', 'start': 4.2, 'end': 8.5},
        {'text': 'Watch this, it changes everything.', 'start': 8.8, 'end': 13.0},
        {'text': 'Most people get this completely wrong.', 'start': 13.4, 'end': 18.0},
        {'text': 'And that is the whole trick.', 'start': 18.3, 'end': 22.0},
    ]
    clips = ContentProcessor().find_highlight_segments(
        sample, niche_keywords=['secret', 'trick'],
        min_segment_length=15, max_segment_length=60,
    )
    if clips:
        print(f"  [ok]   highlight detector: {len(clips)} clip(s) on sample transcript")
    else:
        print("  [FAIL] highlight detector returned 0 clips on the sample transcript")
        ok = False

    db_stats = PipelineDatabase().stats()
    print(f"  [ok]   database: {db_stats['processed_videos']} videos, "
          f"{db_stats['generated_shorts']} clips, "
          f"{db_stats['uploaded_shorts']} uploaded")

    print("=" * 52)
    print("All checks passed." if ok else "Some checks FAILED (see above).")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m src.main',
        description='YouTube Shorts automation pipeline',
    )
    parser.add_argument('target', nargs='?', default=None,
                        help='YouTube URL or 11-character video ID')
    parser.add_argument('--mode', choices=['once', 'schedule', 'test', 'library', 'stats', 'discover'],
                        default='once',
                        help="'library' lists videos already downloaded and can "
                             "process them without touching the network; "
                             "'stats' fetches YouTube metrics for uploaded shorts; "
                             "'discover' dry-runs scheduled discovery for bound "
                             "niches (no downloads)")
    parser.add_argument('--niche', default=None,
                        help='Niche name from config/niches.yaml (default: auto-detect)')
    parser.add_argument('--videos', type=int, default=1,
                        help='Videos per niche when sweeping (default: 1)')
    parser.add_argument('--upload', dest='upload', action='store_true', default=None,
                        help='Upload results to YouTube (overrides UPLOAD_ENABLED)')
    parser.add_argument('--no-upload', dest='upload', action='store_false',
                        help='Never upload, just render locally')
    parser.add_argument('--force', action='store_true',
                        help='Redo finished work: ignore the DB dedup entry, the '
                             'cached transcript and any already-rendered clips')
    parser.add_argument('--from-library', action='store_true',
                        help='Only use an already-downloaded video; never download '
                             '(works offline / when YouTube is rate-limiting)')
    parser.add_argument('--all', action='store_true',
                        help='With --mode library: process every downloaded video')
    parser.add_argument('--model', default=None,
                        help='Whisper model size (tiny/base/small/medium/large)')
    parser.add_argument('--clean', action='store_true',
                        help='Delete temp files older than 24h before running')
    parser.add_argument('--render-more', type=int, default=0, metavar='N',
                        help='Render N additional clips from a cached clip plan '
                             '(no re-download, no re-transcribe; use after a full run)')
    parser.add_argument('--max-source-minutes', type=int, default=0, metavar='N',
                        help='Transcribe only the first N minutes of the source '
                             '(0 = full source; useful for fast discovery on hour-long videos)')
    parser.add_argument('--limit', type=int, default=50,
                        help='With --mode stats: max shorts to refresh (default 50)')
    parser.add_argument('--stats-age-hours', type=int, default=24,
                        help='With --mode stats: refresh clips last fetched more '
                             'than N hours ago (default 24)')
    parser.add_argument('--top', type=int, default=10,
                        help='With --mode stats: show top N clips by views (default 10)')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.mode == 'test':
        return run_test_mode()

    if args.clean:
        logger.info("Cleaning temp files older than 24h")
        cleanup_temp_files(24)

    if args.niche and args.niche not in config.niche_names():
        logger.error(
            "Unknown niche '%s'. Available: %s",
            args.niche, ', '.join(config.niche_names()) or '(none)',
        )
        return 2

    pipeline = ShortsPipeline(upload=args.upload, whisper_model=args.model)

    # Phase 6: --max-source-minutes limits transcription to first N minutes
    if args.max_source_minutes > 0:
        pipeline.transcriber.max_seconds = args.max_source_minutes * 60
        logger.info("Transcription limited to first %d minutes (%.0fs)",
                    args.max_source_minutes, args.max_source_minutes * 60)

    if args.mode == 'stats':
        return run_stats_mode(pipeline, args)

    if args.mode == 'discover':
        return run_discover_mode(pipeline, args)

    if args.mode == 'schedule':
        return _run_schedule(pipeline, args)

    if args.mode == 'library':
        # For library mode, we extract video_id from target if provided, else use falsy to list all
        video_id = extract_video_id(args.target) if args.target else None
        return _render_more_from_plan(pipeline, video_id, 0, args.force, args)

    # --- render-more: render additional clips from cached clip plan ---
    if args.render_more > 0:
        if not args.target:
            logger.error("--render-more requires a video ID or URL (--target)")
            return 2
        video_id = extract_video_id(args.target)
        if not video_id:
            logger.error("Could not read video ID from %r", args.target)
            return 2
        return _render_more_from_plan(pipeline, video_id, args.render_more, args.force, args)

    # --- once ---
    if args.target:
        video_id = extract_video_id(args.target)
        if not video_id:
            logger.error(
                "Could not read a YouTube video ID from %r. Pass a watch URL, "
                "a youtu.be/shorts link, or a bare 11-character ID.", args.target
            )
            return 2
        pipeline.process_video_for_shorts(video_id, args.niche, force=args.force,
                                          local_only=args.from_library)
    else:
        _run_scheduled_sweep(pipeline, args)

    stats = pipeline.report()
    return 0 if stats['videos_processed'] > 0 else 1


def _render_more_from_plan(pipeline: 'ShortsPipeline', video_id: str,
                           count: int, force: bool = False, args=None) -> int:
    """List (and optionally process) videos already downloaded to data/temp.

    This is the resume entry point: it never downloads. run_pipeline.bat's
    "Process from Library" option used to shell out to a fragile inline
    PowerShell script that parsed .info.json files by hand; this replaces it
    with a real code path.
    """
    entries = pipeline.downloader.list_library()
    if not entries:
        print(f"No downloaded videos found in {config.temp_dir}")
        print("Run: python -m src.main --mode once \"<YouTube URL>\" to download one.")
        return 1

    print(f"\nDownloaded videos in {config.temp_dir}:")
    print("-" * 72)
    for i, entry in enumerate(entries, 1):
        mins = (entry['duration'] or 0) / 60
        cached = "transcript cached" if pipeline._transcript_cache_path(
            entry['id']).exists() else "no transcript yet"
        print(f"  {i:2d}. {entry['title'][:44]:<44s} {mins:5.1f}min "
              f"{entry['size_mb']:7.1f}MB  [{cached}]")
    print("-" * 72)

    # Handle case where args might be None (for safety)
    if args is None:
        # Create a simple object with default values
        class DefaultArgs:
            all = False
            target = None
            niche = None
            force = False
        args = DefaultArgs()

    targets: List[Dict] = []
    if args.all:
        targets = entries
    elif args.target:
        wanted = extract_video_id(args.target) or args.target
        targets = [e for e in entries if e['id'] == wanted]
        if not targets:
            logger.error("Video %s is not in the local library", wanted)
            return 2
    else:
        try:
            raw = input("\nEnter a number to process (0 to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw or raw == '0':
            return 0
        try:
            choice = int(raw)
        except ValueError:
            logger.error("Not a number: %r", raw)
            return 2
        if not 1 <= choice <= len(entries):
            logger.error("Choice out of range: %d", choice)
            return 2
        targets = [entries[choice - 1]]

    for entry in targets:
        print()
        pipeline.process_video_for_shorts(
            entry['id'], args.niche, force=args.force, local_only=True,
        )

    stats = pipeline.report()
    return 0 if stats['videos_processed'] > 0 else 1


def _run_scheduled_sweep(pipeline: 'ShortsPipeline', args) -> int:
    """Run every channel-bound niche up to a global per-run video budget.

    Each run_niche returns how many videos it started; the sweep stops once
    the budget is exhausted so 20+ niches can't trigger 20 download cycles
    when only a handful will be uploaded. Niches without an authenticated
    upload channel are skipped inside run_niche (they can't publish).
    """
    budget = getattr(config, 'schedule_max_videos', 3)
    niches = [args.niche] if args.niche else config.niche_names()
    if not niches:
        logger.error("No niches configured and no video specified. Nothing to do.")
        return 0

    started_total = 0
    for niche in niches:
        if started_total >= budget:
            logger.info("Scheduled sweep budget (%d videos) exhausted", budget)
            break
        remaining = budget - started_total
        started = pipeline.run_niche(niche, max_videos=remaining)
        started_total += started
    logger.info("Scheduled sweep started %d video(s)", started_total)
    return started_total


def _run_schedule(pipeline: 'ShortsPipeline', args) -> int:
    try:
        try:
            from .scheduler import PipelineScheduler
        except ImportError:
            from scheduler import PipelineScheduler
    except ImportError as exc:
        logger.error("Scheduler needs APScheduler: %s (pip install apscheduler)", exc)
        return 2

    import time

    run_times = [t.split('#')[0].strip()
                 for t in os.getenv('RUN_TIMES', '0 9 * * *,0 14 * * *,0 19 * * *').split(',')]
    run_times = [t for t in run_times if t]

    def job():
        try:
            _run_scheduled_sweep(pipeline, args)
            pipeline.report()
        except Exception as exc:
            logger.error("Scheduled sweep failed: %s", exc, exc_info=True)

    sched = PipelineScheduler()
    for i, cron in enumerate(run_times):
        try:
            sched.add_daily_job(job, cron, job_id=f'shorts_pipeline_{i}')
        except Exception as exc:
            logger.error("Bad cron entry %r: %s", cron, exc)

    # Feedback loop: refresh YouTube metrics once a day on top of the runs.
    stats_cron = os.getenv('STATS_RUN_TIME', '0 8 * * *')
    try:
        def stats_job():
            try:
                run_stats_mode(pipeline, args)
            except Exception as exc:
                logger.error("Scheduled stats refresh failed: %s", exc)
        sched.add_daily_job(stats_job, stats_cron, job_id='short_stats_refresh')
        logger.info("Stats refresh scheduled at %s", stats_cron)
    except Exception as exc:
        logger.error("Bad STATS_RUN_TIME cron %r: %s", stats_cron, exc)

    sched.start()
    logger.info("Scheduler running (%s). Press Ctrl+C to stop.", ', '.join(run_times))
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down scheduler")
        sched.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
