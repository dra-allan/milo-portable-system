"""Overlay compositing: rank numbers, titles, and blur masks.

All of it is drawtext/boxblur inside a single filtergraph, so the whole clip is
one encode pass rather than a stack of intermediate files.

Three things to know before editing this module.

**1. Text is passed by file, never inline.**
``textfile=`` is used with ``expansion=none`` instead of ``text=``. Clip titles
come from a language model or from a scraped video's own metadata, and FFmpeg
puts filter option values through several layers of quoting and escaping. There
is no escaping recipe that survives ``'``, ``:``, ``,`` and ``%`` together - a
measured matrix of quoted, unquoted and backslash-escaped forms, in both ``-vf``
and ``-filter_complex``, failed at least one of them every time. Two concrete
failures that a title like ``THAT'S 100% WILD, BUDDY`` produced:

* an apostrophe or colon aborted the graph outright
  (*"Both text and text file provided"*), and
* a percent sign logged *"Stray %"*, drew **nothing at all**, and still exited
  zero - a video published with no rank numbers and no titles, and a run that
  reported success.

A file has no syntax to break out of. The only remaining escaping concern is the
font and file *paths*, which are wrapped in single quotes so a Windows drive
letter (``C:/Windows/Fonts/impact.ttf``) does not read as an option separator.

**2. Strokes are drawn, not styled.** drawtext has exactly one
``bordercolor``. The look the reference workflow describes - a metallic stroke
with a black outline around it - is two strokes, so the number is drawn three
times in the same place: fat black border, thinner coloured border, then the
flat fill. Reversing that order buries the fill.

**3. A filter label can only be consumed once.** Anywhere the same frames are
needed twice (backdrop plus blurred patch) there has to be an explicit
``split``.
"""

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


def write_text_file(work_dir: Path, name: str, text: str) -> Path:
    """Write one overlay string to its own UTF-8 file.

    Newlines are collapsed: drawtext would render them as a multi-line block
    that overflows the reserved band and collides with the rank number.
    """
    ensure_dir(work_dir)
    flat = ' '.join(str(text or '').split())
    path = work_dir / f'{name}.txt'
    path.write_text(flat, encoding='utf-8')
    return path


def _drawtext(font: str, textfile: Path, size: int, color: str,
              x: str, y: str, border_color: Optional[str] = None,
              border_width: int = 0,
              box_color: Optional[str] = None,
              box_borderw: int = 0) -> str:
    parts = [
        f'drawtext=fontfile={_quote(font)}',
        f'textfile={_quote(textfile)}',
        'expansion=none',   # makes % and {} literal instead of format codes
        'reload=0',         # the file never changes mid-render
        f'fontsize={size}',
        f'fontcolor={color}',
    ]
    if border_color and border_width > 0:
        parts.append(f'bordercolor={border_color}')
        parts.append(f'borderw={border_width}')
    if box_color:
        parts.append('box=1')
        parts.append(f'boxcolor={box_color}')
        if box_borderw > 0:
            parts.append(f'boxborderw={box_borderw}')
    parts.append(f'x={x}')
    parts.append(f'y={y}')
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
        'setsar=1[fgv]',
        f'[{in_label}]scale={w}:{h}:force_original_aspect_ratio=increase,'
        f'crop={w}:{h},boxblur=luma_radius=40:luma_power=2,setsar=1[bgv]',
        f'[bgv][fgv]overlay=(W-w)/2:(H-h)/2:shortest=1[{out_label}]',
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
    """Header + a compact ranked side-list, as one drawtext chain.

    Layout is deliberately "list, not countdown": a small column of numbered
    rows with the rank as a badge pill and the clip title beside it, sitting in
    the corner so the footage stays the focus. The playing clip's row is
    highlighted; the rest are dimmed. No giant numeral, no full-width title
    band.

    ``work_dir`` holds the .txt files backing each drawtext. It defaults to a
    per-rank directory derived from the video title, which is unique within a
    build - two clips in the same build must not share a text file or the last
    one written wins for both.
    """
    font = config.resolve_font()
    work_dir = Path(work_dir) if work_dir else (
        config.temp_dir / 'text' / f'{safe_slug(video_title)}_r{rank}')

    fill = str(config.get('rank_fill', 'white'))
    outline = str(config.get('rank_outline', 'black'))
    accent = str(config.get('accent_color', '0x1E90FF'))

    title_size = int(config.get('video_title_fontsize', 84))
    title_y = int(config.get('video_title_y', 140))
    body_size = int(config.get('video_title_body_fontsize', 54))

    list_x = int(config.get('list_x', 84))
    list_y = int(config.get('list_y', 560))
    row_h = int(config.get('list_row_h', 92))
    rank_size = int(config.get('list_rank_size', 46))
    label_size = int(config.get('list_label_size', 34))

    filters: List[str] = []

    if show_video_title:
        head = str(clips_total)
        body = _strip_leading_count(video_title, head).upper()
        # One string for "TOP {n}" so the word and the number share a size and
        # sit naturally together - two separately-scaled draws was the spacing
        # bug Allan kept hitting.
        top_file = write_text_file(work_dir, 'top', f'TOP {head}')
        filters.append(_drawtext(font, top_file, title_size, fill,
                                 x='(w-text_w)/2', y=str(title_y),
                                 border_color=accent, border_width=10))
        if body:
            body_file = write_text_file(work_dir, 'vtitle', body)
            filters.append(_drawtext(font, body_file, body_size, fill,
                                     x='(w-text_w)/2',
                                     y=str(title_y + int(title_size * 1.2)),
                                     border_color=outline, border_width=8))

    # The ranked list. Rows are drawn for every clip in the build so the whole
    # leaderboard is visible at once; the current rank is the bright one.
    rows = list(leaderboard or [{'rank': rank, 'title': clip_title}])
    rows = sorted(rows, key=lambda r: int(r.get('rank') or 0))
    for i, row in enumerate(rows):
        r = int(row.get('rank') or 0)
        current = (r == rank)
        row_y = list_y + i * row_h
        num_file = write_text_file(work_dir, f'num{r}', str(r))
        num_color = fill if current else '0x8A8A8A'
        num_box = accent if current else '0x000000@0.45'
        filters.append(_drawtext(font, num_file, rank_size, num_color,
                                 x=str(list_x), y=str(row_y),
                                 border_color=outline, border_width=4,
                                 box_color=num_box, box_borderw=8))
        title = (row.get('title') or clip_title or '').upper()
        if title:
            label_file = write_text_file(work_dir, f'lab{r}', title)
            label_color = fill if current else '0xBFBFBF'
            filters.append(_drawtext(font, label_file, label_size,
                                     label_color,
                                     x=str(list_x + rank_size + 30),
                                     y=str(row_y + int(rank_size * 0.9)
                                           - int(label_size * 0.5)
                                           - int(label_size * 0.06)),
                                     border_color=outline
                                     if current else '0x000000@0.60',
                                     border_width=4))

    return [f'[{in_label}]' + ','.join(filters) + f'[{out_label}]']


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
