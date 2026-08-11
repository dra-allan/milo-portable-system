"""Clip vetting: is this clip actually usable, and where is the good bit?

This is the module that decides whether the finished video looks organic or
looks like a reupload. The reference workflow states the rule plainly: clips
must have **no commentary, no background music, ideally no on-screen text**.
Any of those surviving into the edit is what causes both the monetisation
problem and the "this is just someone else's video" problem.

So each check maps to one of those:

* commentary -> transcribe and measure words per second
* music bed  -> percussive energy + tempo confidence
* text/logos -> OCR, then **blur** rather than reject (a good clip with a
  caption is still a good clip; throwing it away costs more than masking it)
* dead clip  -> scene-change density, which also locates the action peak used
  to place the cut and the sound effect

Every heavy dependency is optional and every failure to import degrades to a
skipped check with a warning. A machine without tesseract should still produce
videos; it just will not auto-mask text.
"""

import math
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import config
from .utils import (ensure_dir, probe_media, run_ffmpeg, run_ffmpeg_capture,
                    setup_logger, which_ffmpeg)

logger = setup_logger(__name__)

_SCENE_RE = re.compile(r'pts_time:([0-9.]+)')


# ---------------------------------------------------------------------------
# Motion / action
# ---------------------------------------------------------------------------
def scene_profile(path: str, duration: float) -> Tuple[float, float]:
    """Return (motion_score, action_time).

    Scene-change density is a cheap proxy for "something happens here". The
    peak is where cuts cluster, which is where the slip/splash/catch is, and
    that is both where the clip should be centred and where the SFX belongs.
    """
    if duration <= 0:
        return 0.0, 0.0
    stderr = run_ffmpeg_capture([
        '-i', str(path), '-vf',
        "select='gt(scene,0.18)',metadata=print:file=-",
        '-an', '-f', 'null', '-',
    ])
    times = [float(m) for m in _SCENE_RE.findall(stderr)]
    if not times:
        return 0.0, duration / 2.0

    # Density per second, capped: a clip with a cut every frame is a montage,
    # not action, and should not outscore a real moment.
    score = min(1.0, len(times) / max(1.0, duration) / 2.0)

    # Densest one-second window is the action peak.
    best_time, best_count = times[0], 0
    for candidate in times:
        count = sum(1 for t in times if candidate <= t < candidate + 1.0)
        if count > best_count:
            best_count, best_time = count, candidate
    return score, best_time


# ---------------------------------------------------------------------------
# Audio: commentary and music
# ---------------------------------------------------------------------------
def extract_audio(path: str, out_wav: Path) -> Optional[Path]:
    ensure_dir(out_wav.parent)
    ok = run_ffmpeg(['-i', str(path), '-vn', '-ac', '1', '-ar', '16000',
                     '-c:a', 'pcm_s16le', str(out_wav)])
    return out_wav if ok and out_wav.exists() else None


def transcribe(wav_path: Path) -> Optional[Dict]:
    """Transcribe with faster-whisper. Returns {text, words, duration} or None.

    Used for two things: rejecting narrated clips, and giving the script writer
    something concrete to name the clip after.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.debug('faster-whisper not installed; commentary check skipped')
        return None
    try:
        model = WhisperModel('base', device='auto', compute_type='int8')
        segments, info = model.transcribe(str(wav_path), vad_filter=True)
        text_parts, words = [], 0
        for segment in segments:
            text_parts.append(segment.text.strip())
            words += len(segment.text.split())
        return {
            'text': ' '.join(text_parts).strip(),
            'words': words,
            'duration': float(getattr(info, 'duration', 0.0) or 0.0),
            'language': getattr(info, 'language', '') or '',
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning('transcription failed: %s', exc)
        return None


def music_confidence(wav_path: Path) -> Optional[float]:
    """0..1 confidence that the clip has a music bed.

    Two signals, because either alone is fooled: a steady tempo (percussive
    onsets landing on a grid) and a high percussive-to-total energy ratio.
    Ambient noise from a boat engine is loud but has no beat; a song has both.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        logger.debug('librosa not installed; music check skipped')
        return None
    try:
        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        if y.size < sr:
            return 0.0
        harmonic, percussive = librosa.effects.hpss(y)
        total = float(np.sum(y ** 2)) or 1e-9
        percussive_ratio = float(np.sum(percussive ** 2)) / total

        onset = librosa.onset.onset_strength(y=percussive, sr=sr)
        tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr)
        if len(beats) < 4:
            beat_regularity = 0.0
        else:
            intervals = np.diff(librosa.frames_to_time(beats, sr=sr))
            spread = float(np.std(intervals))
            mean = float(np.mean(intervals)) or 1e-9
            # Low variance between beats == a machine keeping time == music.
            beat_regularity = max(0.0, 1.0 - min(1.0, spread / mean * 2.0))
        return round(min(1.0, 0.55 * beat_regularity
                         + 0.45 * min(1.0, percussive_ratio * 2.5)), 3)
    except Exception as exc:  # noqa: BLE001
        logger.warning('music detection failed: %s', exc)
        return None


