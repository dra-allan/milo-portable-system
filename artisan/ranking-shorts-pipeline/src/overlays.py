"""Overlay compositing: rank numbers, titles, and blur masks.

Text is rendered by Pillow into transparent PNG sheets and composited with the
``movie=`` filter, not drawn with drawtext. Three things to know before editing
this module.

**1. Text goes through Pillow, so nothing is escaped.**
Clip titles come from a language model or from scraped video metadata, and the
old drawtext ``text=`` path had *no* safe escaping recipe for ``'``, ``:``,
``,`` and ``%`` together - an apostrophe or colon aborted the graph and a
percent signed logged "Stray %", drew nothing, and still exited zero. A PNG has
no syntax to break out of: the hostile string ``THAT'S 100% WILD, BUDDY: PART
[2] 50%OFF`` renders as-is, and the regression test counts pixels. Pillow also
lets us colour only part of a line (the highlight keyword), draw true heavy
strokes outside the fill, and render colour emoji (Segoe UI Emoji) - none of
which drawtext does reliably.

**2. The one escape still left is the file path.**
``movie=`` reads a sheet from disk, so its path gets the same single-quote +
escaped-colon wrapping that drawtext's fontfile got (Windows drive letters).
Only paths, never text.

**3. A filter label can only be consumed once.** Anywhere the same frames are
needed twice (backdrop plus blurred patch) there has to be an explicit
``split``. The PNG sheets just become one extra input each; overlay consumes
the running video once and the sheet once, so no split is needed for them.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import config
from .utils import ensure_dir, safe_slug, setup_logger

logger = setup_logger(__name__)


def _quote(path) -> str:
    """Quote a filesystem path for a filter option value.

    FFmpeg's filtergraph parser strips single quotes before drawtext's own
    option splitter runs, so a bare ``C:/...`` still breaks on the colon.
    The verified form is single quotes AND a backslash-escaped colon:
    ``'C\\:/Windows/Fonts/impact.ttf'``.
    """
    return "'" + str(path).replace('\\', '/').replace(':', '\\:') + "'"


# ---------------------------------------------------------------------------
# Pillow text rendering
# ---------------------------------------------------------------------------
_EMOJI_FONT_CACHE = None


def normalize_text(text: str) -> str:
    """Collapse a title to one flat line; Pillow renders bare ``\\n`` as tofu."""
    return ' '.join(str(text or '').split())


def _emoji_font_path() -> Optional[str]:
    """Path to an emoji font, or None (cached; the answer never moves).

    The configured path wins; on machines without it we fall back to the
    vendored Noto colour-emoji font shipped in ``assets/fonts`` so every
    box renders the glyphs (monochrome minus RAQM, colour with it).
    """
    global _EMOJI_FONT_CACHE
    if _EMOJI_FONT_CACHE is not None:
        return _EMOJI_FONT_CACHE or None
    candidate = str(config.get('emoji_font',
                               'C:/Windows/Fonts/seguiemj.ttf'))
    if not Path(candidate).exists():
        fallback = (Path(__file__).resolve().parent.parent
                    / 'assets' / 'fonts' / 'NotoColorEmoji.ttf')
        if fallback.exists():
            logger.info('emoji font %s missing; using vendored fallback %s',
                        candidate, fallback)
            candidate = str(fallback)
    _EMOJI_FONT_CACHE = candidate if Path(candidate).exists() else ''
    if not _EMOJI_FONT_CACHE:
        logger.warning('emoji font not found (%s); emoji dropped', candidate)
    return _EMOJI_FONT_CACHE or None


# Emoji / dingbats / misc symbols; ZWJ groups multi-codepoint emoji runs.
_EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF'
    '\u2190-\u21FF\uFE0F\u200D]')


def _is_emoji(ch: str) -> bool:
    return bool(_EMOJI_RE.fullmatch(ch))


def _group_emoji(text: str) -> List[tuple]:
    """Split ``text`` into [(substring, is_emoji)] runs.

    The emoji font is a different face than the headline font, so emoji-bearing
    substrings get drawn with it and plain words with the headline face.
    """
    runs: List[tuple] = []
    current, current_is = '', None
    for ch in text or '':
        is_emoji = _is_emoji(ch)
        if current_is is None:
            current_is = is_emoji
        if is_emoji == current_is:
            current += ch
        else:
            runs.append((current, current_is))
            current, current_is = ch, is_emoji
    if current:
        runs.append((current, current_is))
    return runs


def _pillow_color(value: str, default: str = '#FFFFFF') -> str:
    """Map ``0xFFD700`` style config colours to Pillow's ``#FFD700``."""
    value = str(value or '').strip()
    if value.startswith('0x'):
        return '#' + value[2:]
    return value or default


