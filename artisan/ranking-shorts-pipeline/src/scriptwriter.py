"""Clip titles, voice-over lines, and the published metadata.

Two writers behind one interface. With a Gemini key the model writes from what
the vetting pass actually observed (the transcript, the source title, the
motion profile); without one, a template writer derives something serviceable
from the source title. The template path exists so the pipeline is never
blocked on an API key or a quota wall - a run that stops because the copy could
not be written has wasted the download and the render.

Rank 5 gets no voice-over by default. It is the hook clip and it is the
shortest; a line over it steps on the opening beat rather than adding to it.
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from .config import config
from .utils import ensure_dir, safe_slug, setup_logger

logger = setup_logger(__name__)

_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for',
    'with', 'my', 'his', 'her', 'this', 'that', 'it', 'is', 'was', 'you',
    'shorts', 'short', 'video', 'funny', 'best', 'top', 'viral', 'part',
}

# Fallback emoji per topic when the model does not supply one. The overlay
# renders this beside the clip title, so it doubles as the topic's accent.
TOPIC_EMOJI = {
    'fishing_moments': '🎣',
    'animal_moments': '🐾',
    'sports_moments': '⚽',
    'wildlife_moments': '🦁',
    'satisfying_processes': '✨',
    'street_moments': '🎪',
    'gta6_countdown': '🚗',
}


def _clean_words(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z']+", text or '')
    return [w for w in words if w.lower() not in _STOPWORDS and len(w) > 2]


def _template_title(clip: Dict) -> str:
    """Two or three punchy words from the source title."""
    words = _clean_words(clip.get('title') or '')
    if not words:
        words = _clean_words(clip.get('transcript') or '')
    if not words:
        return f"MOMENT {clip.get('rank', '')}".strip()
    return ' '.join(words[:3]).upper()[:28]


def _template_line(clip: Dict) -> str:
    subject = (_clean_words(clip.get('title') or '') or ['this'])[0].lower()
    rank = int(clip.get('rank') or 0)
    if rank == 1:
        return f'Ok that {subject} one is insane.'
    if rank == 2:
        return f'No way that {subject} actually happened.'
        # deliberately understated: the clip carries the joke, not the line
    if rank == 3:
        return 'This one gets me every time.'
    return 'Yeah that is going to hurt.'


def _model_copy(topic_cfg: Dict, clips: List[Dict]) -> Optional[Dict]:
    """Ask the model for titles and lines in one call. None on any failure."""
    if not config.script_api_key:
        return None
    try:
        from google import genai
    except ImportError:
        logger.debug('google-genai not installed; using template writer')
        return None

    described = [
        {
            'rank': clip.get('rank'),
            'source_title': clip.get('title'),
            'transcript': (clip.get('transcript') or '')[:300],
            'seconds': clip.get('clip_duration'),
        }
        for clip in clips
    ]
    prompt = (
        'You write copy for short-form ranking videos. Topic: '
        f"{topic_cfg.get('name')}. For each clip below return a TITLE of 2-4 "
        'words in caps naming what happens (like "MAN OVERBOARD" or "CATCH '
        'AND RELEASE"), a VO line of at most 12 words: one deadpan, funny '
        'reaction to the clip, and ONE relevant emoji to show beside the '
        'title (no flags, no complex sequences). Do not describe the clip, '
        'react to it. No hashtags.\n'
        'Return JSON only: {"clips":[{"rank":5,"title":"...","line":"...",'
        '"emoji":"..."}]}\n'
        f'Clips: {json.dumps(described, ensure_ascii=False)}'
    )
    try:
        client = genai.Client(api_key=config.script_api_key)
        response = client.models.generate_content(
            model=config.script_model, contents=prompt)
        raw = (response.text or '').strip()
        # Models wrap JSON in fences often enough that stripping them is not
        # optional; a fenced payload is not parseable and would silently drop
        # the whole run back to templates.
        raw = re.sub(r'^```(?:json)?|```$', '', raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning('model copywriting failed (%s); using templates', exc)
        return None

    out: Dict[int, Dict] = {}
    for item in (data.get('clips') or []):
        try:
            out[int(item['rank'])] = {
                'title': str(item.get('title') or '').strip().upper()[:28],
                'line': str(item.get('line') or '').strip()[:140],
                'emoji': str(item.get('emoji') or '').strip(),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def write_copy(topic_cfg: Dict, clips: List[Dict]) -> Dict:
    """Fill in ``title`` and ``vo_line`` on each clip; return video metadata."""
    model_copy = _model_copy(topic_cfg, clips)
    total = len(clips)

    for clip in clips:
        rank = int(clip.get('rank') or 0)
        supplied = (model_copy or {}).get(rank) or {}
        clip['title'] = supplied.get('title') or _template_title(clip)
        line = supplied.get('line') or _template_line(clip)
        skip = (config.vo_skip_first and rank == total) \
            or clip.get('has_speech')
        clip['vo_line'] = '' if skip else line
        emoji = supplied.get('emoji') or TOPIC_EMOJI.get(topic_cfg.get('name')
                                                         or '', '')
        if emoji:
            clip['title'] = f"{clip['title']} {emoji}".strip()

    video_title = str(topic_cfg.get('title') or 'TOP {n}').replace(
        '{n}', str(total))
    tags = list(topic_cfg.get('tags') or [])
    description_lines = [f'{video_title}', '']
    for clip in clips:
        description_lines.append(f"#{clip['rank']} {clip['title']}")
    description_lines += [
        '',
        'Clips are credited to their original creators:',
    ]
    for clip in clips:
        credit = clip.get('uploader') or 'original creator'
        description_lines.append(f"  #{clip['rank']} - {credit} "
                                 f"({clip.get('url', '')})")
    description_lines += ['', '#Shorts ' + ' '.join(f'#{t}' for t in tags[:8])]

    return {
        'video_title': video_title,
        'upload_title': f'{video_title} #Shorts',
        'description': '\n'.join(description_lines),
        'tags': tags,
    }


# ---------------------------------------------------------------------------
# Voice-over generation
# ---------------------------------------------------------------------------
def generate_voiceover(clips: List[Dict], build_slug: str) -> None:
    """Generate one VO file per clip that has a line, via the forked TTS.

    Run as a subprocess rather than imported: the TTS owns its own config,
    threads and key-rotation state, and it should be replaceable (or run on
    another box) without this module knowing.
    """
    if not config.vo_enabled:
        logger.info('voice-over disabled; skipping')
        return

    lines = [{'id': f"R{clip['rank']}", 'text': clip['vo_line']}
             for clip in clips if (clip.get('vo_line') or '').strip()]
    if not lines:
        logger.info('no voice-over lines to generate')
        return

    out_dir = ensure_dir(config.vo_dir / safe_slug(build_slug))
    lines_path = out_dir / 'lines.json'
    lines_path.write_text(json.dumps({'lines': lines}, indent=2,
                                     ensure_ascii=False), encoding='utf-8')

    cmd = [sys.executable, '-m', 'ranking_tts.ranking_tts',
           '--lines', str(lines_path), '--out-dir', str(out_dir),
           '--voice', config.tts_voice, '--format', config.tts_format]
    logger.info('generating %d voice-over line(s)', len(lines))
    try:
        proc = subprocess.run(cmd, cwd=str(config.project_root), timeout=1800)
        if proc.returncode != 0:
            logger.warning('TTS exited %s; continuing with whatever exists '
                           '(a clip without a line still renders)',
                           proc.returncode)
    except subprocess.TimeoutExpired:
        logger.error('TTS timed out')
    except FileNotFoundError:
        logger.error('could not launch the TTS module')

    for clip in clips:
        for ext in (config.tts_format, 'mp3', 'wav'):
            candidate = out_dir / f"R{clip['rank']}.{ext}"
            if candidate.exists() and candidate.stat().st_size > 1024:
                clip['vo_path'] = str(candidate)
                break


# ---------------------------------------------------------------------------
# Sound effects
# ---------------------------------------------------------------------------
def attach_sfx(clips: List[Dict]) -> None:
    """Match a sound effect per clip and place it on the action.

    Placed at the detected action offset, not at the clip start: a slip sound
    that fires half a second before the slip reads as a mistake.
    """
    for clip in clips:
        haystack = ' '.join([
            (clip.get('title') or ''),
            (clip.get('transcript') or '')[:120],
        ]).lower()
        chosen = None
        for keyword in config.sfx_map:
            if keyword == 'swoosh':
                continue  # reserved for transitions and the hook
            if keyword in haystack:
                chosen = config.sfx_path(keyword)
                if chosen:
                    break
        cues = []
        if chosen:
            cues.append({'path': str(chosen),
                         'at': float(clip.get('action_offset') or 0.0),
                         'gain': float(config.get('sfx_gain', 0.9))})
        # The hook clip also gets the swoosh under its zoom-in.
        if clip.get('hook_candidate'):
            swoosh = config.sfx_path('swoosh')
            if swoosh:
                cues.append({'path': str(swoosh), 'at': 0.0,
                             'gain': float(config.get('swoosh_gain', 0.5))})
        clip['sfx'] = cues
