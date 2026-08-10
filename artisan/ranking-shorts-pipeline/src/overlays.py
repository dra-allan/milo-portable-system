"""Overlay compositing: rank numbers, titles, and blur masks.

All of it is drawtext/boxblur inside a single filtergraph, so the whole clip
is one encode pass rather than a stack of intermediate files.

Two details worth knowing before editing this module:

1. **Strokes are drawn, not styled.** drawtext has exactly one ``bordercolor``.
   The look the reference workflow describes (a coloured metallic stroke with a
   black outline around it) is two strokes, so the number is drawn three times
   in the same place: a fat black border, then a thinner coloured border, then
   the flat fill. Order matters; reversing it buries the fill.

2. **Text is escaped, not trusted.** Clip titles come from a language model or
   from a source video's own metadata. A stray ``:``, ``'``, ``%`` or ``\\`` in
   one of those is not a cosmetic problem: it terminates the filter option and
   FFmpeg rejects the entire graph, so a single apostrophe would fail the build.
"""

from typing import Dict, List, Optional, Sequence

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__)


def escape_drawtext(text: str) -> str:
    """Escape a string for use as a drawtext ``text=`` value.

    Backslash first, or it would double-escape the escapes added after it.
    """
    if text is None:
        return ''
    out = str(text)
    out = out.replace('\\', '\\\\')
    out = out.replace("'", "\\'")
    out = out.replace(':', '\\:')
    out = out.replace('%', '\\%')
    out = out.replace(',', '\\,')
    out = out.replace('[', '\\[').replace(']', '\\]')
    out = out.replace('\n', ' ').replace('\r', ' ')
    return out


def _drawtext(font: str, text: str, size: int, color: str,
              x: str, y: str, border_color: Optional[str] = None,
              border_width: int = 0, enable: Optional[str] = None) -> str:
    parts = [
        f"drawtext=fontfile='{font}'",
        f"text='{escape_drawtext(text)}'",
        f'fontsize={size}',
        f'fontcolor={color}',
    ]
    if border_color and border_width > 0:
        parts.append(f'bordercolor={border_color}')
        parts.append(f'borderw={border_width}')
    parts.append(f'x={x}')
    parts.append(f'y={y}')
    if enable:
        parts.append(f"enable='{enable}'")
    return ':'.join(parts)


def fill_chain(in_label: str, out_label: str) -> List[str]:
    """Fit a landscape clip into 9:16 over a blurred copy of itself.

    Cropping to fill is the obvious alternative and it is wrong here: these are
    wide handheld clips where the action is often near an edge, and a centre
    crop throws away the thing the clip is ranked for. The blurred bed also
    leaves clean space top and bottom for the video title and the clip title.
    """
    w, h = config.width, config.height
    return [
        f'[{in_label}]scale={w}:-2:force_original_aspect_ratio=decrease,'
        f'setsar=1[fgv]',
        f'[{in_label}]scale={w}:{h}:force_original_aspect_ratio=increase,'
        f'crop={w}:{h},boxblur=luma_radius=40:luma_power=2,setsar=1[bgv]',
        f'[fgv]null[fgv2]',  # keeps labels stable if the chain is extended
        f'[bgv][fgv2]overlay=(W-w)/2:(H-h)/2:shortest=1[{out_label}]',
    ]


