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
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
        self._caption_transcriber = None
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
    def caption_transcriber(self):
        """Accurate, word-level transcriber used only on selected clips.

        Separate from ``self.transcriber`` because the discovery pass runs with
        ``word_timestamps=False`` and a tiny model for speed -- fine for
        deciding *where* the highlights are, but useless for captions, which
        need per-word onsets to be able to reveal words as they are spoken.
        This pass only ever sees the few minutes of audio that were actually
        selected, so it can afford beam search and a larger model.
        """
        if self._caption_transcriber is None:
            try:
                from .transcriber import VideoTranscriber
            except ImportError:
                from transcriber import VideoTranscriber
            self._caption_transcriber = VideoTranscriber(
                profile='caption', device=config.whisper_device,
                word_timestamps=True,
            )
        return self._caption_transcriber

    def _transcript_from_subtitles(self, metadata: Dict) -> Optional[List[Dict]]:
        """Transcript from YouTube's published subtitles, or None.

        This is the single biggest speed win in the pipeline. Transcription was
        ~85% of runtime (a 65-minute source took ~50 minutes at 1.3x realtime),
        and the resulting transcript is only used to *rank* moments -- a job
        that does not need Whisper-grade text. Most sources already ship a
        transcript, which yt-dlp fetched alongside the audio for ~200 KB.

        Returns None when there is no track, when it is too sparse to rank
        against, or when the feature is switched off -- and the caller then
        falls back to Whisper, so nothing is lost.
        """
        if not getattr(self.config, 'use_youtube_subs', True):
            return None

        sub_path = metadata.get('subtitle_path')
        if not sub_path or not Path(sub_path).exists():
            return None

        try:
            from .subtitles import parse_subtitle_file
        except ImportError:
            from subtitles import parse_subtitle_file

        segments = parse_subtitle_file(sub_path)
        if not segments:
            return None

        # Sanity gate: a track that covers almost none of the source (a
        # forced-narrative or credits-only track) would starve highlight
        # detection. Require it to span a decent share of the duration.
        duration = float(metadata.get('duration') or 0)
        covered = float(segments[-1]['end']) - float(segments[0]['start'])
        if duration > 0 and covered < duration * 0.5:
            logger.info(
                "Published subtitles cover only %.0f%% of the source; "
                "falling back to Whisper", 100.0 * covered / duration,
            )
            return None

        logger.info(
            "FAST PATH: using YouTube's published transcript (%s) -- "
            "%d segments covering %.1f min, skipping the Whisper pass",
            Path(sub_path).name, len(segments), covered / 60.0,
        )
        return segments

    def _clip_word_transcript(self, video_path: str, start: float, end: float,
                              padding: float = 0.35,
                              language: Optional[str] = None):
        """Word-level transcript for one clip, in the CLIP's own timeline.

        Returns None on any failure, so the caller can fall back to the
        discovery transcript rather than losing the clip.

        The clip's audio is transcribed on its own rather than slicing the
        full-source transcript. That is deliberate: word onsets from the
        discovery pass are relative to the source, and rebasing them assumes
        the render cut the video at exactly ``start``. FFmpeg seeks to the
        nearest keyframe, so that assumption is wrong by up to a keyframe
        interval -- which is precisely the drift that makes word-level captions
        look out of sync even when segment-level ones looked fine.

        ``padding`` extends the extracted audio slightly so a word straddling
        the clip boundary is still decoded whole; the caption engine clamps
        anything outside the clip afterwards.

        ``language`` is the niche's Whisper hint. Passing it matters twice over:
        it skips the language-detection pass, and it stops a non-English clip
        being decoded as English -- which does not fail loudly, it produces
        confident nonsense that would then be burned into the video.
        """
        try:
            duration = float(end) - float(start)
            if duration <= 0:
                return None
            slice_start = max(0.0, float(start) - padding)
            lead_in = float(start) - slice_start
            slice_duration = duration + padding + lead_in

            tmp_dir = Path(self.config.temp_dir)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            wav = tmp_dir / f"capt_{int(start * 1000)}_{int(end * 1000)}.wav"

            tr = self.caption_transcriber
            if language:
                tr.language = language
            if not tr._extract_audio_chunk(video_path, str(wav),
                                           slice_start, slice_duration):
                return None
            try:
                # -lead_in shifts the slice's timeline back onto the clip's, so
                # t=0 is the first frame the renderer will emit.
                segments = tr.transcribe_file(str(wav), time_offset=-lead_in,
                                              language=language)
            finally:
                try:
                    wav.unlink()
                except OSError:
                    pass

            if not segments:
                return None
            if not any(seg.get('words') for seg in segments):
                logger.warning("Caption pass returned no word timings for "
                               "%.1f-%.1fs", start, end)
                return None
            return segments
        except Exception as exc:
            logger.warning("Word-level caption pass failed for %.1f-%.1fs: %s",
                           start, end, exc)
            return None

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
                                 local_only: bool = False,
                                 source_channel: str = '') -> bool:
        """Audio-only discovery -> transcribe -> find highlights -> section fetch -> render -> (upload).

        Every stage is resumable: an existing audio download, transcript, section
        files, or rendered clips are reused instead of being redone. Returns True
        if at least one Short is on disk when we finish.

        This avoids ever downloading the full source video (1-2 GB). Instead:
          1. Audio-only fetch (~40 MB for an hour) for discovery transcription
          2. Section fetch (clip ranges only, ~few MB each) for rendering

        Args:
            local_only: never download; fail if audio/sections not already cached.
            source_channel: the configured source handle the video came from
                (e.g. ``@AlexHormozi``). Stored on the processed-video row so
                the performance feedback loop can rank sources.
        """
        video_id = extract_video_id(video_id) or video_id
        logger.info("Starting processing for video %s", video_id)

        if not force and self.db.is_video_processed(video_id):
            logger.warning(
                "Video %s was already processed (use --force to redo it)", video_id
            )
            return False

        audio_path = None
        section_files = []
        try:
            # -- 1. audio-only download (or reuse) --------------------------
            logger.info("Step 1/6: Fetching audio for discovery (reusing existing if present)")
            if local_only:
                existing_audio = self.downloader.find_local_audio(video_id)
                if not existing_audio:
                    logger.error(
                        "No local audio for %s in %s. Run without --from-library to download.",
                        video_id, self.downloader.audio_dir,
                    )
                    self.stats['errors'] += 1
                    return False
                metadata = self.downloader._audio_metadata(video_id, existing_audio)
            else:
                metadata = self.downloader.download_audio(video_id)

            if not metadata or not metadata.get('audio_path'):
                logger.error("Could not obtain audio for %s", video_id)
                self.stats['errors'] += 1
                return False

            audio_path = metadata['audio_path']
            if not Path(audio_path).exists():
                logger.error("Audio file vanished: %s", audio_path)
                self.stats['errors'] += 1
                return False

            title = metadata.get('title') or video_id
            duration = metadata.get('duration') or 0
            logger.info(
                "%s: '%s' (%ss, %.1f MB)",
                "Reused existing audio" if metadata.get('from_cache') else "Downloaded audio",
                title, duration, Path(audio_path).stat().st_size / (1024 * 1024),
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
            logger.info("Step 2/6: Transcribing audio (cached transcripts are reused)")
            transcript = None if force else self.load_cached_transcript(video_id)

            # Per-niche captions/language policy. Non-English niches turn
            # captions off, because a wrong caption is worse than none.
            captions_on = bool(niche_config.get('captions', True))
            whisper_language = (niche_config.get('whisper_language')
                                or niche_config.get('language') or '') or None
            if whisper_language in ('en', 'english'):
                whisper_language = 'en'

            if transcript is None:
                # FAST PATH: YouTube already published a transcript for most
                # sources. Parsing it takes milliseconds instead of the ~50
                # minutes Whisper spent on a 65-minute source, and it is only
                # used to *locate* highlights, so ASR-grade text is sufficient.
                transcript = self._transcript_from_subtitles(metadata)

                if transcript is None:
                    self.transcriber.language = whisper_language
                    max_seconds = getattr(self.transcriber, 'max_seconds', None)
                    transcript = self.transcriber.transcribe_audio(
                        audio_path, language=whisper_language,
                        max_seconds=max_seconds,
                    )
                    if not transcript:
                        logger.error("Transcription produced nothing for %s", audio_path)
                        self.stats['errors'] += 1
                        return False
                    logger.info("Transcribed audio into %d segments", len(transcript))

                self.save_transcript(video_id, transcript, title)

            # -- 3. find highlights ----------------------------------------
            logger.info("Step 3/6: Finding highlight segments")
            clip_cap = self.config.max_clips_per_video
            if source_channel:
                perf = (self.db.source_performance() or {}).get(source_channel) or {}
                if perf.get('recorded') and float(perf.get('avg_views') or 0) >= \
                        self.config.winner_avg_views:
                    clip_cap = max(clip_cap, self.config.max_clips_per_video_winner)
                    logger.info(
                        "Source '%s' is a proven winner (avg %.0f views) -- "
                        "raising clip cap to %d",
                        source_channel, float(perf.get('avg_views') or 0), clip_cap,
                    )
            highlights = self.processor.find_highlight_segments(
                transcript,
                niche_keywords=niche_keywords,
                min_segment_length=self.config.min_segment_length,
                max_segment_length=self.config.max_segment_length,
                min_gap_between=self.config.min_gap_between_clips,
                max_clips=clip_cap,
                max_candidates=getattr(self.config, 'max_candidates', None),
                min_score=float(niche_config.get('min_score') or 0.0),
                ranking_mode=bool(niche_config.get('ranking_mode')),
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
                    'candidates': highlights,
                }
                self.save_clip_plan(video_id, plan)

            # -- 4. fetch sections (only the clip ranges) ------------------
            logger.info("Step 4/6: Fetching clip sections (%.1f MB each vs full video)",
                        self.config.section_padding * 2)
            ranges = [(h['start'], h['end']) for h in highlights]
            sections = self.downloader.download_sections(
                video_id, ranges,
                padding=self.config.section_padding,
                concurrency=self.config.download_concurrency,
                force_redownload=force,
            )

            # Filter out failed section downloads
            valid_highlights = []
            valid_sections = []
            for h, s in zip(highlights, sections):
                if s and s.get('path'):
                    valid_highlights.append(h)
                    valid_sections.append(s)
                else:
                    logger.warning("Section download failed for clip %.1f-%.1fs, skipping",
                                   h['start'], h['end'])

            if not valid_highlights:
                logger.error("No sections could be downloaded for %s", video_id)
                self.stats['errors'] += 1
                return False

            highlights = valid_highlights
            section_files = valid_sections
            logger.info("Fetched %d/%d sections successfully", len(highlights), len(ranges))

            # -- 5. render from section files -------------------------------
            safe_title = sanitize_filename(title) or video_id
            shorts_dir = Path(self.config.shorts_dir) / niche / safe_title
            shorts_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Step 5/6: Creating Shorts from section files")
            self.db.record_video(
                video_id, title, niche, duration,
                channel_id=source_channel or (metadata.get('uploader', '') or ''),
                published_at=metadata.get('upload_date'),
            )

            if not captions_on:
                logger.info(
                    "Niche '%s': captions disabled -- rendering without them "
                    "(and skipping the word-level caption pass)", niche,
                )

            # Plan every clip first, so the ones that actually need encoding
            # can be handed to a pool. Resume hits are settled here because
            # they touch the DB and must stay on the main thread.
            created = []
            todo = []
            for i, (highlight, section) in enumerate(zip(highlights, section_files), start=1):
                hook_text = (highlight.get('text') or '').strip()
                safe_hook = sanitize_filename(hook_text) if hook_text else f"clip{i}"
                if len(safe_hook) > 50:
                    safe_hook = safe_hook[:50]
                output_path = str(shorts_dir / f"{i:02d}_{safe_hook}.mp4")
                existing = Path(output_path)

                if not force and existing.exists() and existing.stat().st_size > 64 * 1024:
                    logger.info(
                        "Resume: clip %d/%d already rendered (%.1f MB) -- skipping",
                        i, len(highlights), existing.stat().st_size / (1024 * 1024),
                    )
                    self.stats['shorts_created'] += 1
                    created.append({'index': i, 'path': output_path, 'highlight': highlight})
                    self.db.record_short(
                        video_id, i, highlight['start'], highlight['end'],
                        title=hook_text, local_path=output_path,
                        score=highlight.get('score'),
                    )
                    continue

                todo.append((i, highlight, section, output_path))

            workers = max(1, int(getattr(self.config, 'render_workers', 1) or 1))
            workers = max(1, min(workers, len(todo))) if todo else 1
            # Split the CPU budget so concurrent encodes don't each try to
            # claim every core and thrash.
            per_render_threads = None
            if workers > 1:
                per_render_threads = max(1, (os.cpu_count() or 2) // workers)

            def render_one(item):
                i, highlight, section, output_path = item
                logger.info(
                    "Rendering clip %d/%d: %.1f-%.1fs (score %.2f)",
                    i, len(highlights), highlight['start'], highlight['end'],
                    highlight.get('score', 0.0),
                )

                section_path = section['path']
                clip_start_in_file = section['clip_start_in_file']
                clip_duration = section['clip_duration']

                clip_transcript = []
                clip_relative = False
                if captions_on:
                    # Accurate word-level pass on just this clip's audio. Only
                    # worth its cost when captions will actually be burned in.
                    if getattr(self.config, 'two_pass_captions', True):
                        words = self._clip_word_transcript(
                            section_path, clip_start_in_file,
                            clip_start_in_file + clip_duration,
                            language=whisper_language,
                        )
                        if words:
                            clip_transcript = words
                            clip_relative = True
                    if not clip_transcript:
                        clip_transcript = [
                            seg for seg in transcript
                            if not (seg['end'] <= highlight['start']
                                    or seg['start'] >= highlight['end'])
                        ]

                ok = self.video_editor.create_short_from_segment(
                    video_path=section_path,
                    start_time=clip_start_in_file,
                    end_time=clip_start_in_file + clip_duration,
                    transcript_segments=clip_transcript,
                    output_path=output_path,
                    add_branding=False,
                    burn_captions=captions_on,
                    captions_are_clip_relative=clip_relative,
                    threads=per_render_threads,
                    keywords=niche_keywords,
                )
                return i, highlight, output_path, ok

            if workers > 1:
                logger.info("Rendering %d clip(s) with %d parallel encode(s)",
                            len(todo), workers)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(render_one, todo))
            else:
                results = [render_one(item) for item in todo]

            for i, highlight, output_path, ok in sorted(results, key=lambda r: r[0]):
                if not ok or not Path(output_path).exists():
                    logger.error("Failed to create clip %d", i)
                    self.stats['errors'] += 1
                    continue

                self.stats['shorts_created'] += 1
                created.append({'index': i, 'path': output_path, 'highlight': highlight})
                hook_text = (highlight.get('text') or '').strip()
                self.db.record_short(
                    video_id, i, highlight['start'], highlight['end'],
                    title=hook_text, local_path=output_path,
                    score=highlight.get('score'),
                )
            created.sort(key=lambda c: c['index'])

            if not created:
                logger.error("No clips could be rendered for %s", video_id)
                return False

            # -- 6. upload --------------------------------------------------
            if self.upload_enabled:
                logger.info("Step 6/6: Uploading %d Shorts", len(created))
                self._upload_clips(created, video_id, niche, niche_keywords)
            else:
                logger.info(
                    "Step 6/6: Upload disabled (set UPLOAD_ENABLED=true to publish). "
                    "%d clips kept locally.", len(created)
                )

            self.stats['videos_processed'] += 1
            logger.info(
                "Finished %s -- %d clips in %s",
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
            # Clean up audio regardless of success or exception. Audio is
            # regenerable; section files are small and deliberately kept in
            # data/temp/sections/ so a retry can resume without re-fetching.
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    def _generate_unique_title(self, hook_text: str, niche: str, clip_index: int) -> str:
        """Generate a unique, attention-optimized title for a Short.

        Runs the raw hook through the rule-based title optimizer unless
        ``TITLE_OPTIMIZER=off`` in .env, then appends the niche + #Shorts
        hashtags that YouTube uses for the Shorts feed.
        """
        base = hook_text
        if self.config.title_optimizer:
            try:
                from .title_optimizer import optimize_title
                niche_cfg = self.config.get_niche_config(niche)
                base = optimize_title(
                    hook_text, niche=niche,
                    keywords=niche_cfg.get('keywords', []),
                    clip_index=clip_index,
                )
            except Exception:
                logger.warning("Title optimizer failed; using raw hook", exc_info=True)
                base = hook_text or ''

        base = ' '.join((base or '').split()).strip()
        if not base:
            return f"{niche} clip #{clip_index} #Shorts"
        return f"{base} #{niche} #Shorts"

    def _upload_clips(self, created: List[Dict], video_id: str, niche: str,
                      niche_keywords: List[str]) -> None:
        # Route this niche to its bound channels. If a niche has no token on
        # disk, YouTubeUploader(channel=...) logs a warning and falls back to
        # the default token -- so verify a token exists before we build it.
        channels = self.config.get_niche_channels(niche)
        if not channels:
            # Fall back to the single channel logic for backward compatibility.
            channel = self.config.get_niche_channel(niche)
            if not channel:
                logger.error(
                    "No YouTube channel bound to niche '%s' "
                    "(resolved channel=%r). Clips kept local. "
                    "Bind it in config/niches.yaml with `channel: <name>` or `channels: [...]` and run "
                    "`python -m src.uploader auth --channel <name>` once.",
                    niche, channel,
                )
                self.stats['errors'] += 1
                return
            channels = [channel]
        authed = set(self.config.authenticated_channels())

        # Prepare round-robin state if needed.
        if self.config.multichannel_upload_mode == 'round_robin':
            self._channel_index = 0

        try:
            # We'll create uploaders on demand and cache them in a dict.
            self._uploaders = {}
        except Exception:
            self._uploaders = {}

        def get_uploader_for_channel(channel_key: str):
            if channel_key not in self._uploaders:
                try:
                    from .uploader import YouTubeUploader
                except ImportError:
                    from uploader import YouTubeUploader
                try:
                    self._uploaders[channel_key] = YouTubeUploader(channel=channel_key)
                except Exception as exc:
                    logger.error(
                        "Failed to initialize YouTube client for channel '%s': %s",
                        channel_key, exc,
                    )
                    return None
            return self._uploaders[channel_key]

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

        # Per-source daily cap (Allan's cadence rule): drop clips from sources
        # that already hit UPLOAD_MAX_PER_SOURCE uploads in the last 24h --
        # including the run's own fresh video, so a rich source can't be
        # over-posted even when the backlog mixing is off.
        per_source_cap = getattr(self.config, 'upload_max_per_source', 3)
        per_source_left = {}
        filtered = []
        for item in queue:
            src = item.get('source_video_id', video_id)
            if src not in per_source_left:
                used = self.db.uploaded_count_for_source_since(src)
                per_source_left[src] = max(0, per_source_cap - used)
            if per_source_left[src] <= 0:
                logger.info(
                    "Skipping clip from %s: per-source daily cap (%d) reached",
                    src, per_source_cap,
                )
                continue
            per_source_left[src] -= 1
            filtered.append(item)
        queue = filtered

        # Per-channel daily budget (Allan's rule: max 6 shorts/channel/day).
        # Seed from what's already on YouTube for each bound channel, then keep
        # a running tally so a single sweep can't blow a channel's budget either.
        per_channel_cap = self.config.upload_max_per_channel
        channel_budget = {
            ch: max(0, per_channel_cap - self.db.uploaded_count_for_channel_since(ch))
            for ch in channels
        }
        logger.info(
            "Per-channel daily budget: %s",
            ', '.join(f"{ch}={b}/{per_channel_cap}" for ch, b in channel_budget.items()) or '(none)',
        )

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

            # Select channel based on multichannel mode, skipping channels that
            # already hit their per-channel daily budget.
            if self.config.multichannel_upload_mode == 'all':
                target_channels = [ch for ch in channels if channel_budget.get(ch, 0) > 0]
            elif self.config.multichannel_upload_mode == 'first':
                target_channels = ([channels[0]] if channel_budget.get(channels[0], 0) > 0
                                   else [])
            else:  # round_robin
                # Pick the next channel in round-robin fashion that still has
                # daily budget left; try up to a full rotation of the list.
                target_channels = []
                for _ in range(len(channels)):
                    cand = channels[self._channel_index % len(channels)]
                    self._channel_index += 1
                    if channel_budget.get(cand, 0) > 0:
                        target_channels = [cand]
                        break
            if not target_channels:
                logger.info(
                    "Skipping clip %d: no bound channel has daily budget left "
                    "(cap %d/channel)",
                    item['index'], per_channel_cap,
                )
                continue

            # Anti-burst pacing: space uploads by a random delay so a batch
            # doesn't hit the feed together. Skipped for the first clip.
            if self.stats['shorts_uploaded'] and self.config.upload_pacing_max:
                delay = random.uniform(
                    self.config.upload_pacing_min, self.config.upload_pacing_max
                )
                logger.info("Pacing: waiting %.0f-%.0fs before next upload",
                            self.config.upload_pacing_min, delay)
                time.sleep(delay)

            uploaded_any = False
            for channel_key in target_channels:
                # Skip if the channel is not authenticated (unless there are no auth tokens at all).
                if authed and channel_key not in authed:
                    logger.warning(
                        "Skipping upload for clip %d to channel '%s': no authentication token found.",
                        item['index'], channel_key,
                    )
                    continue
                uploader = get_uploader_for_channel(channel_key)
                if uploader is None:
                    logger.error(
                        "Could not initialize uploader for channel '%s'. Skipping upload for clip %d.",
                        channel_key, item['index'],
                    )
                    self.stats['errors'] += 1
                    continue
                try:
                    short_id = uploader.upload_short(
                        video_path=item['path'],
                        title=short_title,
                        description=description,
                        tags=tags,
                    )
                except Exception as exc:
                    logger.error("Upload raised for clip %d to channel '%s': %s",
                                 item['index'], channel_key, exc)
                    short_id = None

                if short_id:
                    logger.info("Uploaded clip %d to channel '%s' as %s",
                                item['index'], channel_key, short_id)
                    self.stats['shorts_uploaded'] += 1
                    self.db.mark_short_uploaded(item_source, item['index'], short_id,
                                                channel=channel_key)
                    if channel_key in channel_budget:
                        channel_budget[channel_key] = max(0, channel_budget[channel_key] - 1)
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
                    uploaded_any = True
                else:
                    logger.error("Upload failed for clip %d to channel '%s' (kept locally)",
                                 item['index'], channel_key)
                    self.stats['errors'] += 1

            # If none of the target channels succeeded, we still count the item as processed
            # (errors have been incremented above). If at least one succeeded, we consider
            # the clip uploaded (no additional error count).
            if not uploaded_any and self.stats['errors'] == 0:
                # This should not happen because we increment errors on each failure.
                pass

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
                                     max_videos=max_videos, lookback=lookback,
                                     source_performance=self.db.source_performance())

        for skip in result.skipped_already_processed:
            logger.info("Niche '%s': %s already processed -- skipping", niche, skip)
        if result.skipped_duration:
            logger.info("Niche '%s': %d outside duration band -- skipping",
                        niche, len(result.skipped_duration))
        if result.skipped_negative_keywords:
            logger.info("Niche '%s': %d negative-keyword titles -- skipping",
                        niche, len(result.skipped_negative_keywords))
        if result.skipped_min_views:
            logger.info("Niche '%s': %d below min_views threshold -- skipping",
                        niche, len(result.skipped_min_views))

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
            ok = self.process_video_for_shorts(
                vid, niche, source_channel=entry.get('_source_channel', ''),
            )
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
    parser.add_argument('--mode', choices=['once', 'schedule', 'test', 'library', 'stats', 'discover', 'upload-existing', 'migrate-shorts'],
                        default='once',
                        help="'library' lists videos already downloaded and can "
                             "process them without touching the network; "
                             "'stats' fetches YouTube metrics for uploaded shorts; "
                             "'discover' dry-runs scheduled discovery for bound "
                             "niches (no downloads); "
                             "'upload-existing' uploads rendered-but-unpublished shorts; "
                             "'migrate-shorts' restructures legacy shorts layout (see also --dry-run)")
    parser.add_argument('--niche', default=None,
                        help='Niche name from config/niches.yaml (default: auto-detect)')
    parser.add_argument('--videos', type=int, default=1,
                        help='Videos per niche when sweeping (default: 1)')
    parser.add_argument('--upload', dest='upload', action='store_true', default=None,
                        help='Upload results to YouTube (overrides UPLOAD_ENABLED)')
    parser.add_argument('--no-upload', dest='upload', action='store_false',
                        help='Never upload, just render locally')
    parser.add_argument('--upload-limit', type=int, default=None, metavar='N',
                        help='Max clips to upload in --mode upload-existing '
                             '(default: config.UPLOAD_MAX_PER_RUN or 5)')
    parser.add_argument('--channel', default=None,
                        help='Override target YouTube channel for --mode upload-existing '
                             '(defaults to niche-bound channel)')
    parser.add_argument('--source', default=None,
                        help='With --mode upload-existing: only clips cut from this '
                             'source video (URL or 11-character ID)')
    parser.add_argument('--segment', type=int, default=None, metavar='N',
                        help='With --mode upload-existing: only this clip/segment index')
    parser.add_argument('--interactive', action='store_true',
                        help='With --mode upload-existing: list every candidate clip '
                             'grouped by niche/source and pick which ones to upload')
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
    parser.add_argument('--dry-run', action='store_true',
                        help='With --mode migrate-shorts: show what would be migrated without making changes')
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

    if args.mode == 'migrate-shorts':
        # Run the migration script inline
        from .migrate_shorts import migrate_shorts
        moved, updated, errors = migrate_shorts(dry_run=args.dry_run, force=args.force)
        if errors:
            logger.error("Migration completed with %d errors", errors)
            return 1
        logger.info("Migration successful: %d moved, %d updated", moved, updated)
        return 0

    if args.mode == 'upload-existing':
        return _upload_existing_shorts(pipeline, args)

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


def _upload_existing_shorts(pipeline: 'ShortsPipeline', args) -> int:
    """Upload rendered-but-unpublished shorts to YouTube.

    Queries the database for clips with youtube_short_id IS NULL, applies
    optional niche/channel/source/segment filters, and uploads up to the
    configured limit. Respects quota limits and updates DB on success.

    Cross-channel safety: when ``--channel`` is passed, only clips whose niche
    is bound to that channel are auto-selected -- the old behaviour picked the
    oldest clips from *every* niche and posted them to whatever channel was
    chosen, which is how a clip from another niche's folder ended up on the
    wrong channel. ``--interactive`` instead shows every candidate grouped by
    niche/source and lets you choose exactly which clips to publish.
    """
    upload_limit = args.upload_limit
    if upload_limit is None:
        upload_limit = getattr(pipeline.config, 'upload_max_per_run', 5)
    logger.info("Upload limit: %d clips", upload_limit)

    # Pull a larger candidate pool so interactive selection has the full
    # picture; the cap is still enforced at upload time.
    pool_size = max(upload_limit * 3, 100)
    unuploaded = pipeline.db.unuploaded_shorts(limit=pool_size)
    if not unuploaded:
        logger.info("No un-uploaded shorts found in database")
        return 0

    # --- Filters --------------------------------------------------------
    if args.niche:
        unuploaded = [r for r in unuploaded if r.get('niche') == args.niche]
        logger.info("Filtered to niche '%s': %d clips", args.niche, len(unuploaded))

    if args.source:
        src = extract_video_id(args.source) if not args.source.isalnum() or len(args.source) > 11 else args.source
        unuploaded = [r for r in unuploaded if r.get('source_video_id') == src]
        logger.info("Filtered to source '%s': %d clips", src, len(unuploaded))

    if args.segment is not None:
        unuploaded = [r for r in unuploaded if r.get('segment_index') == args.segment]
        logger.info("Filtered to segment %d: %d clips", args.segment, len(unuploaded))

    if not unuploaded:
        logger.info("No clips to upload after filtering")
        return 0

    # Cross-channel safety: a channel override must not drag in clips whose
    # niche belongs to a different channel. Interactive mode shows the user
    # each clip's target channel so they can override deliberately.
    if args.channel and not args.interactive:
        scoped = []
        for r in unuploaded:
            clip_niche = r.get('niche') or args.niche or ''
            bound = pipeline.config.get_niche_channel(clip_niche) if clip_niche else args.channel
            if bound == args.channel or not clip_niche:
                scoped.append(r)
            else:
                logger.info(
                    "Skipping clip %s#%s (niche '%s' binds to channel '%s', "
                    "not '%s') -- pass --interactive to override",
                    r['source_video_id'], r['segment_index'], clip_niche, bound, args.channel,
                )
        unuploaded = scoped
        if not unuploaded:
            logger.warning(
                "No clips bound to channel '%s'. Use --interactive to pick "
                "clips from another niche on purpose.", args.channel
            )
            return 0

    # --- Interactive selection ------------------------------------------
    if args.interactive:
        selected = _interactive_pick_shorts(pipeline, unuploaded, upload_limit)
        if not selected:
            logger.info("No clips selected; nothing to upload")
            return 0
        unuploaded = selected
    else:
        unuploaded = unuploaded[:upload_limit]
        if not unuploaded:
            logger.info("No clips to upload after filtering")
            return 0

    logger.info("Preparing to upload %d clip(s)", len(unuploaded))

    uploaded_count = 0
    errors = 0

    for clip in unuploaded:
        source_video_id = clip['source_video_id']
        segment_index = clip['segment_index']
        local_path = clip['local_path']
        clip_niche = clip.get('niche') or args.niche

        # Anti-burst pacing: space uploads by a random delay so a batch
        # doesn't hit the feed together. Skipped for the first clip.
        if uploaded_count and pipeline.config.upload_pacing_max:
            delay = random.uniform(
                pipeline.config.upload_pacing_min, pipeline.config.upload_pacing_max
            )
            logger.info("Pacing: waiting %.0f-%.0fs before next upload",
                        pipeline.config.upload_pacing_min, delay)
            time.sleep(delay)

        # Validate file exists
        if not local_path or not Path(local_path).exists():
            logger.warning(
                "Clip file missing: %s#%s at %s -- skipping",
                source_video_id, segment_index, local_path
            )
            errors += 1
            continue

        # Resolve target channel
        channel = args.channel
        if not channel:
            if clip_niche:
                channel = pipeline.config.get_niche_channel(clip_niche)
            if not channel:
                logger.error(
                    "No authenticated channel for niche '%s' (clip %s#%s) -- skipping",
                    clip_niche, source_video_id, segment_index
                )
                errors += 1
                continue

        # Verify channel is authenticated
        authed = pipeline.config.authenticated_channels()
        if authed and channel not in authed:
            logger.error(
                "Channel '%s' not authenticated for clip %s#%s -- skipping",
                channel, source_video_id, segment_index
            )
            errors += 1
            continue

        # Per-channel daily cap: don't post more than UPLOAD_MAX_PER_CHANNEL
        # shorts to one channel in 24h, even when uploading by hand.
        per_channel_cap = pipeline.config.upload_max_per_channel
        used_this_channel = pipeline.db.uploaded_count_for_channel_since(channel)
        if used_this_channel >= per_channel_cap:
            logger.info(
                "Channel '%s' already at per-channel daily cap (%d/%d) -- "
                "skipping %s#%s",
                channel, used_this_channel, per_channel_cap,
                source_video_id, segment_index,
            )
            errors += 1
            continue

        # Get uploader for channel
        try:
            uploader = pipeline._uploader_for_channel(channel)
        except Exception as exc:
            logger.error(
                "Failed to initialize uploader for channel '%s': %s",
                channel, exc
            )
            errors += 1
            continue

        # Build title/description/tags from clip data (title goes through the
        # optimizer so a published clip gets a headline, not a raw hook).
        highlight_text = clip.get('title') or clip_niche or 'Short'
        hook = highlight_text.strip().replace('\n', ' ')
        short_title = pipeline._generate_unique_title(hook, clip_niche or 'short', segment_index)

        keywords = []
        if clip_niche:
            niche_config = pipeline.config.get_niche_config(clip_niche)
            keywords = niche_config.get('keywords', [])

        description = (
            f"Full video: https://youtube.com/watch?v={source_video_id}\n\n"
            f"Follow for more {clip_niche} content!\n"
            f"#Shorts #{clip_niche} "
            + ' '.join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
        )
        tags = [clip_niche, 'Shorts'] + [kw for kw in keywords[:10] if kw]

        # Upload with quota error handling
        try:
            short_id = uploader.upload_short(
                video_path=local_path,
                title=short_title,
                description=description,
                tags=tags,
            )
        except Exception as exc:
            # Check for quota exceeded
            err_str = str(exc).lower()
            if 'quota' in err_str or '403' in err_str or 'rate' in err_str:
                logger.error(
                    "YouTube quota exceeded or rate limited: %s. Stopping upload.",
                    exc
                )
                # Re-raise to signal quota exhaustion to caller
                raise
            logger.error(
                "Upload failed for %s#%s: %s",
                source_video_id, segment_index, exc
            )
            errors += 1
            continue

        if not short_id:
            logger.error(
                "Upload returned no video ID for %s#%s",
                source_video_id, segment_index
            )
            errors += 1
            continue

# Mark as uploaded in database
        try:
            pipeline.db.mark_short_uploaded(source_video_id, segment_index, short_id,
                                            channel=channel)
            uploaded_count += 1
            logger.info(
                "Uploaded %s#%s -> %s (via channel %s)",
                source_video_id, segment_index, short_id, channel
            )
        except Exception as exc:
            logger.warning(
                "Upload succeeded but DB update failed for %s#%s: %s",
                source_video_id, segment_index, exc
            )
            # Still count as uploaded since it's on YouTube
            uploaded_count += 1

    logger.info("Upload complete: %d uploaded, %d errors", uploaded_count, errors)
    return 0 if errors == 0 else 1


def _interactive_pick_shorts(pipeline, clips, upload_limit: int) -> List[Dict]:
    """Show candidate clips grouped by niche/source and let the user choose.

    Displays each clip with a number, its niche, the source video title, the
    segment, the optimized title preview, score, and target channel. The user
    can answer with numbers/ranges (``1,2,4-6``), ``all``, or ``q`` to abort.
    Returns the subset of ``clips`` the user selected.
    """
    if not clips:
        return []
    print("\n" + "=" * 78)
    print(f"  Un-uploaded shorts available ({len(clips)} total)")
    print("=" * 78)

    # Group by (niche, source_video_id) preserving DB order.
    groups = []
    seen = set()
    for c in clips:
        key = (c.get('niche') or '', c['source_video_id'])
        if key not in seen:
            seen.add(key)
            groups.append(key)

    index = 0
    for niche, source_id in groups:
        grp = [c for c in clips if (c.get('niche') or '', c['source_video_id']) == (niche, source_id)]
        source_title = grp[0].get('source_title') or source_id
        bound = pipeline.config.get_niche_channel(niche) if niche else '(no niche)'
        print(f"\n  [{niche or 'no-niche'} -> channel {bound}]")
        print(f"    source: {source_title}  ({source_id})")
        for c in grp:
            index += 1
            hook = (c.get('title') or '').strip().replace('\n', ' ')
            preview = pipeline._generate_unique_title(hook, niche or 'short', c['segment_index'])
            dur = max(0, (c.get('end_time') or 0) - (c.get('start_time') or 0))
            score = c.get('score') or 0
            mark = "NEW" if c.get('local_path') else "MISSING"
            print(f"    [{index:>2}] seg {c['segment_index']:>2} | {dur:>3.0f}s | "
                  f"score {score:>5.1f} | {mark}")
            print(f"          {preview}")

    print("\n" + "-" * 78)
    print("Select clips to upload. Formats: 1,2,3 | 1-5 | all | q (quit)")
    print(f"Up to {upload_limit} will be posted.")
    while True:
        raw = input("Selection: ").strip().lower()
        if raw in ('q', 'quit', ''):
            return []
        if raw == 'all':
            return clips[:upload_limit]
        try:
            picks = set()
            for part in raw.replace(' ', '').split(','):
                if not part:
                    continue
                if '-' in part:
                    lo, hi = part.split('-', 1)
                    picks.update(range(int(lo), int(hi) + 1))
                else:
                    picks.add(int(part))
            if not picks:
                raise ValueError
            selected = [c for i, c in enumerate(clips, 1) if i in picks]
            if not selected:
                raise ValueError
            return selected[:upload_limit]
        except (ValueError, TypeError):
            print("Couldn't read that. Try e.g. '1,2,4-6', 'all', or 'q'.")


def _render_more_from_clip_plan(pipeline: 'ShortsPipeline', video_id: str,
                                count: int, force: bool = False,
                                args=None) -> int:
    """Render N additional clips from a saved clip plan.

    This is the real ``--render-more`` path: it loads the deep ranked plan
    cached after transcription, skips every candidate that already has a
    rendered clip (so a mid-render crash is resumed, not redone), renders the
    next N in rank order, records them, and uploads them. It never downloads
    and never re-transcribes: both are already on disk.
    """
    plan = pipeline.load_clip_plan(video_id)
    if not plan:
        logger.error(
            "No cached clip plan for %s. Run the pipeline on it once first "
            "(or --force) so a deep plan is written.", video_id
        )
        return 1
    candidates = plan.get('candidates') or []
    if not candidates:
        logger.error("Clip plan for %s has no candidates", video_id)
        return 1
    done = pipeline.db.rendered_segment_indices(video_id)
    # segment_index in the DB is the 1-based position in the candidates list.
    remaining = [(i + 1, c) for i, c in enumerate(candidates)
                 if (i + 1) not in done]
    if not remaining:
        logger.info("Video %s: all %d planned clips already rendered -- nothing to do",
                    video_id, len(candidates))
        return 0

    picks = remaining[:count] if count > 0 else remaining
    logger.info(
        "Render-more: %d candidate(s) remain, %d already rendered, rendering %d more",
        len(remaining), len(candidates) - len(remaining), len(picks),
    )

    video_path = pipeline.downloader.find_local_video(video_id)
    if not video_path:
        logger.error(
            "No local copy of %s. --render-more never downloads; re-run the "
            "pipeline on the video so its download is restored.", video_id
        )
        return 1
    transcript = pipeline.load_cached_transcript(video_id)
    if not transcript:
        logger.error(
            "No cached transcript for %s. --render-more never re-transcribes; "
            "re-run the pipeline on the video once to rebuild it.", video_id
        )
        return 1

    title = plan.get('title') or video_id
    niche = plan.get('niche') or guess_niche({'title': title})
    niche_keywords = plan.get('niche_keywords') or []
    safe_title = sanitize_filename(title) or video_id
    shorts_dir = Path(config.shorts_dir) / niche / safe_title
    shorts_dir.mkdir(parents=True, exist_ok=True)

    created: List[Dict] = []
    for seg_index, highlight in picks:
        hook_text = (highlight.get('text') or '').strip()
        safe_hook = sanitize_filename(hook_text) if hook_text else f"clip{seg_index}"
        if len(safe_hook) > 50:
            safe_hook = safe_hook[:50]
        output_path = str(shorts_dir / f"{seg_index:02d}_{safe_hook}.mp4")
        existing = Path(output_path)
        if not force and existing.exists() and existing.stat().st_size > 64 * 1024:
            logger.info("Resume: clip %d already rendered -- skipping", seg_index)
            created.append({'index': seg_index, 'path': output_path, 'highlight': highlight})
            pipeline.db.record_short(
                video_id, seg_index, highlight['start'], highlight['end'],
                title=hook_text, local_path=output_path, score=highlight.get('score'),
            )
            continue

        logger.info(
            "Rendering clip %d: %.1f-%.1fs (score %.2f)",
            seg_index, highlight['start'], highlight['end'],
            highlight.get('score', 0.0),
        )
        clip_transcript = [
            seg for seg in transcript
            if not (seg['end'] <= highlight['start']
                    or seg['start'] >= highlight['end'])
        ]
        ok = pipeline.video_editor.create_short_from_segment(
            video_path=str(video_path),
            start_time=highlight['start'],
            end_time=highlight['end'],
            transcript_segments=clip_transcript,
            output_path=output_path,
            add_branding=False,
        )
        if not ok or not Path(output_path).exists():
            logger.error("Failed to create clip %d", seg_index)
            pipeline.stats['errors'] += 1
            continue

        pipeline.stats['shorts_created'] += 1
        created.append({'index': seg_index, 'path': output_path, 'highlight': highlight})
        hook_text = (highlight.get('text') or '').strip()
        pipeline.db.record_short(
            video_id, seg_index, highlight['start'], highlight['end'],
            title=hook_text, local_path=output_path, score=highlight.get('score'),
        )

    if not created:
        logger.error("No clips could be rendered from the plan for %s", video_id)
        return 1

    if pipeline.upload_enabled:
        logger.info("Uploading %d render-more Shorts", len(created))
        pipeline._upload_clips(created, video_id, niche, niche_keywords)
    else:
        logger.info("Upload disabled; %d render-more clips kept locally.", len(created))
    pipeline.stats['videos_processed'] += 1
    return 0


def _render_more_from_plan(pipeline: 'ShortsPipeline', video_id: str,
                           count: int, force: bool = False, args=None) -> int:
    """List (and optionally process) videos already downloaded to data/temp.

    Two entry points share this function:

    * ``--mode library`` (count=0): the interactive library browser -- lists
      every downloaded video and lets the user pick one to process.
    * ``--render-more N`` (count>0): replays the saved clip plan for the target
      video and renders N more clips with zero re-download / re-transcribe.

    The library path never downloads; it was written to replace run_pipeline.bat's
    fragile inline PowerShell that parsed .info.json files by hand.
    """
    if count > 0:
        return _render_more_from_clip_plan(pipeline, video_id, count, force, args)

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


def _niche_backlog_supply(pipeline: 'ShortsPipeline', niche: str) -> List[Dict]:
    """Un-uploaded, on-disk clips for a niche (oldest first).

    Used by the pull-once scheme: if a niche still has clips waiting to post,
    the sweep should spend its uploads on those instead of downloading and
    clipping yet another source video. Clips whose file vanished are excluded
    so a missing file can't keep a niche 'supplied' forever.
    """
    try:
        rows = pipeline.db.unuploaded_shorts(limit=100)
    except AttributeError:
        return []
    return [r for r in rows
            if (r.get('niche') or '') == niche and r.get('local_path')
            and Path(r['local_path']).exists()]


# ----------------------------------------------------------------------
# Queue health and scheduling helpers
# ----------------------------------------------------------------------
def _expire_stale_backlog(pipeline: 'ShortsPipeline', niche: str) -> int:
    """Mark stale backlog clips as expired based on TTL config."""
    try:
        ttl = getattr(pipeline.config, 'backlog_ttl_days', 7)
        return pipeline.db.expire_stale_backlog(niche, ttl)
    except Exception as exc:
        logger.warning("Backlog expiry failed for %s: %s", niche, exc)
        return 0


def _get_queue_health(pipeline: 'ShortsPipeline', niche: str,
                      channels: List[str]) -> Dict:
    """Compute queue health metrics for a niche."""
    health = pipeline.db.get_queue_health(niche)

    # Add per-source cap awareness
    per_source_cap = getattr(pipeline.config, 'upload_max_per_source', 3)
    capped = []
    eligible = 0
    for src, count in health['source_counts'].items():
        used = pipeline.db.uploaded_count_for_source_since(src)
        if used >= per_source_cap:
            capped.append(src)
        else:
            eligible += min(count, per_source_cap - used)
    health['eligible_clips'] = eligible
    health['capped_sources'] = capped

    # Add channel capacity
    per_channel_cap = getattr(pipeline.config, 'upload_max_per_channel', 6)
    channel_remaining = 0
    for ch in channels:
        used = pipeline.db.uploaded_count_for_channel_since(ch)
        channel_remaining += max(0, per_channel_cap - used)
    health['channel_remaining'] = channel_remaining

    # Oldest clip age
    try:
        with pipeline.db._connect() as conn:
            row = conn.execute(
                """SELECT MIN(created_at) FROM generated_shorts g
                   LEFT JOIN processed_videos p ON p.youtube_video_id = g.source_video_id
                   WHERE g.youtube_short_id IS NULL AND g.status = 'queued'
                   AND (p.niche = ? OR ? = '')""",
                (pipeline.db.get_niche_channel(niche) if hasattr(pipeline.db, 'get_niche_channel') else niche, niche),
            ).fetchone()
            if row and row[0]:
                from datetime import datetime
                oldest = datetime.fromisoformat(row[0].replace(' ', 'T'))
                health['oldest_clip_age_days'] = (datetime.now() - oldest).days
    except Exception:
        health['oldest_clip_age_days'] = 0

    return health


def _should_discover_more(pipeline: 'ShortsPipeline', niche: str,
                          health: Dict, channels: List[str]) -> tuple:
    """Decide whether to run fresh discovery for a niche.

    Returns (should_discover: bool, reason: str)
    """
    cfg = pipeline.config

    # Check if niche is active
    niche_cfg = config.get_niche_config(niche)
    max_videos = niche_cfg.get('max_videos', 0) or getattr(cfg, 'schedule_max_videos', 3)
    if max_videos <= 0:
        return False, 'niche_inactive'

    # Total queued clips below target
    target = getattr(cfg, 'queue_target_total', 12)
    if health['total_queued'] < target:
        return True, f'total_queued_below_target ({health["total_queued"]}/{target})'

    # Not enough distinct sources
    min_distinct = getattr(cfg, 'queue_min_distinct_sources', 4)
    if health['distinct_sources'] < min_distinct:
        return True, f'distinct_sources_low ({health["distinct_sources"]}/{min_distinct})'

    # Top source dominance too high
    max_share = getattr(cfg, 'queue_max_top_source_share', 0.5)
    if health['top_source_share'] > max_share:
        return True, f'top_source_dominance_high ({health["top_source_share"]:.2f}/{max_share})'

    # Channel has capacity but not enough eligible clips
    if health['channel_remaining'] > 0 and health['eligible_clips'] < health['channel_remaining']:
        return True, 'channel_capacity_unused'

    # All/most queued clips are source-capped
    if health['total_queued'] > 0 and health['eligible_clips'] == 0:
        return True, 'all_clips_source_capped'

    # Check last discovery time (simplified: if no new source added recently)
    # This would need a last_discovery timestamp in DB; skip for now

    return False, 'queue_healthy'


def _upload_backlog_supply(pipeline: 'ShortsPipeline', niche: str, cap: int,
                           channels: List[str]) -> int:
    """Post up to ``cap`` un-uploaded clips for a niche, using fair source rotation.

    Skips source-capped clips instead of stopping. Returns how many clips uploaded.
    Quota errors abort the run.
    """
    authed = config.authenticated_channels()
    if not channels:
        return 0
    channels = [c for c in channels if not authed or c in authed]
    if not channels:
        return 0

    # Get clips with fair source rotation
    supply = pipeline.db.get_queued_clips_for_upload(niche, limit=cap * 2)
    if not supply:
        return 0

    # Drop rows whose rendered file is gone before the caps are applied.
    #
    # The DB is the source of truth for "queued", but the MP4 lives on disk and
    # can disappear independently (retention sweep, manual cleanup, a moved
    # working directory). Those rows stayed 'queued' forever: every run picked
    # them, spent a selection slot on them, failed with "Video file not found",
    # and left them queued to fail again on the next run. In the observed logs
    # this consumed the entire per-run cap while uploading nothing.
    #
    # Marking them 'missing' takes them out of the queue permanently and frees
    # the slot for a clip that actually exists.
    present = []
    missing = 0
    for clip in supply:
        local_path = (clip.get('local_path') or '').strip()
        if local_path and Path(local_path).exists():
            present.append(clip)
            continue
        missing += 1
        pipeline.db.update_clip_status(
            clip['source_video_id'], clip['segment_index'], 'missing')
    if missing:
        logger.warning(
            "Niche '%s': %d queued clip(s) had no file on disk; marked 'missing' "
            "so they stop consuming upload slots", niche, missing,
        )
    supply = present
    if not supply:
        return 0

    # Per-source and per-channel caps
    per_source_cap = getattr(config, 'upload_max_per_source', 3)
    per_channel_cap = getattr(config, 'upload_max_per_channel', 6)
    src_left = {}
    for clip in supply:
        src = clip['source_video_id']
        if src not in src_left:
            used = pipeline.db.uploaded_count_for_source_since(src)
            src_left[src] = max(0, per_source_cap - used)
    channel_left = {}
    for ch in channels:
        used = pipeline.db.uploaded_count_for_channel_since(ch)
        channel_left[ch] = max(0, per_channel_cap - used)

    # Round-robin across sources, then assign to channels round-robin
    selected = []  # (clip, channel)
    cursor = 0

    # Group by source
    by_source = {}
    for clip in supply:
        src = clip['source_video_id']
        if src not in by_source:
            by_source[src] = []
        by_source[src].append(clip)

    sources = sorted(by_source.keys(), key=lambda s: len(by_source[s]))
    source_pointers = {s: 0 for s in sources}

    for clip in supply:
        if len(selected) >= cap:
            break
        src = clip['source_video_id']
        if src_left.get(src, 0) <= 0:
            continue

        # Find next channel with budget
        chosen = None
        for _ in range(len(channels)):
            cand = channels[cursor % len(channels)]
            cursor += 1
            if channel_left.get(cand, 0) > 0:
                chosen = cand
                break
        if chosen is None:
            break

        channel_left[chosen] -= 1
        src_left[src] -= 1
        selected.append((clip, chosen))

    if not selected:
        logger.info(
            "Niche '%s': backlog clips present but per-source/per-channel daily caps reached",
            niche,
        )
        return 0

    # Log queue health using the wrapper that computes channel remaining
    health = _get_queue_health(pipeline, niche, channels)
    logger.info(
        "QUEUE_HEALTH niche=%s total=%d eligible=%d distinct_sources=%d top_source_share=%.2f channel_remaining=%d",
        niche, health['total_queued'], health['eligible_clips'],
        health['distinct_sources'], health['top_source_share'],
        health['channel_remaining'],
    )

    uploaders = {}

    def uploader_for(channel_key: str):
        if channel_key not in uploaders:
            uploaders[channel_key] = pipeline._uploader_for_channel(channel_key)
        return uploaders[channel_key]

    uploaded = 0
    per_channel_uploaded = {ch: 0 for ch in channels}
    for clip, channel in selected:
        source_video_id = clip['source_video_id']
        segment_index = clip['segment_index']
        local_path = clip['local_path']
        clip_niche = clip.get('niche') or niche
        keywords = (config.get_niche_config(clip_niche)
                    .get('keywords', [])) if clip_niche else []

        if uploaded and config.upload_pacing_max:
            delay = random.uniform(config.upload_pacing_min, config.upload_pacing_max)
            logger.info("Pacing: waiting %.0f-%.0fs before next upload",
                        config.upload_pacing_min, delay)
            time.sleep(delay)

        hook = (clip.get('title') or '').strip().replace('\n', ' ')
        short_title = pipeline._generate_unique_title(hook, clip_niche, segment_index)
        description = (
            f"Full video: https://youtube.com/watch?v={source_video_id}\n\n"
            f"Follow for more {clip_niche} content!\n"
            f"#Shorts #{clip_niche} "
            + ' '.join(f"#{kw.replace(' ', '')}" for kw in keywords[:3])
        )
        tags = [clip_niche, 'Shorts'] + [kw for kw in keywords[:10] if kw]
        try:
            uploader = uploader_for(channel)
        except Exception as exc:
            logger.error("Cannot start uploader for channel '%s': %s", channel, exc)
            continue
        try:
            short_id = uploader.upload_short(
                video_path=local_path, title=short_title,
                description=description, tags=tags,
            )
        except Exception as exc:
            err_str = str(exc).lower()
            if 'quota' in err_str or '403' in err_str or 'rate' in err_str:
                logger.error("YouTube quota/rate limit hit draining backlog: %s", exc)
                break
            logger.error("Backlog upload failed for %s#%s: %s",
                         source_video_id, segment_index, exc)
            continue
        if short_id:
            pipeline.stats['shorts_uploaded'] += 1
            uploaded += 1
            per_channel_uploaded[channel] += 1
            pipeline.db.mark_short_uploaded(source_video_id, segment_index, short_id,
                                            channel=channel)
            try:
                stats = uploader.fetch_statistics(short_id)
                if stats:
                    pipeline.db.record_performance(
                        short_id, source_video_id, segment_index,
                        views=stats['views'], likes=stats['likes'],
                        comments=stats['comments'], favorites=stats['favorites'],
                    )
            except Exception as exc:
                logger.warning("Could not snapshot stats for %s: %s", short_id, exc)
        else:
            logger.error("Backlog upload failed for %s#%s (kept locally)",
                         source_video_id, segment_index)
    logger.info(
        "Backlog drain: %d/%d clip(s) uploaded for niche '%s' (%s)",
        uploaded, len(selected), niche,
        ', '.join(f"{ch}={n}" for ch, n in per_channel_uploaded.items() if n) or 'none',
    )
    return uploaded


def _discover_and_render(pipeline: 'ShortsPipeline', niche: str, cap: int,
                         channels: List[str]) -> int:
    """Run fresh discovery and rendering for a niche."""
    if not pipeline.upload_enabled:
        return 0
    max_videos = int(config.get_niche_config(niche).get('max_videos') or 0)
    if max_videos <= 0:
        max_videos = getattr(config, 'schedule_max_videos', 3)
    max_videos = min(max_videos, cap)
    return pipeline.run_niche(niche, max_videos=max_videos)


def _run_scheduled_sweep(pipeline: 'ShortsPipeline', args) -> int:
    """Run every channel-bound niche with the new queue-based scheduling.

    Flow per niche:
    1. Expire stale backlog clips (TTL)
    2. Upload eligible backlog clips (respecting caps, fair source rotation)
    3. Compute queue health
    4. If queue unhealthy -> run fresh discovery/rendering
    5. If channel capacity remains -> try uploading more backlog

    This replaces the old pull-once model where backlog blocked discovery.
    """
    per_niche_default = getattr(config, 'schedule_max_videos', 3)
    total_budget = getattr(config, 'schedule_max_total', 0)
    niches = [args.niche] if args.niche else config.niche_names()
    if not niches:
        logger.error("No niches configured and no video specified. Nothing to do.")
        return 0

    started_total = 0
    for niche in niches:
        cap = int(config.get_niche_config(niche).get('max_videos') or 0)
        if cap <= 0:
            cap = per_niche_default
        if total_budget:
            cap = min(cap, total_budget - started_total)
        if cap <= 0:
            logger.info("Scheduled sweep total budget (%d videos) exhausted",
                        total_budget)
            break

        # Skip niches without authenticated upload channels.
        #
        # An empty `authed` means "no per-channel token files exist", which is
        # the normal single-channel setup: auth comes from the one default
        # token instead. run_niche() and _upload_backlog_supply() both treat
        # that as usable, but this gate used `any(c in authed ...)` which is
        # unconditionally False for an empty list -- so a single-channel
        # install had every niche skipped here and the sweep did nothing,
        # while the other two code paths would happily have run. Mirror the
        # permissive check the rest of the pipeline uses.
        channels = config.get_niche_channels(niche)
        authed = config.authenticated_channels()
        usable = [c for c in channels if not authed or c in authed]
        if not usable:
            logger.info(
                "Niche '%s': no authenticated upload channel bound "
                "(resolved channels=%r, authed=%s) -- skipping until bound in "
                "config/niches.yaml with `channel: <name>` and authenticated",
                niche, channels, authed or ['(default token)'],
            )
            continue

        channels = usable

        # 1. Expire stale backlog
        expired = _expire_stale_backlog(pipeline, niche)
        if expired:
            logger.info("Niche '%s': expired %d stale backlog clip(s) (TTL %d days)",
                        niche, expired, getattr(config, 'backlog_ttl_days', 7))

        # 2. Upload eligible backlog clips
        backlog_cap = config.upload_max_per_run
        uploaded_backlog = _upload_backlog_supply(pipeline, niche, backlog_cap, channels)

        # 3. Compute queue health after backlog drain
        health = _get_queue_health(pipeline, niche, channels)

        # 4. Decide whether to discover fresh sources
        should_discover, reason = _should_discover_more(pipeline, niche, health, 
                                                         [c for c in channels if c in authed])

        logger.info(
            "QUEUE_HEALTH niche=%s total=%d eligible=%d distinct_sources=%d "
            "top_source_share=%.2f channel_remaining=%d reason=%s",
            niche, health['total_queued'], health['eligible_clips'],
            health['distinct_sources'], health['top_source_share'],
            health['channel_remaining'], reason if should_discover else 'none',
        )

        # 5. If queue unhealthy, run discovery (if we have budget)
        if should_discover:
            remaining_cap = cap
            if remaining_cap > 0:
                logger.info("DISCOVERY_TRIGGER niche=%s reason=%s", niche, reason)
                discovered = _discover_and_render(pipeline, niche, cap, 
                                                   [c for c in channels if c in authed])
                started_total += discovered
                # Re-evaluate health after discovery
                health = _get_queue_health(pipeline, niche, channels)

        # 5b. If channel capacity remains, try one more backlog pass
        if health['channel_remaining'] > 0 and health['eligible_clips'] > 0:
            uploaded_more = _upload_backlog_supply(pipeline, niche, 
                                                    health['channel_remaining'], channels)
            if uploaded_more:
                logger.info("Niche '%s': uploaded %d additional backlog clip(s)",
                            niche, uploaded_more)

        logger.info("Niche '%s': sweep complete", niche)

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

    # Anti-burst jitter: add a random minute offset to each fixed run time so
    # the pipeline never fires on the same :00 every day. A daily sweep landing
    # at 9:00:00 vs 9:27:31 doesn't matter to us but varies the moment the
    # batch enters YouTube's feed test.
    jitter = getattr(config, 'schedule_jitter_minutes', 0)
    if jitter:
        jittered = []
        for cron in run_times:
            parts = cron.split()
            if len(parts) == 5:
                try:
                    parts[0] = str(random.randint(0, min(jitter, 59)))
                except (ValueError, TypeError):
                    pass
            jittered.append(' '.join(parts))
        run_times = jittered
        logger.info("Run times jittered by up to %d minute(s): %s",
                    jitter, ', '.join(run_times))

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
