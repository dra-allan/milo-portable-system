"""Automatic Whisper setup -> payoff scoring for source clip windows.

One transcript is created per source and cached. Candidate windows are then
ranked unattended, so a 100-clip batch never becomes a 100-item approval queue.
When a candidate contains a payoff and nearby setup speech, its actual render
window is shifted backward to include the setup, not merely scored against it.

If faster-whisper is unavailable or fails, the caller keeps its visual/audio
score and renders normally.

THE REGRESSION FIXED HERE (2026-08-19)
--------------------------------------
Every lexicon pattern in this file was written as::

    re.compile(r'\\b(wait|watch|look|...)\\b', re.I)

In a raw string ``\\b`` is a literal backslash followed by ``b`` -- not a word
boundary. So those patterns only matched text containing an actual backslash,
which transcript text never does. Worse, ``_score_text`` tokenised with
``re.findall(r"[\\w']+", low)``, which matched nothing, hit the ``if not words:
return 0.0, 0.0, 0.0, 0.0`` guard, and returned zeros for **every window ever
scored**.

Consequence: ``setup_score``, ``payoff_score`` and ``relevance_score`` have
always been 0.0, and the composite score reduced to::

    0.24 * motion + 0.14 * audio

55% of the weight was dead. Window selection has therefore been "which seconds
had the most scene cuts and the loudest audio", with no idea whether anything
interesting was being said. That is the single largest reason clips from this
lane open mid-sentence on nothing in particular.

The transcript cache shape is unchanged, so ``_CACHE_VERSION`` deliberately
stays at 2 -- there is no reason to re-transcribe every source to fix a scoring
bug.
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

_PAYOFF = re.compile(
    r"\b(wait|watch|look|no way|oh my|finally|actually|literally|unbelievable|"
    r"insane|crazy|wow|did he|did she|i can'?t believe|there it is|here we go|"
    r"let'?s go|got him|that was|plot twist|victory|win|won|clutch)\b", re.I)
_SETUP = re.compile(
    r"\b(because|so|but|if|when|then|first|next|about to|watch this|listen|"
    r"the plan|going to|gonna|trying to|challenge|unless|before)\b", re.I)
_REACTION = re.compile(
    r"\b(yes|yeah|no|wow|oh|damn|holy|wait|what|bro|unbelievable|crazy|"
    r"insane|let'?s go)\b", re.I)
# A question is the most reliable hook material there is, so it is scored
# explicitly rather than left to the generic setup lexicon. story_edit uses the
# same idea to actually restructure the clip.
_QUESTION = re.compile(
    r"\?|\b(are|is|was|were|do|does|did|can|could|will|would|should|have|has|"
    r"why|what|when|where|who|which|how)\b\s+\w+", re.I)
_WORD = re.compile(r"[\w']+")

_MODEL = None
_MODEL_KEY = None

# Bump when the stored segment shape changes so stale caches re-transcribe.
# v2: segments carry word-level timestamps for synced speech captions.
_CACHE_VERSION = 2


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
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', video,
           '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', str(out)]
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
            words = []
            for w in getattr(segment, 'words', None) or []:
                wtext = (getattr(w, 'word', '') or '').strip()
                wstart = getattr(w, 'start', None)
                wend = getattr(w, 'end', None)
                if wtext and wstart is not None and wend is not None:
                    words.append({'word': wtext, 'start': float(wstart),
                                  'end': float(wend)})
            result.append({'text': text, 'start': float(segment.start),
                           'end': float(segment.end),
                           'confidence': float(getattr(segment, 'avg_logprob', 0.0) or 0.0),
                           'words': words})
    return result


def transcript_for(source) -> List[Dict]:
    if not _b('HIGHLIGHT_WHISPER', True):
        return []
    path = _cache_path(source)
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding='utf-8'))
            if (payload.get('source') == source.get('fingerprint')
                    and payload.get('v') == _CACHE_VERSION):
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
        segments, _info = model.transcribe(
            str(wav), beam_size=_i('WHISPER_BEAM_SIZE', 1), vad_filter=True,
            language=language, condition_on_previous_text=False,
            word_timestamps=True)
        result = _collect(segments)
        path.write_text(json.dumps({'source': source.get('fingerprint'),
                                    'v': _CACHE_VERSION, 'segments': result},
                                   indent=2), encoding='utf-8')
        logger.info('HIGHLIGHT_TRANSCRIPT_READY file=%s segments=%d',
                    source.get('filename'), len(result))
        return result
    except Exception as exc:
        logger.warning('HIGHLIGHT_TRANSCRIPT_FAILED file=%s error=%s',
                       source.get('filename'), str(exc)[:180])
        return []


def _overlap(segments, start, end):
    return [s for s in segments if float(s['end']) > start and float(s['start']) < end]


def _score_text(text, required):
    """``(setup, payoff, relevance, density)`` in 0..1 for a block of speech.

    Every one of these returned 0.0 before the regex fix above, which is why
    they are worth reading closely rather than trusting.
    """
    low = (text or '').lower()
    words = _WORD.findall(low)
    if not words:
        return 0.0, 0.0, 0.0, 0.0
    setup = min(1.0, len(_SETUP.findall(low)) / 2.0)
    payoff = min(1.0, (len(_PAYOFF.findall(low))
                       + 0.5 * len(_REACTION.findall(low))) / 3.0)
    relevance = min(1.0, sum(1 for token in required if token.lower() in low)
                    / max(1, len(required))) if required else 0.0
    density = min(1.0, len(words) / 70.0)
    return setup, payoff, relevance, density


def question_score(text: str) -> float:
    """How much question-shaped hook material this speech contains (0..1).

    A window with a question in it can be restructured into a curiosity gap by
    :mod:`story_edit`; a window without one can only ever be a straight cut. So
    this is a real selection signal, not a stylistic preference.
    """
    hits = len(_QUESTION.findall(text or ''))
    return min(1.0, hits / 2.0)


def score_window(window: Dict, source: Dict, spec, db=None) -> Dict:
    segments = transcript_for(source)
    original_start, original_end = float(window['start']), float(window['end'])
    inside = _overlap(segments, original_start, original_end)
    before = _overlap(segments, max(0.0, original_start - _f('HIGHLIGHT_SETUP_SECONDS', 8.0)),
                      original_start)
    required = list(spec.render.must_appear_in_video) + list(spec.caption.all_required())
    text = ' '.join(s['text'] for s in inside)
    before_text = ' '.join(s['text'] for s in before)
    in_setup, payoff, relevance, density = _score_text(text, required)
    prior_setup, _, _, prior_density = _score_text(before_text, required)
    setup_score = max(in_setup, prior_setup, prior_density * 0.7)
    question = question_score(text)

    # If the payoff is in this window and setup speech is nearby, pull the
    # window back so the rendered clip contains both. Keep the requested length
    # and legal bounds; the scene/audio candidate already chose the event.
    payoff_segments = [s for s in inside
                       if _PAYOFF.search(s['text']) or _REACTION.search(s['text'])]
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
                   'payoff_score': round(payoff, 4),
                   'relevance_score': round(relevance, 4),
                   'question_score': round(question, 4),
                   'transcript_quality': round(transcript_quality, 4),
                   'story_start': round(clip_start, 3),
                   'story_end': round(clip_end, 3)})
    # Weights: motion and audio say "something is happening"; setup and payoff
    # say "it is a complete moment"; question says "it can be restructured into
    # a curiosity gap". The text terms are only meaningful now that they are no
    # longer permanently zero.
    window['score'] = round(
        0.22 * float(window.get('motion_score', window.get('score', 0.0)))
        + 0.12 * float(window.get('audio_score', 0.0))
        + 0.23 * setup_score
        + 0.28 * payoff
        + 0.05 * relevance
        + 0.10 * question, 4)
    return window


def rank_windows(windows, source, spec, db=None):
    if not windows:
        return []
    transcript_for(source)
    return sorted([score_window(window, source, spec, db=db) for window in windows],
                  key=lambda item: item.get('score', 0.0), reverse=True)
