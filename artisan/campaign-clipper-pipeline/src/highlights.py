"""Automatic Whisper setup -> payoff scoring for source clip windows.

One transcript is created per source and cached. Candidate windows are then
ranked unattended, so a 100-clip batch never becomes a 100-item approval queue.
When a candidate contains a payoff and nearby setup speech, its actual render
window is shifted backward to include the setup, not merely scored against it.

If faster-whisper is unavailable or fails, the caller keeps its visual/audio
score and renders normally.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List

from .config import config
from .utils import ensure_dir, file_fingerprint, setup_logger

logger = setup_logger(__name__)

_PAYOFF = re.compile(r'\\b(wait|watch|look|no way|oh my|finally|actually|literally|unbelievable|insane|crazy|wow|did he|did she|i can.t believe|there it is|here we go|let.s go|got him|that was|plot twist|victory|win|won|clutch)\\b', re.I)
_SETUP = re.compile(r'\\b(because|so|but|if|when|then|first|next|about to|watch this|listen|the plan|going to|gonna|trying to|challenge|unless|before)\\b', re.I)
_REACTION = re.compile(r'\\b(yes|yeah|no|wow|oh|damn|holy|wait|what|bro|unbelievable|crazy|insane|let.s go)\\b', re.I)
_MODEL = None
_MODEL_KEY = None


def _b(name, default):
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _i(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _load_model():
    global _MODEL, _MODEL_KEY
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.info('HIGHLIGHT_WHISPER_UNAVAILABLE install faster-whisper; using visual score')
        return None
    key = '|'.join([os.getenv('WHISPER_MODEL', 'small'),
                    os.getenv('WHISPER_DEVICE', 'cpu'),
                    os.getenv('WHISPER_COMPUTE_TYPE', 'int8')])
    if _MODEL_KEY == key:
        return _MODEL
    try:
        model, device, compute = key.split('|')
        _MODEL = WhisperModel(model, device=device, compute_type=compute)
        _MODEL_KEY = key
        logger.info('HIGHLIGHT_WHISPER_READY model=%s device=%s compute=%s', model, device, compute)
    except Exception as exc:
        _MODEL = None
        _MODEL_KEY = key
        logger.warning('HIGHLIGHT_WHISPER_LOAD_FAILED error=%s', str(exc)[:180])
    return _MODEL


def _cache_path(source):
    identity = source.get('fingerprint') or file_fingerprint(source['local_path'])
    return ensure_dir(config.data_dir / 'transcripts') / f'{identity}.json'


def _extract_audio(video, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = os.getenv('MILO_FFMPEG') or 'ffmpeg'
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', video, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', str(out)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        return result.returncode == 0 and out.exists() and out.stat().st_size > 1024
    except Exception as exc:
        logger.warning('HIGHLIGHT_AUDIO_FAILED error=%s', str(exc)[:160])
        return False


def _collect(iterator):
    result = []
    for segment in iterator:
        text = (getattr(segment, 'text', '') or '').strip()
        if text:
            result.append({'text': text, 'start': float(segment.start), 'end': float(segment.end), 'confidence': float(getattr(segment, 'avg_logprob', 0.0) or 0.0)})
    return result


def transcript_for(source) -> List[Dict]:
    if not _b('HIGHLIGHT_WHISPER', True):
        return []
    path = _cache_path(source)
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding='utf-8'))
            if payload.get('source') == source.get('fingerprint'):
                return payload.get('segments') or []
    except Exception:
        pass
    model = _load_model()
    if model is None:
        return []
    wav = config.temp_dir / 'highlight_audio' / f'{source.get("fingerprint")}.wav'
    if not wav.exists() and not _extract_audio(source['local_path'], wav):
        return []
    try:
        language = os.getenv('WHISPER_LANGUAGE', '').strip() or None
        segments, _info = model.transcribe(str(wav), beam_size=_i('WHISPER_BEAM_SIZE', 1), vad_filter=True, language=language, condition_on_previous_text=False)
        result = _collect(segments)
        path.write_text(json.dumps({'source': source.get('fingerprint'), 'segments': result}, indent=2), encoding='utf-8')
        logger.info('HIGHLIGHT_TRANSCRIPT_READY file=%s segments=%d', source.get('filename'), len(result))
        return result
    except Exception as exc:
        logger.warning('HIGHLIGHT_TRANSCRIPT_FAILED file=%s error=%s', source.get('filename'), str(exc)[:180])
        return []


def _overlap(segments, start, end):
    return [s for s in segments if float(s['end']) > start and float(s['start']) < end]


def _score_text(text, required):
    low = text.lower()
    words = re.findall(r"[\\w']+", low)
    if not words:
        return 0.0, 0.0, 0.0, 0.0
    setup = min(1.0, len(_SETUP.findall(low)) / 2.0)
    payoff = min(1.0, (len(_PAYOFF.findall(low)) + 0.5 * len(_REACTION.findall(low))) / 3.0)
    relevance = min(1.0, sum(1 for token in required if token.lower() in low) / max(1, len(required)))
    density = min(1.0, len(words) / 70.0)
    return setup, payoff, relevance, density


def score_window(window: Dict, source: Dict, spec, db=None) -> Dict:
    segments = transcript_for(source)
    original_start, original_end = float(window['start']), float(window['end'])
    inside = _overlap(segments, original_start, original_end)
    before = _overlap(segments, max(0.0, original_start - _f('HIGHLIGHT_SETUP_SECONDS', 8.0)), original_start)
    required = list(spec.render.must_appear_in_video) + list(spec.caption.all_required())
    text = ' '.join(s['text'] for s in inside)
    before_text = ' '.join(s['text'] for s in before)
    in_setup, payoff, relevance, density = _score_text(text, required)
    prior_setup, _, _, prior_density = _score_text(before_text, required)
    setup_score = max(in_setup, prior_setup, prior_density * 0.7)

    # If the payoff is in this window and setup speech is nearby, pull the
    # window back so the rendered clip contains both. Keep the requested length
    # and legal bounds; the scene/audio candidate already chose the event.
    payoff_segments = [s for s in inside if _PAYOFF.search(s['text']) or _REACTION.search(s['text'])]
    clip_start, clip_end = original_start, original_end
    if payoff_segments and before:
        first_setup = before[0]
        candidate_start = max(0.0, float(first_setup['start']))
        if original_end - candidate_start <= original_end - original_start + _f('HIGHLIGHT_SETUP_MAX_EXTRA', 0.0):
            clip_start = candidate_start
            clip_end = clip_start + (original_end - original_start)

    transcript_quality = density * 0.65 + min(1.0, len(inside) / 3.0) * 0.35
    window.update({'start': round(clip_start, 3), 'end': round(clip_end, 3),
                   'transcript': text, 'setup_score': round(setup_score, 4),
                   'payoff_score': round(payoff, 4), 'relevance_score': round(relevance, 4),
                   'transcript_quality': round(transcript_quality, 4),
                   'story_start': round(clip_start, 3), 'story_end': round(clip_end, 3)})
    window['score'] = round(0.24 * float(window.get('motion_score', window.get('score', 0.0))) +
                        0.14 * float(window.get('audio_score', 0.0)) +
                        0.25 * setup_score + 0.30 * payoff + 0.07 * relevance, 4)
    return window


def rank_windows(windows, source, spec, db=None):
    if not windows:
        return []
    transcript_for(source)
    return sorted([score_window(window, source, spec, db=db) for window in windows],
                  key=lambda item: item.get('score', 0.0), reverse=True)
