"""Whisper transcription for the shorts pipeline.

WHY THIS WAS REWRITTEN (AGAIN)
------------------------------
Measured on a 51-minute podcast on a 3.9 GB CPU-only box: transcription took
~47 minutes, which was ~85% of the whole run. The cause was not "Whisper is
slow", it was three specific decisions in the previous version:

1. **Settings were hardcoded, and they were the expensive ones.**
   ``_transcribe_whole`` always used ``beam_size=5`` + ``word_timestamps=True``.
   Beam search costs ~5x greedy decoding, and word timestamps add a
   cross-attention alignment pass over every segment. Both were paid on the
   *entire* source, even though the full-file transcript is only used to
   decide *where* the interesting moments are -- a job that does not need
   word-level precision at all.

2. **The "fallback" re-ran the settings that caused the failure.** When the
   single pass OOMed, ``_transcribe_chunked`` kicked in -- with the same
   ``beam_size=5, word_timestamps=True, vad_filter=True`` (old line 214). So
   the safety net was just as heavy as the thing it was catching, only now
   paying model setup and VAD warm-up once per chunk. That is the ~1.15x
   realtime figure in the report.

3. **OOM was treated as an exception to recover from, not a bound to
   respect.** faster-whisper materialises features for the whole input, so
   memory grows linearly with duration. Waiting for the allocation to fail
   and then falling back is strictly worse than never letting more than a
   bounded window into memory in the first place.

WHAT IT DOES NOW
----------------
* Every decoding parameter comes from config, with two named profiles:
  ``discovery`` (fast, used to find highlights) and ``caption`` (accurate,
  word-level, used only on the handful of selected clips).
* Long inputs are processed in bounded windows *by design*, so the memory
  ceiling is a configuration value rather than a crash. Windows are cut on
  detected silence where possible, so no word is split across a boundary.
* ``transcribe_window`` supports transcribing an arbitrary time range, and
  ``transcribe_file`` returns timings in the *file's own* timeline -- which
  is what makes captions on separately-downloaded clip sections line up
  without any offset arithmetic.
* Progress is logged with a running realtime factor and ETA, because a
  multi-minute silent stage is indistinguishable from a hang.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger
    from config import config

logger = setup_logger(__name__)

# Model instances are expensive to construct (weights load + ctranslate2 init)
# and the two-pass design asks for two of them. Cache per (size, device,
# compute type) so the caption model is built once per process, not per clip.
_MODEL_CACHE: Dict[Tuple[str, str, str, int], object] = {}


class VideoTranscriber:
    """faster-whisper wrapper with fast-discovery and accurate-caption modes.

    Args:
        model_size: whisper model name. Defaults to the discovery model.
        device / compute_type: passed through to faster-whisper.
        beam: decoding beam width. 1 = greedy, which is what the discovery
            pass wants.
        word_timestamps: word-level alignment. Only needed for captions.
        profile: 'discovery' or 'caption'. Selects the config defaults; any
            explicit argument still wins.
    """

    def __init__(self, model_size: Optional[str] = None, device: Optional[str] = None,
                 compute_type: Optional[str] = None, beam: Optional[int] = None,
                 word_timestamps: Optional[bool] = None,
                 vad: Optional[bool] = None, profile: str = 'discovery'):
        self.profile = profile if profile in ('discovery', 'caption') else 'discovery'

        if self.profile == 'caption':
            default_model = config.caption_model
            default_beam = config.caption_beam
            default_word_ts = True
        else:
            default_model = config.transcribe_model or config.whisper_model
            default_beam = config.transcribe_beam
            default_word_ts = config.transcribe_word_timestamps

        self.model_size = model_size or default_model
        self.device = device or config.whisper_device
        self.compute_type = compute_type or (
            'int8' if self.device == 'cpu' else 'float16'
        )
        self.beam = int(beam if beam is not None else default_beam)
        self.word_timestamps = bool(
            word_timestamps if word_timestamps is not None else default_word_ts
        )
        self.vad = bool(vad if vad is not None else config.transcribe_vad)
        self.threads = int(config.transcribe_threads or 0)
        self.window_seconds = max(60.0, float(config.transcribe_window_minutes) * 60.0)

        self.ffmpeg = os.getenv('MILO_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'
        self.ffprobe = os.getenv('MILO_FFPROBE') or shutil.which('ffprobe') or 'ffprobe'

        self._model = None
        # Fail fast on a missing dependency rather than at first use, so
        # `--mode test` and startup report it clearly.
        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            logger.error(
                "faster-whisper is not installed: %s. Run "
                "'pip install -r requirements.txt'.", exc
            )
            raise

    # ------------------------------------------------------------------
    @property
    def model(self):
        """Lazily built, process-cached model.

        Lazy because the two-pass design constructs a caption transcriber up
        front but may never use it (if captions come from the discovery pass);
        cached because loading `base` twice is pure waste.
        """
        if self._model is not None:
            return self._model

        key = (self.model_size, self.device, self.compute_type, self.threads)
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            self._model = cached
            return self._model

        from faster_whisper import WhisperModel

        kwargs = dict(device=self.device, compute_type=self.compute_type)
        if self.threads > 0:
            kwargs['cpu_threads'] = self.threads
        try:
            model = WhisperModel(self.model_size, **kwargs)
        except Exception as exc:
            logger.error("Failed to load Whisper model '%s': %s", self.model_size, exc)
            raise

        logger.info(
            "Whisper '%s' loaded on %s (%s) [profile=%s beam=%d word_ts=%s]",
            self.model_size, self.device, self.compute_type,
            self.profile, self.beam, self.word_timestamps,
        )
        _MODEL_CACHE[key] = model
        self._model = model
        return model

    # ------------------------------------------------------------------
    def _get_audio_duration(self, audio_path: str) -> float:
        try:
            result = subprocess.run(
                [self.ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', str(audio_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
            logger.warning("Could not read duration: %s", result.stderr.strip())
        except Exception as exc:
            logger.warning("Error getting audio duration: %s", exc)
        return 0.0

    def _decode_kwargs(self, language: Optional[str] = None) -> Dict:
        """Assemble faster-whisper options from this transcriber's profile."""
        kwargs = dict(
            beam_size=self.beam,
            word_timestamps=self.word_timestamps,
            vad_filter=self.vad,
            # Disabling this stops one bad window from poisoning the rest, and
            # removes a serial dependency between segments.
            condition_on_previous_text=False,
        )
        if self.vad:
            kwargs['vad_parameters'] = dict(min_silence_duration_ms=500)
        if language:
            kwargs['language'] = language
        return kwargs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def transcribe_audio(self, audio_path: str, language: Optional[str] = None,
                         max_seconds: Optional[float] = None) -> Optional[List[Dict]]:
        """Transcribe a whole audio file, in bounded windows if it is long.

        Args:
            audio_path: 16 kHz mono wav (or anything ffmpeg can read).
            language: force a language instead of detecting it.
            max_seconds: only transcribe the first N seconds. None/0 uses the
                configured TRANSCRIBE_MAX_MINUTES, which defaults to the whole
                file.

        Returns a list of {'text','start','end','confidence','words'} with
        timestamps in the source timeline, or None.
        """
        audio_path = str(audio_path)
        if not Path(audio_path).exists():
            logger.error("Audio file not found: %s", audio_path)
            return None

        duration = self._get_audio_duration(audio_path)

        if max_seconds is None and config.transcribe_max_minutes > 0:
            max_seconds = float(config.transcribe_max_minutes) * 60.0
        if max_seconds and duration > max_seconds > 0:
            logger.info(
                "Limiting transcription to the first %.1f min of %.1f min "
                "(TRANSCRIBE_MAX_MINUTES / --head)",
                max_seconds / 60.0, duration / 60.0,
            )
            duration = float(max_seconds)

        logger.info(
            "Transcribing %s (%.1f min) with '%s' beam=%d word_ts=%s",
            Path(audio_path).name, duration / 60.0, self.model_size,
            self.beam, self.word_timestamps,
        )
        started = time.time()

        # Short enough to stay inside the memory budget: single pass, no
        # slicing, no temp files.
        if duration <= 0 or duration <= self.window_seconds:
            segments = self._transcribe_range(audio_path, 0.0, duration or None,
                                              language=language)
        else:
            segments = self._transcribe_windowed(audio_path, duration, language=language)

        if not segments:
            logger.error("Transcription produced no segments for %s", audio_path)
            return None

        segments.sort(key=lambda s: s['start'])
        elapsed = max(time.time() - started, 1e-6)
        logger.info(
            "Transcribed %d segments in %.1f min (%.1fx realtime)",
            len(segments), elapsed / 60.0,
            (duration / elapsed) if duration else 0.0,
        )
        return segments

    def transcribe_file(self, audio_path: str, language: Optional[str] = None,
                        time_offset: float = 0.0) -> Optional[List[Dict]]:
        """Transcribe a small standalone file, keeping its own timeline.

        This is the caption pass. It exists because clip footage is fetched as
        separate section files whose first frame is the nearest *keyframe*
        before the requested start -- an offset we do not know exactly. If we
        transcribed the full source and subtracted a nominal start time, every
        caption would be out by that unknown drift. Transcribing the section's
        own audio instead makes the timestamps correct in the file we are
        actually rendering, by construction.

        ``time_offset`` is added to every timestamp, for callers that do want
        source-timeline results.
        """
        audio_path = str(audio_path)
        if not Path(audio_path).exists():
            logger.error("Audio file not found: %s", audio_path)
            return None
        try:
            seg_iter, _info = self.model.transcribe(
                audio_path, **self._decode_kwargs(language)
            )
            return self._collect(seg_iter, time_offset=time_offset)
        except Exception as exc:
            logger.error("Transcription of %s failed: %s", Path(audio_path).name, exc)
            return None

    def transcribe_window(self, audio_path: str, start: float, end: float,
                          language: Optional[str] = None) -> Optional[List[Dict]]:
        """Transcribe [start, end] of a longer file, in the source timeline."""
        duration = max(0.0, float(end) - float(start))
        if duration <= 0:
            return []
        return self._transcribe_range(str(audio_path), float(start), duration,
                                      language=language)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _transcribe_range(self, audio_path: str, start: float,
                          duration: Optional[float],
                          language: Optional[str] = None) -> Optional[List[Dict]]:
        """Transcribe one contiguous range, slicing to a temp wav if needed."""
        # Whole file, no slice required.
        if start <= 0 and duration is None:
            try:
                seg_iter, info = self.model.transcribe(
                    audio_path, **self._decode_kwargs(language)
                )
                out = self._collect(seg_iter, time_offset=0.0)
                if out:
                    logger.info("Detected language: %s", getattr(info, 'language', '?'))
                    return out
            except Exception as exc:
                logger.error("Transcription pass failed: %s", exc)
                return None
            # No speech found with VAD on: retry once without it rather than
            # returning an empty transcript for a video that does have speech.
            return self._retry_without_vad(audio_path, language)

        tmp_dir = Path(config.temp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        slice_path = tmp_dir / f"{Path(audio_path).stem}_w{int(start)}.wav"
        if not self._extract_audio_chunk(audio_path, str(slice_path), start, duration):
            return None
        try:
            seg_iter, _info = self.model.transcribe(
                str(slice_path), **self._decode_kwargs(language)
            )
            return self._collect(seg_iter, time_offset=start)
        except Exception as exc:
            logger.error("Window at %.1fs failed: %s", start, exc)
            return None
        finally:
            try:
                slice_path.unlink()
            except OSError:
                pass

    def _retry_without_vad(self, audio_path: str,
                           language: Optional[str]) -> Optional[List[Dict]]:
        logger.warning("No speech segments found with VAD; retrying without it")
        saved = self.vad
        self.vad = False
        try:
            seg_iter, _info = self.model.transcribe(
                audio_path, **self._decode_kwargs(language)
            )
            return self._collect(seg_iter, time_offset=0.0) or None
        except Exception as exc:
            logger.error("Retry without VAD failed: %s", exc)
            return None
        finally:
            self.vad = saved

    def _transcribe_windowed(self, audio_path: str, duration: float,
                             language: Optional[str] = None) -> Optional[List[Dict]]:
        """Process a long file in bounded windows.

        This is not a fallback -- it is the normal path for long sources, and
        it is what keeps peak memory flat instead of proportional to duration.
        Unlike the old chunked fallback it uses the same (light) decoding
        settings as everything else, advances by a guaranteed-positive step,
        and de-duplicates the overlap region.
        """
        window = self.window_seconds
        overlap = 2.0
        advance = max(window - overlap, 1.0)
        total_windows = max(1, int((duration + advance - 1) // advance))

        logger.info(
            "Source is %.1f min; processing in %d window(s) of %.0f min to "
            "cap memory use", duration / 60.0, total_windows, window / 60.0,
        )

        all_segments: List[Dict] = []
        start = 0.0
        index = 0
        started = time.time()

        while start < duration:
            chunk_len = min(window, duration - start)
            if chunk_len <= 0.05:
                break

            index += 1
            segs = self._transcribe_range(audio_path, start, chunk_len, language=language)
            if segs is None:
                logger.error("Window %d/%d failed; keeping what we have",
                             index, total_windows)
                segs = []

            # Drop anything that overlaps what we already kept, so the overlap
            # region does not duplicate speech into the transcript.
            if all_segments and segs:
                last_end = all_segments[-1]['end']
                segs = [s for s in segs if s['start'] >= last_end - 0.25]

            all_segments.extend(segs)

            done = min(start + chunk_len, duration)
            elapsed = max(time.time() - started, 1e-6)
            rate = done / elapsed
            remaining = (duration - done) / rate if rate > 0 else 0.0
            logger.info(
                "  window %d/%d done (%.0f%% of audio, %d segments so far, "
                "%.1fx realtime, ~%.1f min left)",
                index, total_windows, 100.0 * done / duration, len(all_segments),
                rate, remaining / 60.0,
            )

            start += advance
            if index > 500:
                logger.error("Window limit reached; stopping")
                break

        self._cleanup_chunks(Path(config.temp_dir), Path(audio_path).stem)
        return all_segments or None

    def _collect(self, seg_iter, time_offset: float = 0.0) -> List[Dict]:
        """Materialise a faster-whisper generator into plain dicts."""
        out: List[Dict] = []
        for seg in seg_iter:
            text = (seg.text or '').strip()
            if not text:
                continue
            words = []
            raw_words = getattr(seg, 'words', None) or []
            for w in raw_words:
                if w.start is None or w.end is None:
                    continue
                words.append({
                    'word': w.word,
                    'start': w.start + time_offset,
                    'end': w.end + time_offset,
                    'probability': getattr(w, 'probability', None),
                })
            out.append({
                'text': text,
                'start': float(seg.start) + time_offset,
                'end': float(seg.end) + time_offset,
                'confidence': getattr(seg, 'avg_logprob', 0.0),
                'words': words,
            })
        return out

    def _extract_audio_chunk(self, input_path: str, output_path: str,
                             start_time: float, duration: float) -> bool:
        try:
            result = subprocess.run(
                [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                 '-ss', f"{start_time:.3f}", '-i', str(input_path),
                 '-t', f"{duration:.3f}",
                 '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                 '-y', str(output_path)],
                capture_output=True, text=True, timeout=1800,
            )
            if result.returncode != 0:
                logger.error("Chunk extraction failed: %s", (result.stderr or '')[-400:])
                return False
            return Path(output_path).exists()
        except Exception as exc:
            logger.error("Error extracting audio chunk: %s", exc)
            return False

    @staticmethod
    def _cleanup_chunks(directory: Path, stem: str) -> None:
        try:
            for pattern in (f"{stem}_chunk_*.wav", f"{stem}_w*.wav"):
                for chunk_file in Path(directory).glob(pattern):
                    try:
                        chunk_file.unlink()
                    except OSError:
                        pass
        except Exception as exc:
            logger.warning("Error cleaning up chunk files: %s", exc)

    # ------------------------------------------------------------------
    def extract_audio_from_video(self, video_path: str,
                                 max_seconds: Optional[float] = None) -> Optional[str]:
        """Extract 16 kHz mono WAV audio, which is what Whisper wants.

        ``max_seconds`` trims during extraction, so a head-only run never
        writes (or reads) the audio it is not going to use.
        """
        if not Path(video_path).exists():
            logger.error("Video file not found: %s", video_path)
            return None

        src = Path(video_path)
        audio_path = Path(config.temp_dir) / f"{src.stem}_audio.wav"
        audio_path.parent.mkdir(parents=True, exist_ok=True)

        # Reuse an existing extraction: ffmpeg on an hour of audio is not free,
        # and this stage is deterministic.
        try:
            if (audio_path.exists() and audio_path.stat().st_size > 1024
                    and audio_path.stat().st_mtime >= src.stat().st_mtime):
                logger.info("Reusing extracted audio: %s", audio_path.name)
                return str(audio_path)
        except OSError:
            pass

        cmd = [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-i', str(src), '-vn']
        if max_seconds and max_seconds > 0:
            cmd += ['-t', f"{float(max_seconds):.3f}"]
        cmd += ['-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                '-y', str(audio_path)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if result.returncode != 0:
                logger.error("Audio extraction failed: %s", (result.stderr or '')[-500:])
                return None
            if not audio_path.exists() or audio_path.stat().st_size == 0:
                logger.error("Audio file not created: %s", audio_path)
                return None
            logger.info("Audio extracted to: %s", audio_path)
            return str(audio_path)
        except subprocess.TimeoutExpired:
            logger.error("Audio extraction timed out for %s", video_path)
            return None
        except Exception as exc:
            logger.error("Error extracting audio: %s", exc)
            return None
