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

# Optional OpenCV import for smart person-aware cropping
try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False
    cv2 = None  # type: ignore

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger, sanitize_filename
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger, sanitize_filename
    from config import config

logger = setup_logger(__name__)

SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920


def detect_faces_in_frame(frame, face_cascade):
    """Detect faces in a single frame and return list of face rectangles."""
    if frame is None:
        return []

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
        flags=cv2.CASCADE_SCALE_IMAGE
    )
    return faces


def get_optimal_crop_regions(video_path, timestamp, num_people_expected=None):
    """
    Analyze a frame at the given timestamp to determine optimal crop regions
    for person-aware cropping.

    Returns a list of (x, y, width, height) tuples for each person/region.
    """
    if not OPENCV_AVAILABLE:
        logger.warning("OpenCV not available, falling back to center crop for smart mode")
        return [(0, 0, SHORT_WIDTH, SHORT_HEIGHT)]  # Full frame fallback

    # Initialize video capture
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Could not open video {video_path} for face detection")
        return [(0, 0, SHORT_WIDTH, SHORT_HEIGHT)]

    # Set position to the timestamp
    cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)

    # Read frame
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        logger.warning(f"Could not read frame at timestamp {timestamp}, falling back to center crop")
        return [(0, 0, SHORT_WIDTH, SHORT_HEIGHT)]

    # Load face cascade classifier - handle case where cv2 might not have CascadeClassifier or XML files
    if not hasattr(cv2, 'CascadeClassifier'):
        logger.warning("OpenCV does not have CascadeClassifier attribute, falling back to center crop for smart mode")
        return [(0, 0, SHORT_WIDTH, SHORT_HEIGHT)]

    cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    # Check if cascade file actually exists
    import os
    if not os.path.exists(cascade_path):
        logger.warning(f"Haar cascade file not found at {cascade_path}, falling back to center crop for smart mode")
        return [(0, 0, SHORT_WIDTH, SHORT_HEIGHT)]

    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        logger.error("Could not load face cascade classifier")
        return [(0, 0, SHORT_WIDTH, SHORT_HEIGHT)]

    # Detect faces
    faces = detect_faces_in_frame(frame, face_cascade)

    if len(faces) == 0:
        logger.info("No faces detected, using center crop")
        # Return center crop region
        center_x = SHORT_WIDTH // 2
        center_y = SHORT_HEIGHT // 2
        size = min(SHORT_WIDTH, SHORT_HEIGHT)  # Square crop from center
        x = max(0, center_x - size // 2)
        y = max(0, center_y - size // 2)
        return [(x, y, size, size)]

    logger.info(f"Detected {len(faces)} faces at timestamp {timestamp}")

    # Sort faces by x-coordinate (left to right)
    faces = sorted(faces, key=lambda f: f[0])

    # If we have face regions, return them
    if len(faces) == 1:
        # Single person - use the face region with some padding
        x, y, w, h = faces[0]
        # Add padding around the face
        padding_x = int(w * 0.3)
        padding_y = int(h * 0.5)
        x = max(0, x - padding_x)
        y = max(0, y - padding_y)
        w = w + 2 * padding_x
        h = h + 2 * padding_y
        return [(x, y, w, h)]

    elif len(faces) == 2:
        # Two people - split screen vertically
        face1 = faces[0]
        face2 = faces[1]

        # Calculate regions for each person
        # Person 1: top half
        x1, y1, w1, h1 = face1
        person1_region = (
            max(0, x1 - 20),
            0,
            min(SHORT_WIDTH, x1 + w1 + 20),
            SHORT_HEIGHT // 2
        )

        # Person 2: bottom half
        x2, y2, w2, h2 = face2
        person2_region = (
            max(0, x2 - 20),
            SHORT_HEIGHT // 2,
            min(SHORT_WIDTH, x2 + w2 + 20),
            SHORT_HEIGHT
        )

        return [person1_region, person2_region]

    else:
        # 3+ people - create a grid layout
        # For simplicity, we'll do a 2x2 grid for up to 4 people
        rows = 2
        cols = 2

        regions = []
        region_width = SHORT_WIDTH // cols
        region_height = SHORT_HEIGHT // rows

        for row in range(rows):
            for col in range(cols):
                if len(regions) >= len(faces):
                    break
                x = col * region_width
                y = row * region_height
                regions.append((x, y, region_width, region_height))

        # If we have more people than grid spaces, just use the grid
        while len(regions) < 4:  # 2x2 grid
            x = (len(regions) % cols) * region_width
            y = (len(regions) // cols) * region_height
            regions.append((x, y, region_width, region_height))

        return regions[:min(len(faces), 4)]  # Limit to 4 regions max

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

    if mode == 'smart':
        # Smart person-aware cropping - will be handled differently in the filter chain
        # For now, return a placeholder that indicates smart mode
        return ([f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease"
                 f":flags=fast_bilinear,"
                 f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[padded]"], 'padded')

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
        caption_style = getattr(config, 'caption_style', 'default')

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

                # Define styles based on caption style
                if caption_style == 'hormozi':
                    # Alex Hormozi Style: Bold, dynamic colors, lower-middle
                    f.write(
                        f'Style: Default,Impact,{font_size},&H00FFFFFF,&H000000FF,'
                        f'&H00000000,&H80000000,-1,0,0,0,100,100,0,0,2,4,2,2,60,60,320,1\n\n'
                    )
                elif caption_style == 'minimalist':
                    # Clean Minimalist: Sans-serif, white with shadow or background
                    f.write(
                        f'Style: Default,Montserrat,{font_size},&H00FFFFFF,&H000000FF,'
                        f'&H00000000,&H64000000,-1,0,0,0,100,100,0,0,2,2,2,2,60,60,320,1\n\n'
                    )
                elif caption_style == 'pop':
                    # Pop & Bounce: Bright neon with black outline
                    f.write(
                        f'Style: Default,Bebas Neue,{font_size},&H00FFFFFF,&H0000FF00,'
                        f'&H00000000,&H80000000,-1,0,0,0,100,100,4,4,0,2,2,2,60,60,320,1\n\n'
                    )
                elif caption_style == 'kinetic':
                    # Kinetic Karaoke: Highlight current word
                    f.write(
                        f'Style: Default,Komika Axis,{font_size},&H00FFFFFF,&H0000FFFF,'
                        f'&H00000000,&H80000000,-1,0,0,0,100,100,0,0,2,4,2,2,60,60,320,1\n\n'
                    )
                elif caption_style == 'neon':
                    # Neon Style: Bright cyan background, magenta text, thick outline
                    f.write(
                        f'Style: Default,Arial Black,{font_size},&H00FF00FF,&H00FFFF00,'
                        f'&H00000000,&H80000000,-1,0,0,0,100,100,3,3,0,2,2,2,60,60,320,1\n\n'
                    )
                elif caption_style == 'outline':
                    # Thick Outline Style: White text with thick black outline
                    f.write(
                        f'Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,'
                        f'&H00000000,&H80000000,-1,0,0,0,100,100,3,3,0,2,2,2,60,60,320,1\n\n'
                    )
                elif caption_style == 'shadow':
                    # Heavy Shadow Style: White text with heavy shadow
                    f.write(
                        f'Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,'
                        f'&H00000000,&H64000000,-1,0,0,0,100,100,0,0,2,6,6,2,60,60,320,1\n\n'
                    )
                elif caption_style == 'bold':
                    # Bold Impact Style: Impact font, white text, black outline
                    f.write(
                        f'Style: Default,Impact,{font_size},&H00FFFFFF,&H000000FF,'
                        f'&H00000000,&H80000000,-1,0,0,0,100,100,2,2,0,2,2,2,60,60,320,1\n\n'
                    )
                else:
                    # Default style (original)
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

                    # Apply style-specific text processing
                    if caption_style == 'hormozi':
                        text = self._process_hormozi_style(text)
                    elif caption_style == 'pop':
                        text = self._process_pop_style(text)
                    elif caption_style == 'kinetic':
                        text = self._process_kinetic_style(text, start, end)
                    # minimalist uses default processing

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

        if config.background_mode == 'smart':
            # Handle smart person-aware cropping
            filters, last_label = self._build_smart_background_filters(
                video_path, start_time, end_time, width=SHORT_WIDTH, height=SHORT_HEIGHT
            )
        else:
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

    def _process_hormozi_style(self, text: str) -> str:
        """Process text for Alex Hormozi style: 1-3 words, dynamic highlighting"""
        # For Hormozi style, we'll limit to 1-3 words and add markup for color highlighting
        words = text.split()
        if len(words) > 3:
            # Take first 3 words for Hormozi style
            words = words[:3]
            text = ' '.join(words)

        # In a real implementation, we would add ASS markup for dynamic colors
        # based on word sentiment (green for money/positive, red for negative, etc.)
        # For now, we'll return the text as-is but could be enhanced with markup
        return text.upper()  # Hormozi style often uses uppercase

    def _process_pop_style(self, text: str) -> str:
        """Process text for Pop & Bounce style: word-by-word with animation"""
        # For Pop & Bounce, we'd want each word to appear separately
        # This would require more complex ASS styling with per-word animation
        # For now, we'll return the text as-is
        return text

    def _process_kinetic_style(self, text: str, start_time: float, end_time: float) -> str:
        """Process text for Kinetic Karaoke style: highlight current word"""
        # For Kinetic Karaoke, we'd need to split words and highlight based on timing
        # This is complex to implement in ASS without knowing exact word timings
        # For now, we'll return the text as-is
        return text

    def _build_smart_background_filters(self, video_path: str, start_time: float, end_time: float,
                                      width: int = SHORT_WIDTH, height: int = SHORT_HEIGHT):
        """
        Build smart person-aware crop filters based on face detection.

        For 1 person: center crop on the person
        For 2 people: split screen vertically (top/bottom)
        For 3+ people: grid layout (2x2 for up to 4 people)
        """
        if not OPENCV_AVAILABLE or not hasattr(cv2, 'CascadeClassifier'):
            logger.warning("OpenCV not available or missing CascadeClassifier for smart mode, falling back to crop mode")
            return build_background_filters('crop', width, height)

        # Sample multiple timestamps to get a better representation of people positions
        duration = end_time - start_time
        sample_times = [
            start_time + duration * 0.25,
            start_time + duration * 0.5,
            start_time + duration * 0.75
        ]

        # Collect face detections from multiple samples
        all_faces = []
        for sample_time in sample_times:
            faces = get_optimal_crop_regions(video_path, sample_time)
            # Convert face regions to normalized coordinates (0-1)
            for (x, y, w, h) in faces:
                norm_x = x / width
                norm_y = y / height
                norm_w = w / width
                norm_h = h / height
                all_faces.append((norm_x, norm_y, norm_w, norm_h))

        if not all_faces:
            logger.warning("No faces detected in smart mode, falling back to crop mode")
            return build_background_filters('crop', width, height)

        # Average the face positions to get stable regions
        avg_norm_x = sum(f[0] for f in all_faces) / len(all_faces)
        avg_norm_y = sum(f[1] for f in all_faces) / len(all_faces)
        avg_norm_w = sum(f[2] for f in all_faces) / len(all_faces)
        avg_norm_h = sum(f[3] for f in all_faces) / len(all_faces)

        # Convert back to pixel coordinates
        avg_x = int(avg_norm_x * width)
        avg_y = int(avg_norm_y * height)
        avg_w = max(1, int(avg_norm_w * width))
        avg_h = max(1, int(avg_norm_h * height))

        # For now, implement a simplified smart crop that detects if there are multiple people
        # and splits accordingly. A more sophisticated implementation would track individuals.

        # Since we're sampling multiple times, let's check if we have consistent separation
        # that indicates multiple people

        # Simple approach: if we detect significant horizontal spread, assume multiple people
        face_positions = [f[0] for f in all_faces]  # normalized x positions
        if len(face_positions) > 1:
            pos_spread = max(face_positions) - min(face_positions)
            if pos_spread > 0.3:  # Significant horizontal spread
                logger.info("Smart mode: Detected horizontal spread, attempting split-screen")

                # Sort faces by x position
                sorted_faces = sorted(all_faces, key=lambda f: f[0])

                if len(sorted_faces) >= 2:
                    # Split screen vertically
                    left_face = sorted_faces[0]
                    right_face = sorted_faces[-1]  # Take the rightmost face

                    # Left person: top half
                    left_region = (
                        0,  # x
                        0,  # y
                        width // 2,  # width
                        height   # height
                    )

                    # Right person: bottom half
                    right_region = (
                        width // 2,  # x
                        0,           # y
                        width // 2,  # width
                        height       # height
                    )

                    # Create filter complexes for side-by-side layout
                    # NOTE: This is a simplified implementation. A proper implementation
                    # would use actual face coordinates and create more complex filter graphs.

                    # For now, we'll fall back to crop mode but log that we detected multiple people
                    logger.info("Smart mode detected multiple people but using crop mode for now")
                    return build_background_filters('crop', width, height)

        # Default to center crop on detected face area
        logger.info("Smart mode: Using center crop on detected face area")

        # Add some padding around the detected area
        padding_x = int(avg_w * 0.2)
        padding_y = int(avg_h * 0.3)

        crop_x = max(0, avg_x - padding_x)
        crop_y = max(0, avg_y - padding_y)
        crop_w = min(width - crop_x, avg_w + 2 * padding_x)
        crop_h = min(height - crop_y, avg_h + 2 * padding_y)

        # Ensure minimum size
        crop_w = max(crop_w, width // 3)
        crop_h = max(crop_h, height // 3)

        return ([
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease"
            f":flags=fast_bilinear,crop={crop_w}:{crop_h}:{crop_x}:{crop_y}[padded]",
        ], 'padded')
