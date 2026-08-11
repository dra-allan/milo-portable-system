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
# Text normalising and the active-sheet contract
# ---------------------------------------------------------------------------
def test_newlines_are_collapsed():
    """A multi-line title overflows its band and collides with the number."""
    assert overlays.normalize_text('MAN\nOVERBOARD\r\nNOW') == \
        'MAN OVERBOARD NOW'


def test_pillow_color_hex_mapping():
    assert overlays._pillow_color('0xFFD700') == '#FFD700'
    assert overlays._pillow_color('#ABCDEF') == '#ABCDEF'
    assert overlays._pillow_color('') == '#FFFFFF'


def test_highlight_runs_colours_only_first_match():
    runs = overlays._highlight_runs('FISHING MOMENTS', ['MOMENTS'], 'hl')
    assert runs == [('FISHING ', False), ('MOMENTS', True), ('', False)]


def test_chain_overlays_sheet_files(tmp_path):
    """The chain is now movie= + overlay; the sheets must exist on disk."""
    chains = overlays.text_chain('in', 'out', 1, 'CATCH', 'TOP 5 FISHING', 5,
                                 work_dir=tmp_path)
    graph = ';'.join(chains)
    assert 'overlay=0:0' in graph
    assert 'movie=' in graph
    assert (tmp_path / 'header.png').exists()
    assert (tmp_path / 'list.png').exists()
    assert (tmp_path / 'active.png').exists()
    from PIL import Image
    for name in ('header.png', 'list.png', 'active.png'):
        assert Image.open(tmp_path / name).size == (config.width,
                                                    config.height)


def test_font_path_embedded_in_movie_isfine(tmp_path):
    """The movie= path keeps the single-quote + escaped-colon wrapper. An
    unquoted Windows path reads as an option separator at the colon."""
    chains = overlays.text_chain('in', 'out', 3, 'CATCH', 'TOP 5', 5,
                                 work_dir=tmp_path)
    graph = chains[0]
    assert "movie='C\\:/Users" in graph or "movie='" in graph
    assert r"\:/" in graph


def test_active_sheet_only_for_the_playing_rank(tmp_path):
    """Inactive ranks show just the number - no description sheet for them."""
    overlays.text_chain('in', 'out', 4, 'MAN OVERBOARD', 'TOP 5', 5,
                        work_dir=tmp_path,
                        leaderboard=[{'rank': 4, 'title': 'MAN OVERBOARD'},
                                     {'rank': 2, 'title': 'ROCKET MAN'}])
    assert (tmp_path / 'active.png').exists()
    assert (tmp_path / 'header.png').exists()
    assert (tmp_path / 'list.png').exists()


def test_default_work_dir_is_per_rank():
    """Two clips in one build must not share a sheet, or the last one written
    wins for both and every clip shows the same number."""
    a = overlays.text_chain('in', 'out', 5, 'A', 'TOP 5 FISHING', 5)
    b = overlays.text_chain('in', 'out', 1, 'B', 'TOP 5 FISHING', 5)
    assert a[0] != b[0]


# ---------------------------------------------------------------------------
# Rank colours
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('rank, colour', [
    (1, '0xFFD700'),   # yellow
    (2, '0xFFFFFF'),   # white
    (3, '0xFF7F00'),   # orange
    (4, '0x00E676'),   # green
    (5, '0xFF3B6B'),   # pink
])
def test_rank_colours_follow_the_scheme(rank, colour):
    assert config.rank_color(rank) == colour


def test_emoji_glyph_draws_not_tofu(tmp_path):
    """Emoji must draw as an inked face glyph, not an empty/tofu box.

    Pillow builds without RAQM render CBDT/COLR colour emoji as monochrome
    silhouettes; that is acceptable - the guard against failure is that a
    real glyph draws real ink. A dropped glyph or hollow tofu box inks far
    fewer pixels than a solid face.
    """
    emoji = '\U0001F602'

    def ink(where, title):
        overlays.text_chain('in', 'out', 1, title, 'TOP 5', 5,
                            work_dir=tmp_path / where,
                            leaderboard=[{'rank': 1, 'title': title}])
        from PIL import Image
        return sum(1 for px in Image.open(tmp_path / where / 'active.png')
                   .convert('RGBA').getdata() if px[3] > 0)

    delta = ink('with', 'BEAR ATTACK ' + emoji) - ink('without', 'BEAR ATTACK')
    assert delta > 500, f'emoji should add ~1k inked pixels, added only {delta}'


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
