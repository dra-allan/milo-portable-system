"""Whisper transcription for the shorts pipeline.

The previous implementation split audio into 30-second chunks and ran Whisper
once per chunk. Three problems, all of which the user's 22-minute transcription
of a 30-minute video demonstrates:

1. Infinite loop at the tail. The advance was ``start_time += actual_duration
   - overlap_duration``. Once ``actual_duration`` shrinks to the 2s overlap the
   position stops moving, so the loop spun on the same final 2 seconds until
   the ``chunk_index > 1000`` safety valve fired -- roughly 935 wasted Whisper
   invocations per run. Reproduced: the loop parks at start=1813.0 forever.
2. Chunking is unnecessary. faster-whisper streams long files natively and
   applies VAD across the whole timeline; 60 separate invocations pay the
   model's fixed setup cost 60 times and destroy cross-chunk context.
3. The 2s overlap was never de-duplicated, so every boundary injected ~2s of
   duplicated speech into the transcript -- duplicate captions and inflated
   segment counts.

Now: one streaming pass over the whole file, with an opt-in chunked fallback
(with a guaranteed-forward advance and overlap de-duplication) for the rare
case where a single pass fails.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger
    from config import config

logger = setup_logger(__name__)


class VideoTranscriber:
    def __init__(self, model_size: Optional[str] = None, device: Optional[str] = None,
                 compute_type: Optional[str] = None):
        self.model_size = model_size or config.whisper_model
        self.device = device or config.whisper_device
        self.compute_type = compute_type or (
            'int8' if self.device == 'cpu' else 'float16'
        )
        self.ffmpeg = os.getenv('MILO_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'
        self.ffprobe = os.getenv('MILO_FFPROBE') or shutil.which('ffprobe') or 'ffprobe'

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            logger.error(
                "faster-whisper is not installed: %s. Run "
                "'pip install -r requirements.txt'.", exc
            )
            raise

        try:
            self.model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
            logger.info(
                "Whisper model '%s' loaded on %s (%s)",
                self.model_size, self.device, self.compute_type,
            )
        except Exception as exc:
            logger.error("Failed to load Whisper model '%s': %s", self.model_size, exc)
            raise

    # ------------------------------------------------------------------
    def _get_audio_duration(self, audio_path: str) -> float:
        try:
            result = subprocess.run(
                [self.ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
            logger.warning("Could not read duration: %s", result.stderr.strip())
        except Exception as exc:
            logger.warning("Error getting audio duration: %s", exc)
        return 0.0

    # ------------------------------------------------------------------
    def transcribe_audio(self, audio_path: str,
                         language: Optional[str] = None) -> Optional[List[Dict]]:
        """Transcribe a whole audio file in one streaming pass.

        Returns a list of {'text','start','end','confidence','words'} or None.
        """
        if not Path(audio_path).exists():
            logger.error("Audio file not found: %s", audio_path)
            return None

        duration = self._get_audio_duration(audio_path)
        logger.info(
            "Transcribing %s (%.1fs) with '%s'",
            Path(audio_path).name, duration, self.model_size,
        )

        segments = self._transcribe_whole(audio_path, language=language)
        if segments is None:
            logger.warning("Single-pass transcription failed; trying chunked fallback")
            segments = self._transcribe_chunked(audio_path, duration, language=language)

        if not segments:
            logger.error("Transcription produced no segments for %s", audio_path)
            return None

        segments.sort(key=lambda s: s['start'])
        logger.info("Transcribed audio: %d segments", len(segments))
        return segments

    def _transcribe_whole(self, audio_path: str,
                          language: Optional[str] = None) -> Optional[List[Dict]]:
        """One pass over the whole file. faster-whisper streams internally."""
        for use_vad in (True, False):
            try:
                kwargs = dict(
                    beam_size=5,
                    word_timestamps=True,
                    vad_filter=use_vad,
                    condition_on_previous_text=False,
                )
                if use_vad:
                    kwargs['vad_parameters'] = dict(min_silence_duration_ms=500)
                if language:
                    kwargs['language'] = language

                seg_iter, info = self.model.transcribe(audio_path, **kwargs)
                out = self._collect(seg_iter, time_offset=0.0)

                if out:
                    logger.info(
                        "Detected language: %s (vad=%s)",
                        getattr(info, 'language', '?'), use_vad,
                    )
                    return out

                logger.warning(
                    "No speech segments found with vad=%s%s",
                    use_vad, "; retrying without VAD" if use_vad else "",
                )
            except Exception as exc:
                logger.error("Transcription pass (vad=%s) failed: %s", use_vad, exc)
                return None
        return None

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

    # ------------------------------------------------------------------
    def _transcribe_chunked(self, audio_path: str, duration: float,
                            chunk_seconds: float = 300.0, overlap: float = 3.0,
                            language: Optional[str] = None) -> Optional[List[Dict]]:
        """Fallback: large chunks, guaranteed forward progress, deduplicated.

        Kept only as a safety net. Note the two fixes versus the original:
        the advance is always positive, and overlapping segments are dropped.
        """
        if duration <= 0:
            return None

        # A minimum advance means the loop can never stall, no matter how the
        # remaining duration shrinks. This is the infinite-loop fix.
        advance = max(chunk_seconds - overlap, 1.0)
        all_segments: List[Dict] = []
        start = 0.0
        index = 0
        tmp_dir = Path(config.temp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        while start < duration:
            chunk_len = min(chunk_seconds, duration - start)
            if chunk_len <= 0.05:
                break

            chunk_path = tmp_dir / f"{Path(audio_path).stem}_chunk_{index}.wav"
            logger.info(
                "Chunk %d: %.1f-%.1fs", index, start, start + chunk_len
            )
            if not self._extract_audio_chunk(audio_path, str(chunk_path), start, chunk_len):
                logger.error("Failed to extract chunk %d", index)
                break

            try:
                kwargs = dict(beam_size=5, word_timestamps=True, vad_filter=True,
                              condition_on_previous_text=False)
                if language:
                    kwargs['language'] = language
                seg_iter, _ = self.model.transcribe(str(chunk_path), **kwargs)
                chunk_segments = self._collect(seg_iter, time_offset=start)
            except Exception as exc:
                logger.error("Chunk %d transcription failed: %s", index, exc)
                chunk_segments = []
            finally:
                try:
                    chunk_path.unlink()
                except OSError:
                    pass

            # De-duplicate the overlap region: drop segments that start before
            # the last kept segment ended. The original kept them, injecting
            # ~2s of duplicated speech at every boundary.
            if all_segments:
                last_end = all_segments[-1]['end']
                chunk_segments = [s for s in chunk_segments if s['start'] >= last_end - 0.25]

            all_segments.extend(chunk_segments)

            start += advance
            index += 1
            if index > 500:
                logger.error("Chunk limit reached; stopping")
                break

        self._cleanup_chunks(tmp_dir, Path(audio_path).stem)
        return all_segments or None

    def _extract_audio_chunk(self, input_path: str, output_path: str,
                             start_time: float, duration: float) -> bool:
        try:
            result = subprocess.run(
                [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                 '-ss', f"{start_time:.3f}", '-i', input_path,
                 '-t', f"{duration:.3f}",
                 '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                 '-y', output_path],
                capture_output=True, text=True, timeout=600,
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
            for chunk_file in Path(directory).glob(f"{stem}_chunk_*.wav"):
                try:
                    chunk_file.unlink()
                except OSError:
                    pass
        except Exception as exc:
            logger.warning("Error cleaning up chunk files: %s", exc)

    # ------------------------------------------------------------------
    def extract_audio_from_video(self, video_path: str) -> Optional[str]:
        """Extract 16kHz mono WAV audio, which is what Whisper wants."""
        if not Path(video_path).exists():
            logger.error("Video file not found: %s", video_path)
            return None

        src = Path(video_path)
        audio_path = Path(config.temp_dir) / f"{src.stem}_audio.wav"
        audio_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            result = subprocess.run(
                [self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                 '-i', str(src), '-vn',
                 '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                 '-y', str(audio_path)],
                capture_output=True, text=True, timeout=3600,
            )
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
