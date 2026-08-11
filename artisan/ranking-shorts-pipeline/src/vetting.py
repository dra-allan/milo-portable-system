"""Fast clip vetting for ranking Shorts.

Fast mode keeps the cheap safety checks and defers expensive Whisper, librosa,
OCR, and scene-density analysis. Set RANKING_FAST_MODE=false to restore the
strict pass.
"""
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from .config import config
from .utils import ensure_dir, probe_media, run_ffmpeg, run_ffmpeg_capture, setup_logger, which_ffmpeg
logger = setup_logger(__name__)
_SCENE_RE = re.compile(r'pts_time:([0-9.]+)')

def scene_profile(path: str, duration: float) -> Tuple[float, float]:
    if duration <= 0: return 0.0, 0.0
    stderr = run_ffmpeg_capture(['-i', str(path), '-vf', "select='gt(scene,0.18)',metadata=print:file=-", '-an', '-f', 'null', '-'])
    times = [float(x) for x in _SCENE_RE.findall(stderr)]
    if not times: return 0.0, duration / 2.0
    score = min(1.0, len(times) / max(1.0, duration) / 2.0)
    best = max(times, key=lambda t: sum(1 for x in times if t <= x < t + 1.0))
    return score, best

def audible_reason(path: str) -> Optional[str]:
    if not probe_media(path).get('has_audio'): return 'no_audio'
    stderr = run_ffmpeg_capture(['-i', str(path), '-vn', '-af', 'volumedetect', '-f', 'null', '-'])
    values = {}
    for line in stderr.splitlines():
        for label in ('max_volume', 'mean_volume'):
            token = label + ':'
            if token in line:
                try: values[label] = float(line.split(token, 1)[1].replace('dB', '').strip())
                except (ValueError, IndexError): pass
    if values.get('max_volume', 0) < float(config.get('min_audio_max_db', -35)) or values.get('mean_volume', 0) < float(config.get('min_audio_mean_db', -45)):
        return 'silent_audio'
    return None

def extract_audio(path: str, out_wav: Path) -> Optional[Path]:
    ensure_dir(out_wav.parent)
    ok = run_ffmpeg(['-i', str(path), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(out_wav)])
    return out_wav if ok and out_wav.exists() else None

def transcribe(wav_path: Path) -> Optional[Dict]:
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel('base', device='auto', compute_type='int8')
        segments, info = model.transcribe(str(wav_path), vad_filter=True, beam_size=1, word_timestamps=False)
        text, words = [], 0
        for segment in segments:
            part = segment.text.strip(); text.append(part); words += len(part.split())
        return {'text': ' '.join(text).strip(), 'words': words, 'duration': float(getattr(info, 'duration', 0.0) or 0.0), 'language': getattr(info, 'language', '') or ''}
    except Exception as exc:
        logger.warning('transcription failed: %s', exc); return None

def music_confidence(wav_path: Path) -> Optional[float]:
    try:
        import librosa, numpy as np
        y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
        if y.size < sr: return 0.0
        _, percussive = librosa.effects.hpss(y); total = float(np.sum(y ** 2)) or 1e-9
        onset = librosa.onset.onset_strength(y=percussive, sr=sr); _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr)
        if len(beats) < 4: return 0.0
        intervals = np.diff(librosa.frames_to_time(beats, sr=sr)); mean = float(np.mean(intervals)) or 1e-9
        regularity = max(0.0, 1.0 - min(1.0, float(np.std(intervals)) / mean * 2.0))
        return round(min(1.0, .55 * regularity + .45 * min(1.0, float(np.sum(percussive ** 2)) / total * 2.5)), 3)
    except Exception:
        return None

def detect_text_boxes(path: str, duration: float, src_w: int, src_h: int, samples: int = 4) -> Tuple[List[Dict], float]:
    if not getattr(config, 'vet_ocr', True): return [], 0.0
    try:
        import pytesseract
        from PIL import Image
    except ImportError: return [], 0.0
    boxes = []; frames_dir = ensure_dir(config.temp_dir / 'ocr')
    scale = config.width / float(src_w or config.width); yoff = (config.height - (src_h or config.height) * scale) / 2.0
    for index in range(samples):
        at = duration * (index + 1) / (samples + 1); frame = frames_dir / f'ocr_{index}.png'
        if not run_ffmpeg(['-ss', f'{at:.2f}', '-i', str(path), '-frames:v', '1', str(frame)]): continue
        try:
            data = pytesseract.image_to_data(Image.open(frame), output_type=pytesseract.Output.DICT)
            for i, text in enumerate(data.get('text') or []):
                if not text.strip(): continue
                try: conf = float(data['conf'][i])
                except (KeyError, TypeError, ValueError): conf = 0
                if conf >= 55: boxes.append({'x': int(data['left'][i] * scale) - 8, 'y': int(data['top'][i] * scale + yoff) - 8, 'w': int(data['width'][i] * scale) + 16, 'h': int(data['height'][i] * scale) + 16})
        except Exception: pass
        finally: frame.unlink(missing_ok=True)
    return boxes, 0.0

