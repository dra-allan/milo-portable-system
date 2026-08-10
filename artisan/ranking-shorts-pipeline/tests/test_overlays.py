"""Overlay tests.

The important assertion style here: where a bug could make FFmpeg *succeed and
draw nothing*, the test renders a real frame and counts drawn pixels. Asserting
on the exit code is what let the original ``text=`` escaping bug through - it
logged "Stray %", drew no text, and returned zero.

The FFmpeg-dependent tests skip cleanly when ffmpeg, a font or Pillow is
missing, so the suite still runs on a bare box.
"""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import overlays  # noqa: E402
from src.config import config  # noqa: E402

HOSTILE = "THAT'S 100% WILD, BUDDY: PART [2] 50%OFF"


def _have_ffmpeg():
    return shutil.which('ffmpeg') is not None


def _have_font():
    try:
        config.resolve_font()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------------------
# Text is passed by file, and passed through intact
# ---------------------------------------------------------------------------
def test_text_file_holds_the_string_verbatim(tmp_path):
    path = overlays.write_text_file(tmp_path, 'clip', HOSTILE)
    assert path.read_text(encoding='utf-8') == HOSTILE


def test_newlines_are_collapsed(tmp_path):
    """A multi-line title overflows its band and collides with the number."""
    path = overlays.write_text_file(tmp_path, 'clip', 'MAN\nOVERBOARD\r\nNOW')
    assert path.read_text(encoding='utf-8') == 'MAN OVERBOARD NOW'


def test_chain_uses_textfile_and_disables_expansion(tmp_path):
    chains = overlays.text_chain('in', 'out', 1, HOSTILE, 'TOP 5 FISHING', 5,
                                work_dir=tmp_path)
    graph = ';'.join(chains)
    # text= would reintroduce the escaping problem this module exists to avoid.
    assert 'textfile=' in graph
    assert ':text=' not in graph
    # Without expansion=none a '%' in a title draws nothing and exits 0.
    assert graph.count('expansion=none') == graph.count('textfile=')


def test_font_and_text_paths_are_quoted(tmp_path):
    """An unquoted Windows path reads as an option separator at the colon."""
    chains = overlays.text_chain('in', 'out', 3, 'CATCH', 'TOP 5', 5,
                                work_dir=tmp_path)
    graph = chains[0]
    assert "fontfile='" in graph
    assert "textfile='" in graph
    # The colon is escaped AND the value is quoted: the graph parser strips
    # quotes before drawtext's own option splitter runs, so either alone
    # leaves the drive-letter colon a separator. Verified against FFmpeg.
    assert r"C\:/Windows/Fonts/impact.ttf" in graph


def test_clip_and_rank_use_separate_files(tmp_path):
    overlays.text_chain('in', 'out', 4, 'MAN OVERBOARD', 'TOP 5', 5,
                       work_dir=tmp_path)
    assert (tmp_path / 'rank.txt').read_text(encoding='utf-8') == '4'
    assert (tmp_path / 'clip.txt').read_text(encoding='utf-8') == \
        'MAN OVERBOARD'


def test_default_work_dir_is_per_rank():
    """Two clips in one build must not share a text file, or the last write
    wins for both and every clip shows the same number."""
    a = overlays.text_chain('in', 'out', 5, 'A', 'TOP 5 FISHING', 5)
    b = overlays.text_chain('in', 'out', 1, 'B', 'TOP 5 FISHING', 5)
    assert a[0] != b[0]


# ---------------------------------------------------------------------------
# Strokes
# ---------------------------------------------------------------------------
def test_rank_is_drawn_three_times_for_a_two_tone_stroke(tmp_path):
    chains = overlays.text_chain('in', 'out', 1, '', 'TOP 5', 5,
                                work_dir=tmp_path, show_video_title=False)
    graph = chains[0]
    assert graph.count("textfile='") == 3
    # black outer, gold inner, then the flat fill on top.
    assert graph.index('borderw=22') < graph.index('borderw=12')