# ---------------------------------------------------------------------------
# On-screen text
# ---------------------------------------------------------------------------
def _fill_transform(src_w: int, src_h: int) -> Tuple[float, float, float]:
    """Return (scale, x_offset, y_offset) for the 9:16 fill in overlays.py.

    OCR boxes are in *source* pixels; the blur masks are applied to the
    *output* frame after the source has been scaled to 1080 wide and centred
    over a blurred bed. Without this mapping the masks land in the wrong place,
    which is worse than not masking at all.
    """
    if src_w <= 0 or src_h <= 0:
        return 1.0, 0.0, 0.0
    scale = config.width / float(src_w)
    scaled_h = src_h * scale
    return scale, 0.0, (config.height - scaled_h) / 2.0


def detect_text_boxes(path: str, duration: float, src_w: int,
                      src_h: int, samples: int = 4) -> Tuple[List[Dict], float]:
    """OCR a few frames and return (output-frame boxes, coverage fraction).

    Boxes are merged and padded: a caption OCRs as a dozen word-level boxes,
    and blurring each one separately leaves sharp gaps between the words where
    the text is still legible.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.debug('pytesseract/Pillow not installed; text check skipped')
        return [], 0.0

    frames_dir = ensure_dir(config.temp_dir / 'ocr')
    boxes: List[Dict] = []
    scale, x_off, y_off = _fill_transform(src_w, src_h)

    for index in range(samples):
        at = duration * (index + 1) / (samples + 1)
        frame = frames_dir / f'ocr_{index}.png'
        if not run_ffmpeg(['-ss', f'{at:.2f}', '-i', str(path),
                           '-frames:v', '1', str(frame)]):
            continue
        try:
            data = pytesseract.image_to_data(
                Image.open(frame), output_type=pytesseract.Output.DICT)
        except Exception as exc:  # noqa: BLE001
            logger.warning('OCR failed on frame %d: %s', index, exc)
            continue
        finally:
            frame.unlink(missing_ok=True)

        for i, text in enumerate(data.get('text') or []):
            if not (text or '').strip():
                continue
            try:
                conf = float(data['conf'][i])
            except (KeyError, TypeError, ValueError):
                conf = 0.0
            if conf < 55:
                continue
            boxes.append({
                'x': int(data['left'][i] * scale + x_off) - 8,
                'y': int(data['top'][i] * scale + y_off) - 8,
                'w': int(data['width'][i] * scale) + 16,
                'h': int(data['height'][i] * scale) + 16,
            })

    merged = _merge_boxes(boxes)
    frame_area = float(config.width * config.height) or 1.0
    coverage = sum(b['w'] * b['h'] for b in merged) / frame_area
    return merged, round(coverage, 4)


def _merge_boxes(boxes: List[Dict], gap: int = 24) -> List[Dict]:
    """Union overlapping/nearby boxes so a caption becomes one mask."""
    remaining = [dict(b) for b in boxes if b['w'] > 0 and b['h'] > 0]
    merged: List[Dict] = []
    while remaining:
        current = remaining.pop()
        changed = True
        while changed:
            changed = False
            for other in list(remaining):
                if (current['x'] - gap < other['x'] + other['w']
                        and other['x'] - gap < current['x'] + current['w']
                        and current['y'] - gap < other['y'] + other['h']
                        and other['y'] - gap < current['y'] + current['h']):
                    x1 = min(current['x'], other['x'])
                    y1 = min(current['y'], other['y'])
                    x2 = max(current['x'] + current['w'],
                             other['x'] + other['w'])
                    y2 = max(current['y'] + current['h'],
                             other['y'] + other['h'])
                    current = {'x': x1, 'y': y1, 'w': x2 - x1, 'h': y2 - y1}
                    remaining.remove(other)
                    changed = True
        merged.append(current)
    return merged


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------
def perceptual_hash(path: str, duration: float) -> Optional[str]:
    """dHash of a mid-clip frame.

    URL history alone does not stop duplicates: the same moment is reuploaded
    across accounts and platforms constantly, and publishing it twice is the
    fastest way to look like a bot farm.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    frame = ensure_dir(config.temp_dir / 'phash') / 'frame.png'
    at = max(0.1, duration / 2.0)
    if not run_ffmpeg(['-ss', f'{at:.2f}', '-i', str(path), '-frames:v', '1',
                       '-vf', 'scale=9:8', str(frame)]):
        return None
    try:
        image = Image.open(frame).convert('L')
        pixels = list(image.getdata())
        bits = []
        for row in range(8):
            for col in range(8):
                left = pixels[row * 9 + col]
                right = pixels[row * 9 + col + 1]
                bits.append('1' if left > right else '0')
        return f'{int("".join(bits), 2):016x}'
    except Exception as exc:  # noqa: BLE001
        logger.warning('phash failed: %s', exc)
        return None
    finally:
        frame.unlink(missing_ok=True)


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count('1')
    except (TypeError, ValueError):
        return 64