def perceptual_hash(path: str, duration: float) -> Optional[str]:
    try:
        from PIL import Image
        frame = ensure_dir(config.temp_dir / 'phash') / 'frame.png'
        if not run_ffmpeg(['-ss', f'{max(.1, duration / 2):.2f}', '-i', str(path), '-frames:v', '1', '-vf', 'scale=9:8', str(frame)]): return None
        image = Image.open(frame).convert('L'); pixels = list(image.getdata()); bits = []
        for row in range(8):
            for col in range(8): bits.append('1' if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else '0')
        frame.unlink(missing_ok=True); return f'{int("".join(bits), 2):016x}'
    except Exception: return None

def hamming(a: str, b: str) -> int:
    try: return bin(int(a, 16) ^ int(b, 16)).count('1')
    except (TypeError, ValueError): return 64

def is_duplicate(phash: Optional[str], known: List[str]) -> bool:
    return bool(phash) and any(hamming(phash, other) <= int(config.get('phash_distance', 6)) for other in known if other)

def vet(candidate: Dict, known_hashes: List[str]) -> Dict:
    path = candidate.get('local_path')
    if not path or not Path(path).exists(): candidate.update(ok=False, reason='missing_file'); return candidate
    media = probe_media(path); duration = media['duration']; min_clip = float(config.get('min_clip_seconds', 2.5)); max_clip = float(config.get('max_clip_seconds', 9.0))
    if duration < min_clip: candidate.update(ok=False, reason='too_short'); return candidate
    reason = audible_reason(path)
    if reason: candidate.update(ok=False, reason=reason); return candidate
    if config.fast_mode:
        motion, action_at = 1.0, duration / 2.0
    else:
        motion, action_at = scene_profile(path, duration)
        if motion < float(config.get('min_motion_score', .10)) and not (motion == 0 and candidate.get('source_kind') == 'youtube_shorts' and duration <= float(config.get('shorts_zero_cut_seconds', 120))):
            candidate.update(ok=False, reason='no_motion'); return candidate
    candidate['motion_score'] = motion; candidate['action_at'] = action_at
    window = min(max_clip, duration); start = max(0.0, min(action_at - window * .35, duration - window)); candidate['clip_start'] = round(start, 3); candidate['clip_duration'] = round(window, 3); candidate['action_offset'] = round(max(0.0, action_at - start), 3)
    phash = perceptual_hash(path, duration); candidate['phash'] = phash
    if is_duplicate(phash, known_hashes): candidate.update(ok=False, reason='duplicate'); return candidate
    if config.vet_transcribe or config.vet_music:
        wav = extract_audio(path, config.temp_dir / 'vet' / f"{candidate.get('source_id') or 'clip'}.wav")
        if wav and config.vet_transcribe:
            transcript = transcribe(wav)
            if transcript:
                candidate['transcript'] = transcript['text']; wps = transcript['words'] / duration if duration else 0; candidate['words_per_second'] = round(wps, 3)
                if wps > float(config.get('max_words_per_second', .45)) and not (candidate.get('allow_commentary') or config.get('allow_commentary')):
                    candidate.update(ok=False, reason='has_commentary'); wav.unlink(missing_ok=True); return candidate
        if wav and config.vet_music:
            music = music_confidence(wav); candidate['music_confidence'] = music
            if music is not None and music > float(config.get('max_music_confidence', .55)) and not (candidate.get('allow_music') or config.get('allow_music')):
                candidate.update(ok=False, reason='has_music'); wav.unlink(missing_ok=True); return candidate
        if wav: wav.unlink(missing_ok=True)
    boxes, coverage = detect_text_boxes(path, duration, media.get('width', config.width), media.get('height', config.height)); candidate['text_coverage'] = coverage
    if not config.fast_mode and coverage > float(config.get('max_text_coverage', .18)):
        candidate.update(ok=False, reason='too_much_text'); return candidate
    candidate['text_boxes'] = boxes if config.get('blur_detected_text', True) else []; candidate.update(ok=True, reason=''); logger.info('vetted OK: %s (fast=%s)', (candidate.get('title') or '')[:48], config.fast_mode); return candidate
