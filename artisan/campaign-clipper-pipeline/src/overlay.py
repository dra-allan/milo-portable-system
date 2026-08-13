"""On-screen text and logo compositing.

Text goes through Pillow, never drawtext
----------------------------------------
Every campaign that requires "your own text" is asking for a string that came
from a model or from a human, and those strings contain apostrophes, colons,
commas and percent signs. ``drawtext`` has no escaping recipe that survives all
of those together: a percent sign logs "Stray %", draws nothing, and still
exits zero. The pipeline would then report a successful render of a clip with
no text on it, for a campaign whose payout depends on the text being there.

A transparent PNG has no syntax to break out of. The hostile string
``THAT'S 100% WILD, BUDDY: PART [2] 50%OFF`` renders as typed. Pillow also gives
real heavy strokes outside the fill, per-run colour for a highlight phrase, and
colour emoji, none of which drawtext does reliably.

The one escape still needed is the file path handed to ``movie=`` - single
quotes plus a backslash-escaped colon, for Windows drive letters. Paths only,
never text.

Why there is a logo *detector* here
-----------------------------------
"ADD LOGO IF NOT ALREADY ON CLIP" is a real, common requirement, and campaign
content folders genuinely ship a mix of branded and unbranded clips. Stamping a
second logo on an already-branded clip is exactly the sloppy output these
campaigns reject. The same detector is reused by the validator to prove the
stamp actually landed on the finished file.
"""

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .config import config
from .utils import ensure_dir, quote_filter_path, setup_logger

logger = setup_logger(__name__)

_EMOJI_RANGES = (
    '[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF'
    '\u2190-\u21FF\uFE0F\u200D]')


def _emoji_re():
    import re
    return re.compile(_EMOJI_RANGES)


def normalize_text(text: str) -> str:
    """Collapse to a single logical line; Pillow renders bare newlines as tofu."""
    return ' '.join(str(text or '').split())


def _group_emoji(text: str) -> List[Tuple[str, bool]]:
    """Split into [(substring, is_emoji)] runs; the two faces differ."""
    pattern = _emoji_re()
    runs: List[Tuple[str, bool]] = []
    current, current_is = '', None
    for ch in text or '':
        is_emoji = bool(pattern.fullmatch(ch))
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


