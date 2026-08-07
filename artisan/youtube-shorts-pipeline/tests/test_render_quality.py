"""Tests for output-quality decisions in the render path.

These lock in the fixes for "the exported video looks low quality". Each one
maps to a specific defect that was degrading the output:

* the download format was pinned to format 18 (640x360) -- the root cause,
  since nothing downstream can recover detail that was never fetched;
* every rescale used ``fast_bilinear``, swscale's lowest-quality filter,
  including the one producing the sharp foreground;
* output was forced to ``-r 30``, discarding half the frames of a 60fps source;
* captions were composited onto 4:2:0 chroma, muddying the glyph edges;
* the file carried no colour metadata, so players guessed BT.601 vs BT.709.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import video_editor as ve  # noqa: E402
from src.config import config  # noqa: E402


MODES = ['cheap', 'blur', 'black', 'crop']


# ---------------------------------------------------------------------------
# Download resolution -- the root cause
# ---------------------------------------------------------------------------
def test_download_format_is_not_pinned_to_360p():
    """Format 18 is 640x360 and used to be selected unconditionally.

    Because yt-dlp honours the order in a '/'-separated format string, listing
    '18' first meant DOWNLOAD_HEIGHT was ignored entirely and every render was
    an upscale from 360p.
    """
    source = open(os.path.join(os.path.dirname(__file__), '..',
                              'src', 'downloader.py'), encoding='utf-8').read()
    # No format string may begin with the bare progressive format 18.
    assert not re.search(r"['\"]18/", source), \
        "format 18 (640x360) must not be preferred over high-res streams"


def test_download_prefers_separate_high_res_streams():
    """1080p+ only exists as separate video+audio on YouTube."""
    source = open(os.path.join(os.path.dirname(__file__), '..',
                              'src', 'downloader.py'), encoding='utf-8').read()
    assert 'bestvideo[height<=' in source
    assert '+bestaudio' in source
    # And ties break toward resolution, not extractor order.
    assert 'format_sort' in source


def test_default_download_height_supports_a_vertical_crop():
    """Smart framing crops *into* the source, so headroom matters.

    A 1080p landscape frame cropped to 9:16 is only ~608px wide, which then has
    to be upscaled to 1080. Allowing more than 1080p is what keeps a cropped
    tile sharp.
    """
    assert config.download_height >= 1080


# ---------------------------------------------------------------------------
# Scaler quality
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('mode', MODES)
def test_visible_scale_does_not_use_fast_bilinear(mode):
    """fast_bilinear is the lowest-quality scaler swscale offers.

    It is acceptable on a backdrop that is about to be Gaussian blurred, but
    not on the foreground the viewer is actually reading.
    """
    filters, _ = ve.build_background_filters(mode, scaler='lanczos')
    graph = ';'.join(filters)

    for stage in filters:
        if 'gblur' in stage or 'bgb]' in stage:
            continue                 # backdrop: quality is unobservable
        assert 'fast_bilinear' not in stage, \
            f"visible rescale in mode '{mode}' uses fast_bilinear: {stage}"

    if mode in ('crop', 'black'):
        assert 'lanczos' in graph


@pytest.mark.parametrize('mode', MODES)
def test_scaler_is_configurable(mode):
    filters, _ = ve.build_background_filters(mode, scaler='bicubic')
    visible = [s for s in filters if 'gblur' not in s and 'bgb]' not in s]
    assert any('bicubic' in s for s in visible)


@pytest.mark.parametrize('mode', MODES)
def test_backdrop_still_uses_the_cheap_scaler(mode):
    """The blur optimisation must survive the quality change.

    Sharpening the backdrop scaler would re-introduce the cost that made the
    full-resolution gblur more expensive than the encode itself.
    """
    filters, _ = ve.build_background_filters(mode, scaler='lanczos')
    blur_stages = [s for s in filters if 'gblur' in s]
    for stage in blur_stages:
        assert 'fast_bilinear' in stage


@pytest.mark.parametrize('mode', MODES + ['smart'])
def test_every_mode_sets_sar(mode):
    """An unset SAR makes players stretch the frame."""
    filters, label = ve.build_background_filters(mode)
    assert 'setsar=1' in ';'.join(filters)
    assert label


def test_smart_mode_degrades_to_fill_not_letterbox():
    """'smart' reaching this function must still fill the frame.

    It previously returned letterboxed bars, so a stray direct call produced
    output that looked nothing like the requested mode.
    """
    filters, _ = ve.build_background_filters('smart')
    graph = ';'.join(filters)
    assert 'crop=' in graph
    assert 'pad=' not in graph


def test_config_scaler_default_is_high_quality():
    assert config.video_scaler == 'lanczos'


def test_invalid_scaler_falls_back_safely(monkeypatch):
    monkeypatch.setenv('VIDEO_SCALER', 'not-a-real-scaler')
    from src.config import Config
    assert Config().video_scaler == 'lanczos'


# ---------------------------------------------------------------------------
# Encoder settings
# ---------------------------------------------------------------------------
def test_crf_is_visually_transparent():
    """CRF 20 left visible blocking once captions were composited on top."""
    assert config.video_crf <= 19


def test_preset_favours_quality():
    """At a fixed CRF, a slower preset is a better frame for the same size."""
    assert config.video_preset in ('slow', 'slower', 'veryslow', 'medium')


def test_audio_is_not_bitrate_starved():
    assert int(str(config.audio_bitrate).rstrip('k')) >= 160
    assert config.audio_sample_rate >= 44100


def test_fps_cap_allows_60():
    """Forcing 30fps threw away half the frames of a 60fps source."""
    assert config.video_max_fps >= 60


def test_render_command_carries_quality_flags():
    """Colour tags, profile and tuning must reach the ffmpeg command line."""
    source = open(os.path.join(os.path.dirname(__file__), '..',
                               'src', 'video_editor.py'), encoding='utf-8').read()
    for flag in ('-colorspace', 'bt709', '-color_primaries', '-color_trc',
                 '-profile:v', '-tune', '-movflags', '+faststart'):
        assert flag in source, f"missing encoder flag {flag}"


def test_no_hard_coded_30fps_output():
    """'-r', '30' unconditionally resampled every source."""
    source = open(os.path.join(os.path.dirname(__file__), '..',
                               'src', 'video_editor.py'), encoding='utf-8').read()
    assert "'-r', '30'" not in source


def test_captions_are_composited_at_full_chroma():
    """Burning glyphs onto 4:2:0 fringes the coloured emphasis words."""
    source = open(os.path.join(os.path.dirname(__file__), '..',
                               'src', 'video_editor.py'), encoding='utf-8').read()
    assert 'yuv444p' in source
    # ...and the delivery format is still 4:2:0.
    assert 'yuv420p' in source


# ---------------------------------------------------------------------------
# Framerate selection logic
# ---------------------------------------------------------------------------
class _FakeEditor:
    """Exercise _choose_fps without constructing a real VideoEditor.

    VideoEditor.__init__ probes ffmpeg, which is irrelevant to this logic.
    """

    def __init__(self, fps):
        self._fps = fps

    probe_fps = lambda self, path: self._fps          # noqa: E731
    _choose_fps = ve.VideoEditor._choose_fps


@pytest.mark.parametrize('src_fps', [23.976, 24, 25, 29.97, 30, 50, 60])
def test_fps_within_cap_is_passed_through(src_fps):
    """None means 'insert no resampling filter at all'.

    Restating even the correct rate risks a duplicated/dropped frame; leaving
    it alone preserves timestamps exactly.
    """
    assert _FakeEditor(src_fps)._choose_fps('x') is None


@pytest.mark.parametrize('src_fps', [120, 144, 240])
def test_absurd_fps_is_capped(src_fps):
    assert _FakeEditor(src_fps)._choose_fps('x') == pytest.approx(
        float(config.video_max_fps)
    )


def test_unknown_fps_is_passed_through():
    """An unprobeable source must not be forced to a guessed rate."""
    assert _FakeEditor(None)._choose_fps('x') is None