def _pillow_font(font_path: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(font_path, size)


def _highlight_runs(text: str, keywords: List[str],
                    highlight: str) -> List[tuple]:
    """Split ``text`` into (substring, is_highlight) runs.

    Only the first occurrence of the first matching keyword is coloured; the
    rest of the line stays the base fill, matching the "1-2 highlight words"
    look without needing text measurement gymnastics.
    """
    low = (text or '').lower()
    for keyword in keywords:
        needle = (keyword or '').strip().lower()
        if not needle:
            continue
        index = low.find(needle)
        if index < 0:
            continue
        before, after = text[:index], text[index + len(keyword):]
        return [(before, False), (text[index:index + len(keyword)], True),
                (after, False)]
    return [(text or '', False)]


def _fit_size(text: str, max_width: int, start: int, font_path: str) -> int:
    """Shrink a font size until the line fits ``max_width`` (best effort)."""
    from PIL import ImageFont
    size = max(10, start)
    while size > 10:
        font = ImageFont.truetype(font_path, size)
        width = font.getlength(text)
        if width <= max_width:
            break
        size = int(size * 0.92)
    return size


def _draw_line(image, x: int, y: int, runs: List[tuple], text_path: str,
               emoji_path: Optional[str], size: int, fill: str,
               stroke_ratio: float, shadow: Sequence[int]):
    """Draw a multi-colour, multi-font line onto an RGBA sheet.

    Each plain-text run gets a heavy black stroke and a hard black drop shadow
    (two draws: shadow pass offset, then fill pass). Emoji runs are colour
    bitmap glyphs and are drawn without stroke, which the CBDT/COLR formats do
    not reliably support.
    """
    from PIL import ImageDraw
    import math as _math
    draw = ImageDraw.Draw(image)
    stroke = max(2, int(size * stroke_ratio))
    text_font = _pillow_font(text_path, size)
    emoji_font = _pillow_font(emoji_path, max(12, int(size * 0.9))) \
        if emoji_path else None
    shadow = (shadow[0], shadow[1]) if shadow else (0, 0)
    cursor = float(x)

    for item, is_emoji in runs:
        if not item:
            continue
        font = emoji_font if is_emoji else text_font
        draw_y = y
        if is_emoji and emoji_path:
            draw_y = y + int(size * 0.06)  # bitmap emoji centres differently
        if is_emoji:
            draw.text((cursor, draw_y), item, font=font)
        else:
            color = _pillow_color(fill)
            if shadow != (0, 0):
                draw.text((cursor + shadow[0], draw_y + shadow[1]), item,
                          font=font, fill='#000000',
                          stroke_width=stroke, stroke_fill='#000000')
            draw.text((cursor, draw_y), item, font=font, fill=color,
                      stroke_width=stroke, stroke_fill='#000000')
        cursor += font.getlength(item)


def _line_width(runs: List[tuple], text_path: str, emoji_path: Optional[str],
                size: int) -> float:
    total = 0.0
    text_font = _pillow_font(text_path, size)
    emoji_font = _pillow_font(emoji_path, max(12, int(size * 0.9))) \
        if emoji_path else None
    for item, is_emoji in runs:
        font = emoji_font if is_emoji and emoji_path else text_font
        if item:
            total += font.getlength(item)
    return total


def fill_chain(in_label: str, out_label: str) -> List[str]:
    """Fit a landscape clip into 9:16 by cropping to fill the frame.

    Scale to cover and centre-crop so there are no bars and no blurred bed.
    """
    w, h = config.width, config.height
    return [
        f'[{in_label}]scale={w}:{h}:force_original_aspect_ratio=increase,'
        f'crop={w}:{h},setsar=1[{out_label}]',
    ]


def clamp_box(box: Dict) -> Optional[Dict]:
    """Clamp/round one OCR box so crop will accept it.

    crop refuses a region that runs past the frame edge and wants even offsets
    and sizes. OCR boxes routinely land a pixel or two outside the frame, and
    an unclamped box fails the render rather than degrading.
    """
    x = max(0, int(box.get('x', 0)) // 2 * 2)
    y = max(0, int(box.get('y', 0)) // 2 * 2)
    w = min(int(box.get('w', 0)) // 2 * 2, config.width - x)
    h = min(int(box.get('h', 0)) // 2 * 2, config.height - y)
    if w < 8 or h < 8:
        return None
    return {'x': x, 'y': y, 'w': w, 'h': h}


def mask_chain(in_label: str, out_label: str,
               boxes: Sequence[Dict]) -> List[str]:
    """Blur regions carrying the source's own text or logos.

    Boxes are ``{x, y, w, h}`` in *output* frame coordinates (vetting maps them
    through the 9:16 fill transform). One crop+blur+overlay per box, rather
    than a single global blur, so the rest of the picture stays sharp.
    """
    usable = [b for b in (clamp_box(box) for box in boxes) if b]
    if not usable:
        return [f'[{in_label}]null[{out_label}]']

    chains: List[str] = []
    src = in_label
    for idx, box in enumerate(usable):
        last = idx == len(usable) - 1
        tag = f'mb{idx}'
        dst = out_label if last else f'mk{idx}'
        chains.append(f'[{src}]split=2[{tag}a][{tag}b]')
        chains.append(
            f"[{tag}a]crop={box['w']}:{box['h']}:{box['x']}:{box['y']},"
            f'boxblur=luma_radius=18:luma_power=2[{tag}p]')
        chains.append(f"[{tag}b][{tag}p]overlay={box['x']}:{box['y']}[{dst}]")
        src = dst
    return chains


def text_chain(in_label: str, out_label: str, rank: int, clip_title: str,
               video_title: str, clips_total: int,
               work_dir: Optional[Path] = None,
               show_video_title: bool = True,
               leaderboard: Optional[List[Dict]] = None) -> List[str]:
    """Header + a persistent ranked side-list, as a Pillow/movie overlay chain.

    Layout (list, not countdown): a centered header up top ("TOP N" then the
    niche body, the first highlight keyword in colour), then a left column of
    rank numbers that stays on screen the whole clip - one saturated colour per
    rank, heavy black stroke, hard drop shadow. The row matching the playing
    clip additionally shows its title + emoji beside the number; the other rows
    show just the number. Since every clip is rendered independently with its
    own ``rank``, that reveal/disappear state machine is implicit.

    Each element is rendered to its own transparent PNG under ``work_dir`` and
    loaded with ``movie=``, so no extra ``-i`` inputs are needed and the
    assembler's input indexing is untouched.

    ``work_dir`` defaults to a per-rank directory derived from the video title,
    which is unique within a build - two clips in the same build must not share
    a sheet or the last one written wins for both.
    """
    from PIL import Image

    font = config.resolve_font()
    emoji_font = _emoji_font_path()
    work_dir = Path(work_dir) if work_dir else (
        config.temp_dir / 'text' / f'{safe_slug(video_title)}_r{rank}')
    ensure_dir(work_dir)

    w, h = config.width, config.height
    fill = str(config.get('rank_fill', 'white'))
    highlight = str(config.get('highlight_color', '0xFFD700'))
    stroke_ratio = float(config.get('stroke_ratio', 0.07))
    shadow = (int(config.get('shadow_x', 6)), int(config.get('shadow_y', 6)))

    title_size = int(config.get('video_title_fontsize', 84))
    title_y = int(config.get('video_title_y', 140))
    body_size = int(config.get('video_title_body_fontsize', 54))

    list_x = int(config.get('list_x', 84))
    list_y = int(config.get('list_y', 560))
    row_h = int(config.get('list_row_h', 92))
    rank_size = int(config.get('list_rank_size', 46))
    label_size = int(config.get('list_label_size', 34))
    keywords = [str(k) for k in (config.get('highlight_keywords') or [])]

    sheets: List[Path] = []

    # -- header ----------------------------------------------------------
    if show_video_title:
        header = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        head = str(clips_total)
        top_runs = _group_emoji(normalize_text(f'TOP {head}'))
        top_width = _line_width(top_runs, font, emoji_font, title_size)
        _draw_line(header, (w - top_width) / 2, title_y, top_runs,
                   font, emoji_font, title_size, fill, stroke_ratio, shadow)

        body = _strip_leading_count(video_title, head).upper()
        if body:
            body_runs_raw = _highlight_runs(body, keywords, highlight)
            body_runs = []
            for piece, is_hl in body_runs_raw:
                grouped = _group_emoji(normalize_text(piece))
                for text_part, is_emoji in grouped:
                    body_runs.append(
                        (text_part, is_emoji,
                         highlight if is_hl else fill))
            size = min(body_size, _fit_size(body, int(w * 0.92), body_size,
                                            font))
            body_width = _line_width(
                [(t, e) for t, e, _ in body_runs], font, emoji_font, size)
            _draw_colored_line(header, (w - body_width) / 2,
                               title_y + int(title_size * 1.18), body_runs,
                               font, emoji_font, size, stroke_ratio, shadow)
        header_path = work_dir / 'header.png'
        header.save(header_path)
        sheets.append(header_path)

    # -- persistent rank numbers ------------------------------------------
    rows = list(leaderboard or [{'rank': rank, 'title': clip_title}])
    rows = sorted(rows, key=lambda r: int(r.get('rank') or 0))
    list_sheet = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    for i, row in enumerate(rows):
        r = int(row.get('rank') or 0)
        row_y = list_y + i * row_h
        num_runs = _group_emoji(normalize_text(str(r)))
        num_color = config.rank_color(r)
        _draw_line(list_sheet, list_x, row_y, num_runs, font, emoji_font,
                   rank_size, num_color, stroke_ratio, shadow)
    list_path = work_dir / 'list.png'
    list_sheet.save(list_path)
    sheets.append(list_path)

    # -- active description (only the playing row) -------------------------
    active_row = next((row for row in rows if int(row.get('rank') or 0) == rank),
                      None)
    active_text = normalize_text((active_row or {}).get('title') or clip_title)
    if active_text:
        active = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        max_width = max(120, w - list_x - rank_size - 90)
        desc_size = _fit_size(active_text, max_width, label_size, font)
        desc_runs = _group_emoji(active_text)
        desc_width = _line_width(desc_runs, font, emoji_font, desc_size)
        index = next(i for i, row in enumerate(rows)
                     if int(row.get('rank') or 0) == rank)
        row_y = list_y + index * row_h
        desc_x = list_x + rank_size + 24
        desc_y = row_y + max(0, (rank_size - desc_size) // 2)
        _draw_line(active, desc_x, desc_y, desc_runs, font, emoji_font,
                   desc_size, fill, stroke_ratio, shadow)
        active_path = work_dir / 'active.png'
        active.save(active_path)
        sheets.append(active_path)

    # -- chain ------------------------------------------------------------
    chains: List[str] = []
    src = in_label
    for i, sheet in enumerate(sheets):
        tag = f'mv{i}'
        dst = out_label if i == len(sheets) - 1 else f'ovl{i}'
        chains.append(f"movie={_quote(str(sheet))}[{tag}]")
        chains.append(f'[{src}][{tag}]overlay=0:0:format=auto[{dst}]')
        src = dst
    return chains


def _draw_colored_line(image, x: float, y: int,
                       runs: List[tuple], text_path: str,
                       emoji_path: Optional[str], size: int,
                       stroke_ratio: float, shadow: Sequence[int]):
    """Like :func:`_draw_line` but ``runs`` carry their own colour.

    ``runs`` is [(substring, is_emoji, color)]; the plain-colour path cannot
    express a highlight keyword mid-line, which is the whole point of this one.
    """
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    stroke = max(2, int(size * stroke_ratio))
    text_font = _pillow_font(text_path, size)
    emoji_font = _pillow_font(emoji_path, max(12, int(size * 0.9))) \
        if emoji_path else None
    shadow = (shadow[0], shadow[1]) if shadow else (0, 0)
    cursor = float(x)

    for item, is_emoji, color in runs:
        if not item:
            continue
        font = emoji_font if is_emoji and emoji_path else text_font
        draw_y = y + (int(size * 0.06) if is_emoji else 0)
        if is_emoji:
            draw.text((cursor, draw_y), item, font=font)
        else:
            if shadow != (0, 0):
                draw.text((cursor + shadow[0], draw_y + shadow[1]), item,
                          font=font, fill='#000000',
                          stroke_width=stroke, stroke_fill='#000000')
            draw.text((cursor, draw_y), item, font=font,
                      fill=_pillow_color(color),
                      stroke_width=stroke, stroke_fill='#000000')
        cursor += font.getlength(item)


def _strip_leading_count(video_title: str, head: str) -> str:
    """Drop a leading "TOP 5" from the configured title.

    It is already drawn as the two accented elements above; repeating it eats
    a third of the frame.
    """
    body = str(video_title or '').strip()
    if body.upper().startswith('TOP '):
        body = body[4:].lstrip()
        if body.startswith(head):
            body = body[len(head):].lstrip()
    return body


def hook_zoom_chain(in_label: str, out_label: str) -> List[str]:
    """Punch-in on the opening clip, easing back to 1.0.

    zoompan runs with ``d=1`` so it emits one output frame per input frame and
    ``on`` counts real frames; with the default ``d`` it holds and stretches
    the clip instead. ``s`` and ``fps`` must be pinned or zoompan resets the
    frame size to its own default and the stitch stage rejects the clip.
    """
    if not config.get('hook_zoom', True):
        return [f'[{in_label}]null[{out_label}]']
    amount = float(config.get('hook_zoom_amount', 1.18))
    frames = int(config.get('hook_zoom_frames', 24))
    delta = amount - 1.0
    expr = f'if(lte(on,{frames}),{amount}-{delta}*on/{frames},1)'
    return [
        f"[{in_label}]zoompan=z='{expr}':d=1:"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f's={config.width}x{config.height}:fps={config.fps}[{out_label}]'
    ]
