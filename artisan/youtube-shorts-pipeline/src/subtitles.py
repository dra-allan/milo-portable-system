"""Reuse YouTube's published subtitles instead of running Whisper.

WHY THIS EXISTS
---------------
Transcription was ~85% of every run: the logs show a 65-minute source taking
~50 minutes at 1.3-1.5x realtime, for a transcript that is only used to decide
*where* the interesting moments are. But most of the sources the pipeline pulls
from already ship a transcript -- either uploader-provided or YouTube's own ASR
-- and yt-dlp can fetch it as a ~200 KB text file in about a second.

So the fast path is: download the subtitle track, parse it, and skip Whisper
entirely. That turns the dominant stage of the pipeline from ~50 minutes into
~1 second whenever a track exists. Whisper remains the fallback for the sources
that have none.

Accuracy note: YouTube's ASR is the same class of model as Whisper `tiny`/`base`
(the discovery default), so for the purpose of *ranking* moments this is not a
downgrade. Word-level caption timings still come from the dedicated caption
pass on the selected clips, so burned captions are unaffected.

The parser handles both formats yt-dlp returns: WebVTT and SRT (json3/srv are
converted to vtt by yt-dlp when we ask for vtt).
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .utils import setup_logger
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger

logger = setup_logger(__name__)

# 00:01:02.500 or 00:01:02,500 or 01:02.500
_TS = r'(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})'
_CUE_RE = re.compile(rf'^\s*{_TS}\s*-->\s*{_TS}')
# WebVTT inline karaoke tags: <00:00:01.234><c>word</c>
_TAG_RE = re.compile(r'<[^>]*>')


def _seconds(hours, minutes, seconds, millis) -> float:
    return (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
            + int(str(millis).ljust(3, '0')) / 1000.0)


def parse_subtitle_file(path: str) -> Optional[List[Dict]]:
    """Parse a .vtt/.srt file into the pipeline's transcript segment shape.

    Returns ``[{'text','start','end','confidence','words'}, ...]`` in the
    source timeline, or None if nothing usable was found.

    ``words`` is left empty on purpose: subtitle cues carry no reliable
    per-word onsets, and inventing them would make captions drift. Callers
    that need word timings run the caption pass, which is what already
    happens.
    """
    p = Path(path)
    if not p.exists():
        return None

    try:
        raw = p.read_text(encoding='utf-8-sig', errors='replace')
    except Exception as exc:
        logger.warning("Could not read subtitle file %s: %s", p.name, exc)
        return None

    segments: List[Dict] = []
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        match = _CUE_RE.match(lines[i])
        if not match:
            i += 1
            continue

        g = match.groups()
        start = _seconds(g[0], g[1], g[2], g[3])
        end = _seconds(g[4], g[5], g[6], g[7])

        # Cue payload: every line until a blank line or the next timestamp.
        i += 1
        parts: List[str] = []
        while i < len(lines) and lines[i].strip() and not _CUE_RE.match(lines[i]):
            parts.append(lines[i])
            i += 1

        text = _TAG_RE.sub('', ' '.join(parts))
        text = re.sub(r'\s+', ' ', text).replace('&nbsp;', ' ').strip()
        # Undo the HTML entities yt-dlp leaves in ASR tracks.
        for entity, char in (('&amp;', '&'), ('&#39;', "'"), ('&quot;', '"'),
                             ('&gt;', '>'), ('&lt;', '<')):
            text = text.replace(entity, char)
        text = text.strip()

        if not text or end <= start:
            continue

        # YouTube's ASR tracks emit "rolling" cues where each cue repeats the
        # tail of the previous one. Left alone this triples the transcript and
        # wrecks keyword scoring, so drop an exact continuation.
        if segments:
            prev = segments[-1]
            if text == prev['text']:
                prev['end'] = max(prev['end'], end)
                continue
            if text.startswith(prev['text']) and len(prev['text']) > 8:
                text = text[len(prev['text']):].strip()
                if not text:
                    continue

        segments.append({
            'text': text,
            'start': start,
            'end': end,
            # Not from a model, so there is no logprob. 0.0 is the neutral
            # value the scorer already treats as "no confidence signal".
            'confidence': 0.0,
            'words': [],
        })

    if not segments:
        logger.warning("Subtitle file %s contained no usable cues", p.name)
        return None

    segments.sort(key=lambda s: s['start'])
    return _merge_short_cues(segments)


def _merge_short_cues(segments: List[Dict],
                      min_seconds: float = 2.0) -> List[Dict]:
    """Merge sub-2s cues into sentence-ish segments.

    ASR tracks emit a cue every 1-2 words. The highlight scorer expects
    Whisper-style segments (a clause or sentence each), and scoring per-word
    fragments makes every candidate look equally uninteresting. Merging until
    a cue reaches ``min_seconds`` or ends on sentence punctuation reproduces
    the granularity Whisper would have produced.
    """
    merged: List[Dict] = []
    for seg in segments:
        if not merged:
            merged.append(dict(seg))
            continue
        current = merged[-1]
        span = current['end'] - current['start']
        ends_sentence = current['text'][-1:] in '.!?'
        gap = seg['start'] - current['end']
        if span < min_seconds and not ends_sentence and gap < 2.0:
            current['text'] = f"{current['text']} {seg['text']}".strip()
            current['end'] = seg['end']
        else:
            merged.append(dict(seg))
    return merged


def find_subtitle_file(*candidates) -> Optional[Path]:
    """First existing subtitle sidecar among the given paths/globs.

    yt-dlp names subtitle files ``<basename>.<lang>.vtt``, and the language
    suffix varies ('en', 'en-orig', 'en-US'), so callers pass a directory plus
    a stem and this globs for whatever landed.
    """
    for candidate in candidates:
        if candidate is None:
            continue
        p = Path(candidate)
        if p.exists() and p.is_file():
            return p
    return None