def mask_chain(in_label: str, out_label: str,
               boxes: Sequence[Dict[str, int]]) -> List[str]:
    """Blur out regions of the frame that carry the source's own text/logos.

    Each box is ``{x, y, w, h}`` in output-frame coordinates, as produced by
    :mod:`src.vetting`. A crop+blur+overlay per box is used rather than one
    global blur so the rest of the picture stays sharp.

    crop requires even offsets and dimensions, and refuses a region that runs
    past the frame edge, so every box is clamped and rounded here. OCR boxes
    routinely land a pixel or two outside the frame.
    """
    if not boxes:
        return [f'[{in_label}]null[{out_label}]']

    chains: List[str] = []
    src = in_label
    usable = []
    for box in boxes:
        x = max(0, int(box.get('x', 0)) // 2 * 2)
        y = max(0, int(box.get('y', 0)) // 2 * 2)
        bw = int(box.get('w', 0)) // 2 * 2
        bh = int(box.get('h', 0)) // 2 * 2
        bw = min(bw, config.width - x)
        bh = min(bh, config.height - y)
        if bw < 8 or bh < 8:
            continue
        usable.append((x, y, bw, bh))

    if not usable:
        return [f'[{in_label}]null[{out_label}]']

    for idx, (x, y, bw, bh) in enumerate(usable):
        last = idx == len(usable) - 1
        base = f'mb{idx}'
        dst = out_label if last else f'mk{idx}'
        # split, because a label can only be consumed once: the same frames
        # are needed as the backdrop and as the source of the blurred patch.
        chains.append(f'[{src}]split=2[{base}a][{base}b]')
        chains.append(
            f'[{base}a]crop={bw}:{bh}:{x}:{y},'
            f'boxblur=luma_radius=18:luma_power=2[{base}p]')
        chains.append(f'[{base}b][{base}p]overlay={x}:{y}[{dst}]')
        src = dst
    return chains


def text_chain(in_label: str, out_label: str, rank: int, clip_title: str,
               video_title: str, clips_total: int,
               show_video_title: bool = True) -> List[str]:
    """Rank number + clip title + video title, as one drawtext chain."""
    font = config.resolve_font()
    stroke = config.rank_color(rank)
    fill = str(config.get('rank_fill', 'white'))
    outline = str(config.get('rank_outline', 'black'))
    accent = str(config.get('accent_color', '0x1E90FF'))

    rank_size = int(config.get('rank_fontsize', 300))
    clip_size = int(config.get('clip_title_fontsize', 68))
    title_size = int(config.get('video_title_fontsize', 62))
    rank_y = int(config.get('rank_y', 760))
    clip_y = int(config.get('clip_title_y', 1180))
    title_y = int(config.get('video_title_y', 300))

    filters: List[str] = []

    if show_video_title:
        # "TOP" + the number, drawn separately so the number can be scaled up
        # and accented the way the reference workflow does it.
        head = str(clips_total)
        filters.append(_drawtext(
            font, 'TOP', int(title_size * 1.45), fill,
            x='(w-text_w)/2-70', y=str(title_y - 120),
            border_color=accent, border_width=8))
        filters.append(_drawtext(
            font, head, int(title_size * 2.1), accent,
            x='(w-text_w)/2+110', y=str(title_y - 150),
            border_color=fill, border_width=8))
        body = video_title
        # Strip a leading "TOP 5" from the configured title: it is already
        # drawn above, and repeating it eats the frame.
        upper = body.upper()
        if upper.startswith('TOP '):
            body = body[4:].lstrip()
            if body[:len(head)] == head:
                body = body[len(head):].lstrip()
        filters.append(_drawtext(
            font, body.upper(), title_size, fill,
            x='(w-text_w)/2', y=str(title_y),
            border_color=outline, border_width=8))

    # Clip title sits *behind* the rank number in the reference edit, i.e. it
    # is drawn first so the number wins any overlap.
    if clip_title:
        filters.append(_drawtext(
            font, clip_title.upper(), clip_size, fill,
            x='(w-text_w)/2', y=str(clip_y),
            border_color=outline, border_width=10))

    number = str(rank)
    filters.append(_drawtext(font, number, rank_size, outline,
                             x='(w-text_w)/2', y=str(rank_y),
                             border_color=outline, border_width=22))
    filters.append(_drawtext(font, number, rank_size, stroke,
                             x='(w-text_w)/2', y=str(rank_y),
                             border_color=stroke, border_width=12))
    filters.append(_drawtext(font, number, rank_size, fill,
                             x='(w-text_w)/2', y=str(rank_y)))

    return [f'[{in_label}]' + ','.join(filters) + f'[{out_label}]']


def hook_zoom_chain(in_label: str, out_label: str) -> List[str]:
    """Punch-in on the opening clip, easing back to 1.0.

    zoompan is used with ``d=1`` so it emits one output frame per input frame
    and ``on`` counts real frames; with the default ``d`` it would hold and
    stretch the clip. ``s`` and ``fps`` must be pinned or zoompan resets the
    frame size to its own default and the concat stage rejects the clip.
    """
    if not config.get('hook_zoom', True):
        return [f'[{in_label}]null[{out_label}]']
    amount = float(config.get('hook_zoom_amount', 1.18))
    frames = int(config.get('hook_zoom_frames', 24))
    delta = amount - 1.0
    expr = (f'if(lte(on,{frames}),{amount}-{delta}*on/{frames},1)')
    return [
        f"[{in_label}]zoompan=z='{expr}':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f's={config.width}x{config.height}:fps={config.fps}[{out_label}]'
    ]
