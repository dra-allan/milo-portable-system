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
from typing import Dict, List, Optional, Tuple

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger, sanitize_filename
    from .config import config
    from . import captions as captions_mod
    from . import smart_crop
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger, sanitize_filename
    from config import config
    import captions as captions_mod
    import smart_crop

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
                             height: int = SHORT_HEIGHT,
                             scaler: Optional[str] = None):
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

    **Scaler choice (quality fix).** Every scale in here used to be
    ``flags=fast_bilinear``, including the one that produces the *sharp
    foreground* -- the part the viewer actually looks at. fast_bilinear is the
    lowest-quality scaler swscale offers; on the resize that lands the source
    into a 1080x1920 frame it visibly softens edges and text. It is now used
    only for the blurred backdrop, where by definition no detail survives, and
    the foreground uses ``scaler`` (lanczos by default).
    """
    mode = (mode or 'cheap').lower()
    fg_flags = (scaler or 'lanczos').strip() or 'lanczos'
    # Only the backdrop keeps the cheap scaler: it is about to be Gaussian
    # blurred, so scaler quality is unobservable and the speed is free.
    bg_flags = 'fast_bilinear'

    fg = (f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease"
          f":flags={fg_flags}[fgs]")

    if mode == 'black':
        # No backdrop at all: flat bars. Fastest (2.01x) but a different look.
        return ([f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease"
                 f":flags={fg_flags},"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[padded]"],
                'padded')

    if mode == 'crop':
        # Fill the frame by cropping the sides. No bars, but loses the edges.
        return ([f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase"
                 f":flags={fg_flags},crop={width}:{height},setsar=1[padded]"],
                'padded')

    if mode == 'smart':
        # Person-aware framing needs the source file to inspect, so it cannot
        # be built here. VideoEditor._build_smart_background_filters() handles
        # 'smart' before this function is ever reached; this branch only exists
        # so a stray direct call degrades to a sane centre-fill instead of
        # raising. (It used to return letterboxed bars, which silently looked
        # nothing like the mode the user asked for.)
        return build_background_filters('crop', width, height, scaler=fg_flags)

    if mode == 'blur':
        # The original, kept as the reference look for anyone who wants it.
        return ([
            "[0:v]split=2[bg][fg]",
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase"
            f":flags={bg_flags},"
            f"crop={width}:{height},gblur=sigma={REFERENCE_BLUR_SIGMA:g}[bgb]",
            fg,
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[padded]",
        ], 'padded')

    # 'cheap' (default): blur small, then scale up.
    k = CHEAP_BACKDROP_DIVISOR
    bw = max(2, (width // k) // 2 * 2)
    bh = max(2, (height // k) // 2 * 2)
    sigma = REFERENCE_BLUR_SIGMA / k
    return ([
        "[0:v]split=2[bg][fg]",
        f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase"
        f":flags={bg_flags},crop={bw}:{bh},gblur=sigma={sigma:g},"
        f"scale={width}:{height}:flags={bg_flags}[bgb]",
        fg,
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[padded]",
    ], 'padded')


class VideoEditor:
    # Populated once per process by available_fonts(); see the note there on
    # libass' silent font substitution.
    _font_cache = None

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

    def probe_fps(self, path: str) -> Optional[float]:
        """Source framerate as a float, or None if it cannot be determined.

        Reads ``r_frame_rate``, which ffprobe reports as a rational such as
        ``30000/1001``. Parsing the fraction rather than rounding is what keeps
        29.97 from being reported as 30 and then resampled -- a resample that
        duplicates or drops one frame per second and shows up as a visible
        stutter on panning shots.
        """
        try:
            result = subprocess.run(
                [self.ffprobe, '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=r_frame_rate', '-of',
                 'default=nw=1:nk=1', path],
                capture_output=True, text=True, timeout=30,
            )
            raw = (result.stdout or '').strip().splitlines()
            if result.returncode != 0 or not raw:
                return None
            value = raw[0].strip()
            if '/' in value:
                num, den = value.split('/', 1)
                den_f = float(den)
                if den_f == 0:
                    return None
                fps = float(num) / den_f
            else:
                fps = float(value)
            return fps if 1.0 <= fps <= 240.0 else None
        except Exception as exc:
            logger.debug("Could not probe framerate of %s: %s", path, exc)
            return None

    def _choose_fps(self, path: str) -> Optional[float]:
        """Output framerate: the source's own, capped at ``video_max_fps``.

        Returns None to mean "pass the source rate through untouched", which is
        strictly better than restating it -- no resampling filter is inserted at
        all, so timestamps survive exactly.
        """
        cap = float(getattr(config, 'video_max_fps', 60) or 60)
        fps = self.probe_fps(path)
        if fps is None:
            return None
        if fps > cap + 0.01:
            logger.info("Source is %.3f fps; capping output at %g fps", fps, cap)
            return cap
        # Within the cap: keep the source rate, don't touch it.
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
                  font_size: Optional[int] = None,
                  keywords: Optional[List[str]] = None,
                  style: Optional[str] = None) -> bool:
        """Write the ASS caption file for a clip.

        Routes to the word-level viral engine (:mod:`captions`) for every style
        except ``legacy``, which keeps the old segment-per-line behaviour for
        anyone who depends on it.

        The previous version of this method rendered one dialogue line per
        Whisper segment -- a 15-25 word paragraph held static for 5-10 seconds
        -- and its "hormozi" preset truncated the text to the first 3 words,
        silently discarding most of the speech. See captions.py.

        Args:
            transcript_segments: segments carrying ABSOLUTE source timestamps
                (unless ``time_offset`` is 0 because they are already clip
                relative).
            time_offset: clip start in the source timeline; subtracted from
                every timestamp so captions line up with the extracted clip.
            clip_duration: clamp/drop captions past the end of the clip.
            keywords: niche keywords, which bias which word gets emphasised.
        """
        style = (style or getattr(config, 'caption_style', 'viral') or 'viral').lower()

        if style == 'legacy':
            return self._write_ass_legacy(
                transcript_segments, ass_path, time_offset=time_offset,
                clip_duration=clip_duration, font_size=font_size,
            )

        try:
            document = captions_mod.build_viral_ass(
                transcript_segments,
                preset_name=style,
                time_offset=time_offset,
                clip_duration=clip_duration,
                keywords=keywords or [],
                font_size=font_size or getattr(config, 'caption_font_size', None),
                max_words=getattr(config, 'caption_max_words', None),
                available_fonts=self.available_fonts(),
                play_res=(SHORT_WIDTH, SHORT_HEIGHT),
                punch_ratio=getattr(config, 'caption_punch_ratio', 0.22),
            )
        except Exception as exc:
            logger.error("Caption generation failed: %s", exc, exc_info=True)
            return False

        if not document:
            logger.warning("No caption words fell inside the clip")
            return False

        try:
            ass_path.parent.mkdir(parents=True, exist_ok=True)
            with open(ass_path, 'w', encoding='utf-8') as f:
                f.write(document)
        except Exception as exc:
            logger.error("Could not write subtitle file %s: %s", ass_path, exc)
            return False
        return True

    # ------------------------------------------------------------------
    @classmethod
    def available_fonts(cls):
        """Font family names known to fontconfig, or None if unavailable.

        Needed because libass *silently* substitutes its default for a missing
        family. Without this check a preset asking for Montserrat ExtraBold on
        a box that lacks it renders in a plain fallback and looks nothing like
        the intended style -- with no warning anywhere.

        Cached: fc-list takes ~100ms and the answer cannot change mid-run.
        """
        if cls._font_cache is not None:
            return cls._font_cache or None
        names = set()
        fc = shutil.which('fc-list')
        if fc:
            try:
                result = subprocess.run(
                    [fc, '--format', '%{family}\n'],
                    capture_output=True, text=True, timeout=20,
                )
                if result.returncode == 0:
                    for line in (result.stdout or '').splitlines():
                        for family in line.split(','):
                            family = family.strip()
                            if family:
                                names.add(family)
            except Exception as exc:
                logger.debug("fc-list failed: %s", exc)
        cls._font_cache = names
        if not names:
            logger.debug("Could not enumerate fonts; using preset font as-is")
        return names or None

    # ------------------------------------------------------------------
    def _write_ass_legacy(self, transcript_segments: List[Dict], ass_path: Path,
                          time_offset: float = 0.0,
                          clip_duration: Optional[float] = None,
                          font_size: Optional[int] = None) -> bool:
        """One dialogue line per transcript segment (the pre-rewrite look)."""
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
                f.write(
                    f'Style: Default,{captions_mod.resolve_font("Montserrat ExtraBold", self.available_fonts())},'
                    f'{font_size},&H00FFFFFF,&H000000FF,'
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
                            continue
                        end = min(end, clip_duration)
                    if end <= 0:
                        continue
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
            logger.debug("Wrote %d legacy caption lines to %s", written, ass_path.name)
            return written > 0
        except Exception as exc:
            logger.error("Could not write subtitle file %s: %s", ass_path, exc)
            return False

    @staticmethod
    def _escape_filter_path(path: str) -> str:
        """Escape a path for use inside an FFmpeg filter argument."""
        p = str(path).replace('\\', '/')
        # Windows drive colons and single quotes must be escaped.
        # Inside FFmpeg's single-quoted filter strings, ' becomes ''
        p = p.replace(':', r'\:').replace("'", "''")
        return p

    # ------------------------------------------------------------------
    def create_short_from_segment(self, video_path: str, start_time: float,
                                  end_time: float, transcript_segments: List[Dict],
                                  output_path: str, add_branding: bool = False,
                                  burn_captions: bool = True,
                                  captions_are_clip_relative: bool = False,
                                  threads: Optional[int] = None,
                                  keywords: Optional[List[str]] = None,
                                  caption_style: Optional[str] = None) -> bool:
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

        scaler = getattr(config, 'video_scaler', 'lanczos')

        if config.background_mode == 'smart':
            # Handle smart person-aware cropping
            filters, last_label = self._build_smart_background_filters(
                video_path, start_time, end_time, width=SHORT_WIDTH, height=SHORT_HEIGHT
            )
        else:
            filters, last_label = build_background_filters(
                config.background_mode, scaler=scaler
            )

        if burn_captions and transcript_segments:
            caption_offset = 0.0 if captions_are_clip_relative else start_time
            if self.write_ass(transcript_segments, ass_path,
                              time_offset=caption_offset, clip_duration=duration,
                              keywords=keywords, style=caption_style):
                # Use relative path from cwd for FFmpeg subtitles filter to avoid
                # Windows drive-letter parsing issues in filter strings.
                try:
                    rel_ass = Path(ass_path).relative_to(Path.cwd())
                except ValueError:
                    rel_ass = Path(ass_path)
                # Render subtitles in a full-chroma format, then convert once at
                # the end. Burning big bold captions directly onto yuv420p
                # blends the glyph edges into half-resolution chroma planes,
                # which is what makes caption edges look muddy or fringed --
                # especially the coloured emphasis words. Compositing at 4:4:4
                # and subsampling afterwards keeps the outlines crisp.
                filters.append(
                    f"[{last_label}]format=yuv444p,"
                    f"subtitles='{self._escape_filter_path(rel_ass)}'"
                    f":alpha=1[captioned]"
                )
                last_label = 'captioned'
            else:
                logger.warning("No caption lines fell inside the clip; skipping captions")

        # Final conversion to the delivery format. Tagging the colour space
        # matters: untagged H.264 is interpreted as BT.601 by some players and
        # BT.709 by others, so an untagged file looks washed out or oversaturated
        # depending on where it is watched.
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
                # 128k was audibly lossy on music beds. 192k at 48kHz is what
                # YouTube itself recommends for stereo; the extra bytes are
                # negligible next to the video track.
                '-c:a', 'aac', '-b:a', config.audio_bitrate,
                '-ar', str(config.audio_sample_rate),
                '-ac', '2',
            ]
        else:
            logger.warning("Source has no audio stream; rendering a silent clip")
            cmd += ['-an']

        cmd += [
            '-c:v', 'libx264',
            '-preset', config.video_preset,
            '-crf', str(config.video_crf),
            '-pix_fmt', 'yuv420p',
            # High profile + level 4.2 covers 1080x1920@60. The default
            # (unconstrained) is fine for YouTube but some mobile players and
            # editors refuse odd combinations, so state it.
            '-profile:v', 'high',
            '-level', '4.2',
            # Film tuning turns OFF the psychovisual over-smoothing x264 applies
            # by default. Without this, fine detail (skin texture, fabric) is
            # deliberately blurred to save bits, which reads as "low quality"
            # even at a good CRF.
            '-tune', 'film',
            # Two consecutive B-frames + CABAC: better compression at equal
            # quality, so the CRF budget buys more detail.
            '-bf', '2', '-g', '60',
            # Colour metadata (see the filter comment above).
            '-colorspace', 'bt709',
            '-color_primaries', 'bt709',
            '-color_trc', 'bt709',
            '-color_range', 'tv',
            '-movflags', '+faststart',
        ]

        # Framerate: previously hard-coded to '-r 30', which silently threw away
        # half the frames of any 50/60fps source and made motion look choppy.
        # Now the source rate is preserved, capped at video_max_fps so an
        # unusual high-rate source cannot explode the encode time.
        target_fps = self._choose_fps(str(src))
        if target_fps:
            cmd += ['-r', f"{target_fps:g}"]

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
        filters, last_label = build_background_filters(
            config.background_mode, scaler=getattr(config, 'video_scaler', 'lanczos')
        )
        filters.append(f"[{last_label}]format=yuv420p[vout]")
        cmd = [
            self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-i', input_path,
            '-filter_complex', ';'.join(filters),
            '-map', '[vout]', '-map', '0:a?',
            '-c:v', 'libx264', '-preset', config.video_preset,
            '-crf', str(config.video_crf), '-profile:v', 'high', '-tune', 'film',
            '-colorspace', 'bt709', '-color_primaries', 'bt709',
            '-color_trc', 'bt709', '-color_range', 'tv',
            '-c:a', 'copy', '-y', output_path,
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
            # 4:4:4 while compositing glyphs, 4:2:0 for delivery -- see the
            # note in create_short_from_segment().
            '-vf', (f"format=yuv444p,"
                    f"subtitles='{self._escape_filter_path(ass_path)}':alpha=1,"
                    f"format=yuv420p"),
            '-c:v', 'libx264', '-preset', config.video_preset,
            '-crf', str(config.video_crf), '-profile:v', 'high', '-tune', 'film',
            '-colorspace', 'bt709', '-color_primaries', 'bt709',
            '-color_trc', 'bt709', '-color_range', 'tv',
            '-c:a', 'copy', '-y', output_path,
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
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', config.audio_bitrate,
            '-ar', str(config.audio_sample_rate), '-y', output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            logger.error("Audio normalisation failed: %s", (result.stderr or '')[-500:])
            return False
        return Path(output_path).exists()

    def _build_smart_background_filters(self, video_path: str, start_time: float,
                                        end_time: float,
                                        width: int = SHORT_WIDTH,
                                        height: int = SHORT_HEIGHT):
        """Person-aware framing, delegating to :mod:`smart_crop`.

        The previous implementation lived here and never worked: it normalised
        source-pixel face coordinates by the *output* size, averaged the
        positions of different people into a point where nobody stood, and its
        multi-person branch computed regions and then discarded them with
        ``return build_background_filters('crop', ...)``. See smart_crop.py for
        the full analysis.

        Falls back to the configured non-smart backdrop whenever detection
        finds nobody, so this stage can never fail a render.
        """
        fallback = getattr(config, 'smart_fallback_mode', 'crop')
        scaler = getattr(config, 'video_scaler', 'lanczos')
        try:
            result = smart_crop.build_smart_filters(
                video_path, start_time, end_time, width=width, height=height,
                zoom=getattr(config, 'smart_zoom', 1.0),
                headroom=getattr(config, 'smart_headroom', 0.55),
                samples=getattr(config, 'smart_samples', 9),
                max_people=getattr(config, 'smart_max_people', 4),
                min_presence=getattr(config, 'smart_min_presence', 0.34),
                scaler=scaler,
            )
        except Exception as exc:
            logger.warning("Smart framing failed (%s); using '%s'", exc, fallback)
            return build_background_filters(fallback, width, height, scaler=scaler)

        if not result:
            logger.info("Smart framing found no people; using '%s'", fallback)
            return build_background_filters(fallback, width, height, scaler=scaler)

        filters, label, _count = result
        return list(filters), label