def is_duplicate(phash: Optional[str], known: List[str]) -> bool:
    if not phash:
        return False
    limit = int(config.get('phash_distance', 6))
    return any(hamming(phash, other) <= limit for other in known if other)


# ---------------------------------------------------------------------------
# The vetting pass
# ---------------------------------------------------------------------------
def vet(candidate: Dict, known_hashes: List[str]) -> Dict:
    """Vet a downloaded candidate.

    Returns the candidate enriched with ``ok``, ``reason`` and everything the
    ranker, script writer and assembler need: the chosen window, the action
    time, the blur boxes, the transcript and the scores.
    """
    path = candidate.get('local_path')
    if not path or not Path(path).exists():
        candidate.update(ok=False, reason='missing_file')
        return candidate

    media = probe_media(path)
    duration = media['duration']
    min_clip = float(config.get('min_clip_seconds', 2.5))
    max_clip = float(config.get('max_clip_seconds', 9.0))

    if duration < min_clip:
        candidate.update(ok=False, reason='too_short')
        return candidate

    motion, action_at = scene_profile(path, duration)
    candidate['motion_score'] = motion
    candidate['action_at'] = action_at
    if motion < float(config.get('min_motion_score', 0.10)):
        # Cut density is a proxy for "something happens here", but a real
        # moment short is one unbroken shot - the fail/catch/close-call
        # happens mid-frame with no cut before or after. Zero cuts on a
        # short-form source is not a montage; it is the moment itself.
        zero_cut_short = (
            motion == 0.0
            and candidate.get('source_kind') == 'youtube_shorts'
            and duration <= float(config.get('shorts_zero_cut_seconds', 120))
        )
        if not zero_cut_short:
            candidate.update(ok=False, reason='no_motion')
            return candidate

    # Window: centre on the action, clamped to the clip. A clip longer than
    # max_clip is trimmed rather than dropped - the good two seconds of a
    # 40-second upload is still the good two seconds.
    window = min(max_clip, duration)
    start = max(0.0, min(action_at - window * 0.35, duration - window))
    candidate['clip_start'] = round(start, 3)
    candidate['clip_duration'] = round(window, 3)
    candidate['action_offset'] = round(max(0.0, action_at - start), 3)

    phash = perceptual_hash(path, duration)
    candidate['phash'] = phash
    if is_duplicate(phash, known_hashes):
        candidate.update(ok=False, reason='duplicate')
        return candidate

    wav = extract_audio(path, config.temp_dir / 'vet' /
                        f"{candidate.get('source_id') or 'clip'}.wav")
    transcript = None
    if wav:
        transcript = transcribe(wav)
        if transcript:
            candidate['transcript'] = transcript['text']
            wps = (transcript['words'] / duration) if duration else 0.0
            candidate['words_per_second'] = round(wps, 3)
            if wps > float(config.get('max_words_per_second', 0.45)):
                if (candidate.get('allow_commentary')
                        or config.get('allow_commentary')):
                    # Narrated raw footage (a bear close-call with a cameraman
                    # talking over it): keep it, but mark the speech so the
                    # script writer does not lay TTS voice-over on top of the
                    # clip's own narration.
                    candidate['has_speech'] = True
                else:
                    candidate.update(ok=False, reason='has_commentary')
                    wav.unlink(missing_ok=True)
                    return candidate

        music = music_confidence(wav)
        if music is not None:
            candidate['music_confidence'] = music
            if music > float(config.get('max_music_confidence', 0.55)):
                candidate.update(ok=False, reason='has_music')
                wav.unlink(missing_ok=True)
                return candidate
        wav.unlink(missing_ok=True)

    boxes, coverage = detect_text_boxes(path, duration, media['width'],
                                        media['height'])
    candidate['text_coverage'] = coverage
    if coverage > float(config.get('max_text_coverage', 0.18)):
        # Past this point the frame is more overlay than footage; blurring it
        # would just be a smear.
        candidate.update(ok=False, reason='too_much_text')
        return candidate
    candidate['text_boxes'] = boxes if config.get('blur_detected_text', True) \
        else []

    candidate.update(ok=True, reason='')
    logger.info("vetted OK: %s (motion=%.2f music=%s wps=%s text=%.1f%%)",
                (candidate.get('title') or '')[:48], motion,
                candidate.get('music_confidence'),
                candidate.get('words_per_second'), coverage * 100)
    return candidate