def _font(path: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


def _wrap(text: str, font_path: str, size: int, max_width: int,
          max_lines: int) -> List[str]:
    """Greedy word wrap measured in real glyph widths.

    Character-count wrapping is wrong for the condensed bold faces used here;
    ``WWWWW`` and ``iiiii`` are the same length and nowhere near the same width.
    """
    font = _font(font_path, size)
    words = normalize_text(text).split()
    lines: List[str] = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines or ['']


def _fit(text: str, font_path: str, size: int, max_width: int,
         max_lines: int) -> Tuple[int, List[str]]:
    """Shrink until the text fits in ``max_lines`` of ``max_width``.

    Truncating instead would be a compliance bug, not a cosmetic one: several
    campaigns require a specific phrase to appear in the video, and a phrase cut
    off at the frame edge has not appeared.
    """
    while size > 22:
        lines = _wrap(text, font_path, size, max_width, max_lines + 1)
        if len(lines) <= max_lines:
            return size, lines
        size = int(size * 0.9)
    return size, _wrap(text, font_path, size, max_width, max_lines)


def _highlight_runs(text: str, phrase: str) -> List[Tuple[str, bool]]:
    """Split a line so ``phrase`` can be coloured differently.

    Used for the required brand phrase. Colouring the phrase the campaign
    demands makes it obviously present to a human reviewer, which is who
    actually approves these submissions.
    """
    if not phrase:
        return [(text, False)]
    low, needle = text.lower(), phrase.lower()
    index = low.find(needle)
    if index < 0:
        return [(text, False)]
    return [(text[:index], False),
            (text[index:index + len(phrase)], True),
            (text[index + len(phrase):], False)]


def text_sheet(text: str, out_path: Path, highlight: str = '',
               y_ratio: Optional[float] = None,
               size: Optional[int] = None) -> Optional[Path]:
    """Render ``text`` to a transparent 1080x1920 PNG. None when text is empty."""
    text = normalize_text(text)
    if not text:
        return None
    from PIL import Image, ImageDraw

    font_path = config.resolve_font()
    emoji_path = config.resolve_emoji_font()
    width, height = config.width, config.height
    margin = int(width * config.text_side_margin)
    max_width = width - margin * 2
    base_size = size or config.text_size
    base_size, lines = _fit(text, font_path, base_size, max_width,
                            config.text_max_lines)

    sheet = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    stroke = max(2, int(base_size * config.text_stroke_ratio))
    shadow_x, shadow_y = config.text_shadow
    text_font = _font(font_path, base_size)
    emoji_font = (_font(emoji_path, max(12, int(base_size * 0.9)))
                  if emoji_path else None)
    line_height = int(base_size * 1.22)
    top = int(height * (y_ratio if y_ratio is not None
                        else config.text_y_ratio))

    for index, line in enumerate(lines):
        runs: List[Tuple[str, bool, str]] = []
        for piece, is_high in _highlight_runs(line, highlight):
            for part, is_emoji in _group_emoji(piece):
                runs.append((part, is_emoji,
                             config.text_highlight if is_high
                             else config.text_fill))
        total = 0.0
        for part, is_emoji, _ in runs:
            font = emoji_font if (is_emoji and emoji_font) else text_font
            total += font.getlength(part)
        cursor = (width - total) / 2
        base_y = top + index * line_height
        for part, is_emoji, colour in runs:
            if not part:
                continue
            font = emoji_font if (is_emoji and emoji_font) else text_font
            draw_y = base_y + (int(base_size * 0.06) if is_emoji else 0)
            if is_emoji and emoji_font:
                # Colour bitmap glyph formats do not support strokes.
                draw.text((cursor, draw_y), part, font=font)
            else:
                if (shadow_x, shadow_y) != (0, 0):
                    draw.text((cursor + shadow_x, draw_y + shadow_y), part,
                              font=font, fill='#000000',
                              stroke_width=stroke, stroke_fill='#000000')
                draw.text((cursor, draw_y), part, font=font, fill=colour,
                          stroke_width=stroke, stroke_fill='#000000')
            cursor += font.getlength(part)

    ensure_dir(Path(out_path).parent)
    sheet.save(out_path)
    return Path(out_path)


def sheet_ink(path) -> int:
    """Count non-transparent pixels on a sheet.

    This is the regression guard that matters. The historical silent failure in
    this family of pipelines is a text stage that produces *nothing* and exits
    zero, so the validator asserts ink rather than trusting that the renderer
    was asked to draw.
    """
    try:
        from PIL import Image
        with Image.open(path) as image:
            alpha = image.convert('RGBA').getchannel('A')
            return sum(1 for value in alpha.getdata() if value > 8)
    except Exception as exc:
        logger.warning('SHEET_INK_FAILED path=%s error=%s', path, exc)
        return 0


# -- filter chains ------------------------------------------------------
def fill_chain(in_label: str, out_label: str) -> List[str]:
    """Fit any aspect ratio into 9:16 over a blurred copy of itself.

    Centre-cropping to fill is the obvious alternative and it is wrong for
    campaign footage: these are gameplay and stream clips where the subject is
    routinely near an edge or in a HUD corner, and a centre crop throws away
    the exact thing the campaign is paying for. Several campaigns also require
    gameplay to stay clearly visible throughout, which a crop cannot guarantee.
    The blurred bed additionally leaves clean space top and bottom for the
    required text.
    """
    w, h = config.width, config.height
    return [
        f'[{in_label}]scale={w}:-2:force_original_aspect_ratio=decrease,'
        f'setsar=1[fgv]',
        f'[{in_label}]scale={w}:{h}:force_original_aspect_ratio=increase,'
        f'crop={w}:{h},boxblur=luma_radius=40:luma_power=2,setsar=1[bgv]',
        f'[bgv][fgv]overlay=(W-w)/2:(H-h)/2:shortest=1[{out_label}]',
    ]


_POSITIONS = {
    'top-right': ('W-w-{m}', '{m}'),
    'top-left': ('{m}', '{m}'),
    'bottom-right': ('W-w-{m}', 'H-h-{m}'),
    'bottom-left': ('{m}', 'H-h-{m}'),
    'top-center': ('(W-w)/2', '{m}'),
    'bottom-center': ('(W-w)/2', 'H-h-{m}'),
}


def logo_chain(in_label: str, out_label: str, logo_path,
               position: str = 'top-right', scale: float = 0.14,
               margin: float = 0.04, opacity: float = 1.0) -> List[str]:
    """Composite a logo image, scaled relative to frame width.

    Scaling by a ratio rather than fixed pixels is the point: campaign logo
    folders ship anything from a 200px sprite to a 4000px master, and a fixed
    size makes the first illegible and the second cover the clip.
    """
    target_w = max(24, int(config.width * scale))
    margin_px = max(0, int(config.width * margin))
    x_expr, y_expr = _POSITIONS.get(position, _POSITIONS['top-right'])
    x_expr = x_expr.replace('{m}', str(margin_px))
    y_expr = y_expr.replace('{m}', str(margin_px))

    chains = [f'movie={quote_filter_path(str(logo_path))}[logoraw]']
    # format=rgba before scale so a palettised or opaque source still carries an
    # alpha channel for colorchannelmixer to act on.
    chain = (f'[logoraw]format=rgba,scale={target_w}:-1')
    if opacity < 1.0:
        chain += f',colorchannelmixer=aa={max(0.05, min(1.0, opacity)):.3f}'
    chains.append(chain + '[logo]')
    chains.append(f'[{in_label}][logo]overlay={x_expr}:{y_expr}:'
                  f'format=auto[{out_label}]')
    return chains


def sheet_chain(in_label: str, out_label: str,
                sheets: Sequence[Path]) -> List[str]:
    """Composite text sheets in order.

    Each sheet becomes one ``movie=`` source rather than an extra ``-i`` input,
    so the renderer's input indexing stays fixed no matter how many text
    elements a campaign needs.
    """
    usable = [s for s in sheets if s and Path(s).exists()]
    if not usable:
        return [f'[{in_label}]null[{out_label}]']
    chains: List[str] = []
    src = in_label
    for index, sheet in enumerate(usable):
        tag = f'tx{index}'
        dst = out_label if index == len(usable) - 1 else f'txo{index}'
        chains.append(f'movie={quote_filter_path(str(sheet))}[{tag}]')
        chains.append(f'[{src}][{tag}]overlay=0:0:format=auto[{dst}]')
        src = dst
    return chains


# -- logo detection -----------------------------------------------------
def logo_present(video_path, logo_path, samples: int = 5,
                 threshold: float = 0.72) -> Optional[bool]:
    """Is this logo already burned into the video?

    Multi-scale normalised template match over frames sampled across the whole
    clip. Returns None when OpenCV is unavailable, and callers treat None as
    "absent" on purpose: a duplicated logo costs appearance, a missing one
    costs the payout, so the tie breaks toward stamping.

    Sampling several frames matters because these logos are often animated in
    or only present during part of the clip.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.info('LOGO_DETECT_UNAVAILABLE opencv not installed')
        return None
    try:
        template = cv2.imread(str(logo_path), cv2.IMREAD_UNCHANGED)
        if template is None:
            return None
        # Flatten alpha onto black; matching a 4-channel template against a
        # 3-channel frame silently returns garbage rather than erroring.
        if template.ndim == 3 and template.shape[2] == 4:
            alpha = template[:, :, 3:4].astype('float32') / 255.0
            template = (template[:, :, :3].astype('float32')
                        * alpha).astype('uint8')
        template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        capture = cv2.VideoCapture(str(video_path))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            capture.release()
            return None
        best = 0.0
        for index in range(samples):
            capture.set(cv2.CAP_PROP_POS_FRAMES,
                        int(total * (index + 0.5) / samples))
            ok, frame = capture.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for ratio in (0.08, 0.12, 0.16, 0.22, 0.30):
                width = max(16, int(gray.shape[1] * ratio))
                if width >= gray.shape[1] or template.shape[1] < 8:
                    continue
                height = max(8, int(template.shape[0]
                                    * width / template.shape[1]))
                if height >= gray.shape[0]:
                    continue
                resized = cv2.resize(template, (width, height),
                                     interpolation=cv2.INTER_AREA)
                result = cv2.matchTemplate(gray, resized,
                                           cv2.TM_CCOEFF_NORMED)
                best = max(best, float(np.max(result)))
        capture.release()
        logger.info('LOGO_DETECT file=%s best_match=%.3f threshold=%.2f',
                    Path(video_path).name, best, threshold)
        return best >= threshold
    except Exception as exc:
        logger.warning('LOGO_DETECT_FAILED error=%s', str(exc)[:160])
        return None