@pytest.mark.parametrize('rank, colour', [
    (1, '0xFFD700'),   # gold
    (2, '0xC0C0C0'),   # silver
    (3, '0xCD7F32'),   # bronze
    (4, '0x1E90FF'),   # blue
    (5, '0x1E90FF'),
])
def test_rank_colours_follow_the_medal_scheme(rank, colour, tmp_path):
    graph = overlays.text_chain('in', 'out', rank, '', 'TOP 5', 5,
                                work_dir=tmp_path)[0]
    assert colour in graph


def test_leading_top_n_is_not_drawn_twice(tmp_path):
    overlays.text_chain('in', 'out', 1, '', 'TOP 5 FISHING MOMENTS', 5,
                       work_dir=tmp_path)
    assert (tmp_path / 'vtitle.txt').read_text(encoding='utf-8') == \
        'FISHING MOMENTS'


# ---------------------------------------------------------------------------
# Masks
# ---------------------------------------------------------------------------
def test_no_boxes_is_a_passthrough():
    assert overlays.mask_chain('a', 'b', []) == ['[a]null[b]']


def test_box_running_past_the_frame_is_clamped():
    """crop refuses a region past the edge, failing the whole render."""
    box = overlays.clamp_box({'x': config.width - 40, 'y': 10,
                              'w': 500, 'h': 60})
    assert box['x'] + box['w'] <= config.width


def test_box_offsets_and_sizes_are_even():
    box = overlays.clamp_box({'x': 101, 'y': 203, 'w': 305, 'h': 47})
    assert box['x'] % 2 == 0 and box['y'] % 2 == 0
    assert box['w'] % 2 == 0 and box['h'] % 2 == 0


def test_degenerate_box_is_dropped():
    assert overlays.clamp_box({'x': 0, 'y': 0, 'w': 4, 'h': 4}) is None


def test_each_box_splits_before_reuse():
    """A label consumed twice without a split is a hard graph error."""
    chains = overlays.mask_chain('src', 'out', [
        {'x': 100, 'y': 900, 'w': 300, 'h': 90},
        {'x': 600, 'y': 1000, 'w': 200, 'h': 80},
    ])
    graph = ';'.join(chains)
    assert graph.count('split=2') == 2
    assert graph.endswith('[out]')


# ---------------------------------------------------------------------------
# Real renders
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _have_ffmpeg() or not _have_font(),
                    reason='needs ffmpeg and a font')
def test_hostile_title_actually_draws_pixels(tmp_path):
    """The regression that matters: this exact string used to render a clean,
    successful, completely blank overlay."""
    pytest.importorskip('PIL')
    from PIL import Image

    chains = overlays.text_chain('0:v', 'vout', 1, HOSTILE, 'TOP 5 FISHING', 5,
                                work_dir=tmp_path)
    out = tmp_path / 'frame.png'
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
        '-i', f'color=c=navy:s={config.width}x{config.height}:d=1',
        '-filter_complex', ';'.join(chains), '-map', '[vout]',
        '-frames:v', '1', str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()[:400]

    lit = sum(1 for value in Image.open(out).convert('L').getdata()
              if value > 200)
    # Exit code 0 is not evidence of anything here; pixels are.
    assert lit > 5000, f'overlay drew almost nothing ({lit} lit pixels)'


@pytest.mark.skipif(not _have_ffmpeg() or not _have_font(),
                    reason='needs ffmpeg and a font')
def test_full_clip_graph_is_accepted_by_ffmpeg(tmp_path):
    """fill -> mask -> text -> hook zoom, end to end, as the assembler builds it."""
    chains = []
    chains += overlays.fill_chain('0:v', 'filled')
    chains += overlays.mask_chain('filled', 'masked',
                                 [{'x': 100, 'y': 900, 'w': 300, 'h': 90}])
    chains += overlays.text_chain('masked', 'texted', 2, "IT'S 50% GONE",
                                 'TOP 5 FISHING MOMENTS', 5,
                                 work_dir=tmp_path)
    chains += overlays.hook_zoom_chain('texted', 'zoomed')
    chains.append(f'[zoomed]fps={config.fps},format=yuv420p,setsar=1[vout]')

    out = tmp_path / 'clip.mp4'
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=1',
        '-filter_complex', ';'.join(chains), '-map', '[vout]',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode()[:600]
    assert out.stat().st_size > 1024
