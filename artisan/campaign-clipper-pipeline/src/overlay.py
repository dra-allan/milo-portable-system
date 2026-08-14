"""Caption and logo compositing over a real full-frame 9:16 crop."""

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .config import config
from .utils import ensure_dir, quote_filter_path, setup_logger

logger = setup_logger(__name__)

_EMOJI_RANGES = ('[\\U0001F000-\\U0001FAFF\\u2600-\\u27BF\\u2B00-\\u2BFF'
                 '\\u2190-\\u21FF\\uFE0F\\u200D]')


def _emoji_re():
    import re
    return re.compile(_EMOJI_RANGES)


def normalize_text(text: str) -> str:
    return ' '.join(str(text or '').split())


def _group_emoji(text: str) -> List[Tuple[str, bool]]:
    pattern = _emoji_re()
    runs, current, current_is = [], '', None
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
    font = _font(font_path, size)
    words = normalize_text(text).split()
    lines, current = [], ''
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
    while size > 22:
        lines = _wrap(text, font_path, size, max_width, max_lines + 1)
        if len(lines) <= max_lines:
            return size, lines
        size = int(size * 0.9)
    return size, _wrap(text, font_path, size, max_width, max_lines)


def _highlight_runs(text: str, phrase: str) -> List[Tuple[str, bool]]:
    if not phrase:
        return [(text, False)]
    index = text.lower().find(phrase.lower())
    if index < 0:
        return [(text, False)]
    return [(text[:index], False),
            (text[index:index + len(phrase)], True),
            (text[index + len(phrase):], False)]


def text_sheet(text: str, out_path: Path, highlight: str = '',
               y_ratio: Optional[float] = None,
               size: Optional[int] = None) -> Optional[Path]:
    text = normalize_text(text)
    if not text:
        return None
    from PIL import Image, ImageDraw
    font_path = config.resolve_font()
    emoji_path = config.resolve_emoji_font()
    width, height = config.width, config.height
    margin = int(width * config.text_side_margin)
    base_size, lines = _fit(text, font_path, size or config.text_size,
                            width - margin * 2, config.text_max_lines)
    sheet = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    stroke = max(2, int(base_size * config.text_stroke_ratio))
    sx, sy = config.text_shadow
    text_font = _font(font_path, base_size)
    emoji_font = _font(emoji_path, max(12, int(base_size * 0.9))) if emoji_path else None
    line_height = int(base_size * 1.22)
    top = int(height * (y_ratio if y_ratio is not None else config.text_y_ratio))
    for index, line in enumerate(lines):
        runs = []
        for piece, high in _highlight_runs(line, highlight):
            for part, emoji in _group_emoji(piece):
                runs.append((part, emoji, config.text_highlight if high else config.text_fill))
        total = sum((emoji_font if emoji and emoji_font else text_font).getlength(part)
                    for part, emoji, _ in runs)
        cursor, base_y = (width - total) / 2, top + index * line_height
        for part, emoji, colour in runs:
            if not part:
                continue
            font = emoji_font if emoji and emoji_font else text_font
            y = base_y + (int(base_size * 0.06) if emoji else 0)
            if emoji and emoji_font:
                draw.text((cursor, y), part, font=font)
            else:
                if (sx, sy) != (0, 0):
                    draw.text((cursor + sx, y + sy), part, font=font,
                              fill='#000000', stroke_width=stroke, stroke_fill='#000000')
                draw.text((cursor, y), part, font=font, fill=colour,
                          stroke_width=stroke, stroke_fill='#000000')
            cursor += font.getlength(part)
    ensure_dir(Path(out_path).parent)
    sheet.save(out_path)
    return Path(out_path)


def sheet_ink(path) -> int:
    try:
        from PIL import Image
        with Image.open(path) as image:
            alpha = image.convert('RGBA').getchannel('A')
            return sum(1 for value in alpha.getdata() if value > 8)
    except Exception as exc:
        logger.warning('SHEET_INK_FAILED path=%s error=%s', path, exc)
        return 0


def crop_chain(in_label: str, out_label: str, crop=None,
               scaler: str = 'lanczos') -> List[str]:
    """Crop source to Shorts aspect, then scale to the exact output frame."""
    w, h = config.width, config.height
    if crop is None:
        # Expression-based centre crop fallback, valid for every source size.
        return [f'[{in_label}]scale={w}:{h}:force_original_aspect_ratio=increase,'
                f'crop={w}:{h},setsar=1[{out_label}]']
    x, y, cw, ch = crop
    return [f'[{in_label}]crop={cw}:{ch}:{x}:{y},'
            f'scale={w}:{h}:flags={scaler},setsar=1[{out_label}]']


def fill_chain(in_label: str, out_label: str) -> List[str]:
    """Backward-compatible centre-crop chain. No blur, no synthetic canvas."""
    return crop_chain(in_label, out_label, crop=None)


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
    target_w = max(24, int(config.width * scale))
    margin_px = max(0, int(config.width * margin))
    x_expr, y_expr = _POSITIONS.get(position, _POSITIONS['top-right'])
    x_expr = x_expr.replace('{m}', str(margin_px))
    y_expr = y_expr.replace('{m}', str(margin_px))
    chains = [f'movie={quote_filter_path(str(logo_path))}[logoraw]']
    chain = f'[logoraw]format=rgba,scale={target_w}:-1'
    if opacity < 1.0:
        chain += f',colorchannelmixer=aa={max(0.05, min(1.0, opacity)):.3f}'
    chains.append(chain + '[logo]')
    chains.append(f'[{in_label}][logo]overlay={x_expr}:{y_expr}:format=auto[{out_label}]')
    return chains


def sheet_chain(in_label: str, out_label: str, sheets: Sequence[Path]) -> List[str]:
    usable = [s for s in sheets if s and Path(s).exists()]
    if not usable:
        return [f'[{in_label}]null[{out_label}]']
    chains, src = [], in_label
    for index, sheet in enumerate(usable):
        tag = f'tx{index}'
        dst = out_label if index == len(usable) - 1 else f'txo{index}'
        chains.append(f'movie={quote_filter_path(str(sheet))}[{tag}]')
        chains.append(f'[{src}][{tag}]overlay=0:0:format=auto[{dst}]')
        src = dst
    return chains


# Kept as a validator-compatible API. Logo detection is unchanged in behaviour.
def logo_present(video_path, logo_path, samples: int = 5,
                 threshold: float = 0.72) -> Optional[bool]:
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
        if template.ndim == 3 and template.shape[2] == 4:
            alpha = template[:, :, 3:4].astype('float32') / 255.0
            template = (template[:, :, :3].astype('float32') * alpha).astype('uint8')
        template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        cap = cv2.VideoCapture(str(video_path))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            cap.release()
            return None
        best = 0.0
        for index in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (index + 0.5) / samples))
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            for ratio in (0.08, 0.12, 0.16, 0.22, 0.30):
                width = max(16, int(gray.shape[1] * ratio))
                if width >= gray.shape[1] or template.shape[1] < 8:
                    continue
                height = max(8, int(template.shape[0] * width / template.shape[1]))
                if height >= gray.shape[0]:
                    continue
                resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
                result = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
                best = max(best, float(np.max(result)))
        cap.release()
        return best >= threshold
    except Exception as exc:
        logger.warning('LOGO_DETECT_FAILED error=%s', str(exc)[:160])
        return None
