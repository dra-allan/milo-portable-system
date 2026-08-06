"""FFmpeg rendering for vertical Shorts.

Bugs fixed in this rewrite (all reproduced against a real 40s test source):

1. Cross-device rename. The final step did ``Path(tmp).rename(output)``, which
   raises ``OSError: [Errno 18] Invalid cross-device link`` whenever data/temp
   and the output directory live on different filesystems. The whole render
   succeeded and was then thrown away. Now uses shutil.move.

2. Captions were never in sync. Segment timestamps are absolute (source
   timeline), but after extracting a clip the timeline restarts at 0. The ASS
   file was written with the absolute times, so a clip cut at 10:00 had no
   captions for its first 10 minutes and the rest landed past the end.
   Timestamps are now rebased onto the clip.

3. Sub-second truncation. Cut points went through format_timestamp(), which
   returns HH:MM:SS and floors the fraction, so every clip drifted up to 1s
   from its scored boundary and could clip a word. Now passes float seconds.

4. Four sequential re-encodes (extract -> crop -> caption -> loudnorm), each
   decoding and encoding the video again: 4x the time and 4x generation loss.
   Now one filter chain, one encode.

5. Fixed 30-60s subprocess timeouts. A 60s 1080p clip cannot encode in 30s on
   a laptop CPU, so long clips were killed and reported as failures. Timeouts
   now scale with clip length.

6. Output was a crop of the source, so a 1280x720 input produced a 405x720
   video. YouTube Shorts wants 1080x1920. Now scales and pads to exactly
   1080x1920 with a blurred fill, so nothing is cropped away and the frame is
   always correct.

7. ASS escaping wrote a literal '\\n' into every caption line and did not
   escape the text properly.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger, sanitize_filename
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger, sanitize_filename
    from config import config

logger = setup_logger(__name__)

SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920

# The reference blur strength, applied at full 1080x1920 by the original code.
# Every cheaper backdrop mode is calibrated to look like this one.
REFERENCE_BLUR_SIGMA = 28.0
# Downscale factor for the 'cheap' backdrop. Measured at k=8 (136x240):
# SSIM 0.975 against the full-resolution blur, filter stage 3.07x faster.
CHEAP_BACKDROP_DIVISOR = 8


def build_background_filters(mode: str, width: int = SHORT_WIDTH,
                             height: int = SHORT_HEIGHT):
    """Build the scale/pad filter graph, returning (filters, output_label).

    Why this is worth its own function: ``gblur=sigma=28`` over a full
    1080x1920 frame was the most expensive operation in the entire pipeline --
    measured at 19.87s of filtering for a 20s clip, more than the H.264 encode
    itself (see BENCHMARKS.md).

    A Gaussian blur is scale-invariant: blurring an image downscaled by k with
    sigma S/k looks like blurring the original with sigma S. Since the
    backdrop is deliberately out of focus, it can be blurred at a fraction of
    the resolution and scaled back up. Deriving sigma from the divisor matters
    -- an earlier hand-picked sigma scored SSIM 0.870 and looked visibly
    wrong, while the derived value scores 0.975.
    """
    mode = (mode or 'cheap').lower()

    # fast_bilinear costs ~10% less than the default scaler and no one can see
    # the difference on a blurred backdrop or a downscale.
    fg = (f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease"
          f":flags=fast_bilinear[fgs]")

    if mode == 'black':
        # No backdrop at all: flat bars. Fastest (2.01x) but a different look.
        return ([f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease"
                 f":flags=fast_bilinear,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[padded]"], 'padded')

    if mode == 'crop':
        # Fill the frame by cropping the sides. No bars, but loses the edges.
        return ([f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase"
                 f":flags=fast_bilinear,crop={width}:{height}[padded]"], 'padded')

    if mode == 'blur':
        # The original, kept as the reference look for anyone who wants it.
        return ([
            "[0:v]split=2[bg][fg]",
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma={REFERENCE_BLUR_SIGMA:g}[bgb]",
            fg,
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[padded]",
        ], 'padded')

    # 'cheap' (default): blur small, then scale up.
    k = CHEAP_BACKDROP_DIVISOR
    bw = max(2, (width // k) // 2 * 2)
    bh = max(2, (height // k) // 2 * 2)
    sigma = REFERENCE_BLUR_SIGMA / k
    return ([
        "[0:v]split=2[bg][fg]",
        f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase"
        f":flags=fast_bilinear,crop={bw}:{bh},gblur=sigma={sigma:g},"
        f"scale={width}:{height}:flags=fast_bilinear[bgb]",
        fg,
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[padded]",
    ], 'padded')


class VideoEditor:
    def __init__(self):
        self.ffmpeg = os.getenv('MILO_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'
        self.ffprobe = os.getenv('MILO_FFPROBE') or shutil.which('ffprobe') or 'ffprobe'
        try:
            result = subprocess.run([self.ffmpeg, '-version'],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                raise RuntimeError('ffmpeg returned a non-zero exit status')
            first = (result.stdout or '').splitlines()[:1]
            logger.info("FFmpeg ready: %s", first[0] if first else 'unknown version')
        except FileNotFoundError:
            logger.error(
                "FFmpeg not found. Install it and make sure 'ffmpeg' is on PATH "
                "(or set MILO_FFMPEG to its full path)."
            )
            raise
        except Exception as exc:
            logger.error("Failed to initialise FFmpeg: %s", exc)
            raise

    # ------------------------------------------------------------------
    def probe_dimensions(self, path: str):
        """Return (width, height) or None."""
        try:
            result = subprocess.run(
                [self.ffprobe, '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=width,height', '-of', 'csv=p=0', path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.error("ffprobe failed on %s: %s", path, result.stderr.strip())
                return None
            parts = [p for p in re.split(r'[,\sx]+', result.stdout.strip()) if p]
            if len(parts) < 2:
                return None
            return int(parts[0]), int(parts[1])
        except Exception as exc:
            logger.error("Could not probe %s: %s", path, exc)
            return None

    def has_audio_stream(self, path: str) -> bool:
        try:
            result = subprocess.run(
                [self.ffprobe, '-v', 'error', '-select_streams', 'a:0',
                 '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', path],
                capture_output=True, text=True, timeout=30,
            )
            return 'audio' in (result.stdout or '')
        except Exception:
            return False

    # ------------------------------------------------------------------
    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        """ASS timestamps are H:MM:SS.cc and must not go negative."""
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centis = int(round((seconds - int(seconds)) * 100))
        if centis >= 100:
            centis = 99
        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

    @staticmethod
    def _escape_ass_text(text: str) -> str:
        """Escape for an ASS dialogue line and wrap long captions."""
        text = (text or '').strip()
        text = text.replace('\\', '\\\\').replace('{', '(').replace('}', ')')
        text = re.sub(r'\s+', ' ', text)
        # Keep captions to two readable lines rather than one long ribbon.
        words = text.split()
        if len(words) > 7:
            mid = (len(words) + 1) // 2
            text = ' '.join(words[:mid]) + r'\N' + ' '.join(words[mid:])
        return text

    def write_ass(self, transcript_segments: List[Dict], ass_path: Path,
                  time_offset: float = 0.0, clip_duration: Optional[float] = None,
                  font_size: Optional[int] = None) -> bool:
        """Write an ASS subtitle file with timestamps rebased onto the clip.

        Args:
            transcript_segments: segments carrying ABSOLUTE source timestamps.
            time_offset: clip start in the source timeline; subtracted from
                every timestamp so captions line up with the extracted clip.
            clip_duration: clamp/drop captions past the end of the clip.
        """
        font_size = font_size or config.caption_font_size
        try:
            ass_path.parent.mkdir(parents=True, exist_ok=True)
            with open(ass_path, 'w', encoding='utf-8') as f:
                f.write('[Script Info]\n')
                f.write('Title: Shorts captions\n')
                f.write('ScriptType: v4.00+\n')
                f.write('WrapStyle: 0\n')
                f.write('ScaledBorderAndShadow: yes\n')
                f.write(f'PlayResX: {SHORT_WIDTH}\n')
                f.write(f'PlayResY: {SHORT_HEIGHT}\n\n')

                f.write('[V4+ Styles]\n')
                f.write('Format: Name, Fontname, Fontsize, PrimaryColour, '
                        'SecondaryColour, OutlineColour, BackColour, Bold, Italic, '
                        'Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, '
                        'BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, '
                        'MarginV, Encoding\n')
                # Alignment 2 = bottom-centre; MarginV lifts it clear of the
                # Shorts UI overlay.
                f.write(
                    f'Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,'
                    f'&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,320,1\n\n'
                )

                f.write('[Events]\n')
                f.write('Format: Layer, Start, End, Style, Name, MarginL, MarginR, '
                        'MarginV, Effect, Text\n')

                written = 0
                for seg in transcript_segments or []:
                    start = float(seg.get('start', 0.0)) - time_offset
                    end = float(seg.get('end', 0.0)) - time_offset
                    if clip_duration is not None:
                        if start >= clip_duration:
                            continue        # entirely past the clip
                        end = min(end, clip_duration)
                    if end <= 0:
                        continue            # entirely before the clip
                    start = max(0.0, start)
                    if end <= start:
                        continue
                    text = self._escape_ass_text(seg.get('text', ''))
                    if not text:
                        continue
                    f.write(
                        f'Dialogue: 0,{self._format_ass_time(start)},'
                        f'{self._format_ass_time(end)},Default,,0,0,0,,{text}\n'
                    )
                    written += 1

            logger.debug("Wrote %d caption lines to %s", written, ass_path.name)
            return written > 0
        except Exception as exc:
            logger.error("Could not write subtitle file %s: %s", ass_path, exc)
            return False

    @staticmethod
    def _escape_filter_path(path: str) -> str:
        """Escape a path for use inside an FFmpeg filter argument."""
        p = str(path).replace('\\', '/')
        # Windows drive colons and filter separators must be escaped.
        p = p.replace(':', r'\:').replace("'", r"\'")
        return p

    # ------------------------------------------------------------------
    def create_short_from_segment(self, video_path: str, start_time: float,
                                  end_time: float, transcript_segments: List[Dict],
                                  output_path: str, add_branding: bool = False,
                                  burn_captions: bool = True,
                                  captions_are_clip_relative: bool = False,
                                  threads: Optional[int] = None) -> bool:
        """Render one vertical Short in a single FFmpeg pass.

        Pipeline: seek -> scale/pad to 1080x1920 -> burn captions ->
        loudness-normalise audio -> encode.

        Args:
            captions_are_clip_relative: when True, ``transcript_segments``
                already start at 0 for this clip, so no rebasing is applied.
                This is what the two-pass caption flow produces: the clip's own
                audio is transcribed, so its timings are correct in the file
                being rendered and must not be shifted again.
            threads: cap libx264 threads. Used when several renders run
                concurrently so they do not each try to claim every core.
        """
        src = Path(video_path)
        if not src.exists():
            logger.error("Source video not found: %s", video_path)
            return False

        start_time = max(0.0, float(start_time))
        end_time = float(end_time)
        duration = end_time - start_time
        if duration <= 0:
            logger.error(
                "Invalid clip bounds %.2f-%.2f (duration %.2f)",
                start_time, end_time, duration,
            )
            return False

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        temp_dir = Path(config.temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        ass_path = temp_dir / f"{sanitize_filename(out.stem)}.ass"
        # Render to a temp file next to the destination so the final move is
        # always same-filesystem (fixes the cross-device rename failure).
        staging = out.with_name(f".{out.stem}.partial.mp4")

        # --- video filter chain ------------------------------------------
        # Scale to fit inside 1080x1920 and fill the bars behind it. Nothing is
        # cropped out, and the result is exactly the resolution YouTube Shorts
        # expects. The backdrop strategy is configurable because the original
        # full-resolution gblur cost more than the video encode itself.
        filters, last_label = build_background_filters(config.background_mode)

        if burn_captions and transcript_segments:
            # Clip-relative captions are already on this file's timeline (the
            # two-pass flow transcribes the clip's own audio), so rebasing them
            # by start_time would shift every line out of sync.
            caption_offset = 0.0 if captions_are_clip_relative else start_time
            if self.write_ass(transcript_segments, ass_path,
                              time_offset=caption_offset, clip_duration=duration):
                filters.append(
                    f"[{last_label}]subtitles='{self._escape_filter_path(ass_path)}'[captioned]"
                )
                last_label = 'captioned'
            else:
                logger.warning("No caption lines fell inside the clip; skipping captions")

        filters.append(f"[{last_label}]format=yuv420p[vout]")
        filter_complex = ';'.join(filters)

        has_audio = self.has_audio_stream(str(src))

        cmd = [
            self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            # -ss before -i seeks fast; -t after gives an exact duration.
            # Both are floats, so no sub-second truncation.
            '-ss', f"{start_time:.3f}",
            '-i', str(src),
            '-t', f"{duration:.3f}",
            '-filter_complex', filter_complex,
            '-map', '[vout]',
        ]

        if has_audio:
            cmd += [
                '-map', '0:a:0',
                '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
                '-c:a', 'aac', '-b:a', '128k', '-ar', '44100',
            ]
        else:
            logger.warning("Source has no audio stream; rendering a silent clip")
            cmd += ['-an']

        cmd += [
            '-c:v', 'libx264',
            '-preset', config.video_preset,
            '-crf', str(config.video_crf),
            '-pix_fmt', 'yuv420p',
            '-r', '30',
            '-movflags', '+faststart',
        ]
        if threads and int(threads) > 0:
            cmd += ['-threads', str(int(threads))]
        cmd += ['-y', str(staging)]

        # Timeouts must scale with the work: a fixed 30s killed any real clip.
        timeout = max(300, int(duration * 30) + 120)

        logger.info(
            "Rendering %.2f-%.2fs (%.1fs) -> %s",
            start_time, end_time, duration, out.name,
        )

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timed out after %ss rendering %s", timeout, out.name)
            self._cleanup(staging, ass_path)
            return False
        except Exception as exc:
            logger.error("FFmpeg could not be launched: %s", exc)
            self._cleanup(staging, ass_path)
            return False

        if result.returncode != 0:
            logger.error(
                "FFmpeg failed (exit %s) for %s: %s",
                result.returncode, out.name,
                (result.stderr or '').strip()[-800:],
            )
            self._cleanup(staging, ass_path)
            return False

        if not staging.exists() or staging.stat().st_size == 0:
            logger.error("FFmpeg reported success but produced no output for %s", out.name)
            self._cleanup(staging, ass_path)
            return False

        try:
            if out.exists():
                out.unlink()
            # shutil.move handles cross-filesystem moves; Path.rename does not.
            shutil.move(str(staging), str(out))
        except Exception as exc:
            logger.error("Could not move rendered clip into place: %s", exc)
            self._cleanup(staging, ass_path)
            return False

        self._cleanup(None, ass_path)

        dims = self.probe_dimensions(str(out))
        logger.info(
            "Short ready: %s (%s, %.1f MB)",
            out, f"{dims[0]}x{dims[1]}" if dims else 'unknown',
            out.stat().st_size / (1024 * 1024),
        )
        return True

    @staticmethod
    def _cleanup(*paths):
        for p in paths:
            if not p:
                continue
            try:
                Path(p).unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Backwards-compatible helpers. The single-pass renderer above replaces
    # these for normal use, but they are kept because other tooling calls them.
    # ------------------------------------------------------------------
    def create_vertical_crop(self, input_path: str, output_path: str,
                             x_offset: int = 0, y_offset: int = 0) -> bool:
        """Scale/pad a video to 1080x1920 (kept for API compatibility)."""
        if not Path(input_path).exists():
            logger.error("Input video not found: %s", input_path)
            return False
        # Reuse the shared (and much cheaper) backdrop graph rather than
        # keeping a second copy of the expensive full-resolution blur.
        filters, last_label = build_background_filters(config.background_mode)
        filters.append(f"[{last_label}]format=yuv420p[vout]")
        cmd = [
            self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', input_path,
            '-filter_complex', ';'.join(filters),
            '-map', '[vout]', '-map', '0:a?',
            '-c:v', 'libx264', '-preset', config.video_preset,
            '-crf', str(config.video_crf), '-c:a', 'copy', '-y', output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            logger.error("Vertical conversion failed: %s", (result.stderr or '')[-500:])
            return False
        return Path(output_path).exists()

    def add_burn_in_captions(self, input_path: str, transcript_segments: List[Dict],
                             output_path: str, font_size: Optional[int] = None,
                             time_offset: float = 0.0) -> bool:
        """Burn captions into an existing clip (kept for API compatibility)."""
        if not Path(input_path).exists():
            logger.error("Input video not found: %s", input_path)
            return False
        ass_path = Path(config.temp_dir) / f"{Path(output_path).stem}.ass"
        if not self.write_ass(transcript_segments, ass_path,
                              time_offset=time_offset, font_size=font_size):
            logger.warning("No captions to burn; copying input through")
        cmd = [
            self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', input_path,
            '-vf', f"subtitles='{self._escape_filter_path(ass_path)}',format=yuv420p",
            '-c:v', 'libx264', '-preset', config.video_preset,
            '-crf', str(config.video_crf), '-c:a', 'copy', '-y', output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        self._cleanup(ass_path)
        if result.returncode != 0:
            logger.error("Caption burn failed: %s", (result.stderr or '')[-500:])
            return False
        return Path(output_path).exists()

    def normalize_audio(self, input_path: str, output_path: str) -> bool:
        """Loudness-normalise audio, copying video (kept for API compatibility)."""
        if not Path(input_path).exists():
            logger.error("Input video not found: %s", input_path)
            return False
        cmd = [
            self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', input_path,
            '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-y', output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            logger.error("Audio normalisation failed: %s", (result.stderr or '')[-500:])
            return False
        return Path(output_path).exists()
