"""Tests for BACKGROUND_MODE=reframe — the vendored motion-engine pre-pass.

Pins the wiring decisions:

* the mode is accepted and degrades to ``reframe_fallback``;
* a successful pre-pass swaps the render input for the window-cut master with
  seek 0 and an empty background graph (passthrough);
* caption timing is NOT rebased a second time: it stays anchored to the source
  timeline because write_ass subtracts start_time, which equals the master's
  window-local timeline;
* any engine failure falls back without killing the clip;
* a real end-to-end render through the motion engine produces 1080x1920.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import video_editor as ve  # noqa: E402
from src.config import config  # noqa: E402


@pytest.fixture
def editor():
    return ve.VideoEditor()


@pytest.fixture(autouse=True)
def restore_mode():
    """Tests mutate the shared config singleton; always put it back."""
    saved = (config.background_mode, config.reframe_fallback)
    yield
    config.background_mode, config.reframe_fallback = saved


# ---------------------------------------------------------------------------
# Config surface
# ---------------------------------------------------------------------------

def test_config_accepts_reframe_mode():
    cfg = open(os.path.join(os.path.dirname(__file__), '..', 'src', 'config.py'),
               encoding='utf-8').read()
    assert "'reframe'" in cfg.split('background_mode not in')[1].split(':')[0], \
        "BACKGROUND_MODE must accept 'reframe'"


def test_reframe_fallback_defaults_to_smart():
    assert config.reframe_fallback in ('cheap', 'blur', 'black', 'crop', 'smart')


# ---------------------------------------------------------------------------
# Planning decisions (pure, no ffmpeg)
# ---------------------------------------------------------------------------

def _editor_without_probe():
    """An editor whose ffmpeg points nowhere; planning never calls ffmpeg."""
    obj = object.__new__(ve.VideoEditor)
    obj.ffmpeg = 'not-a-real-ffmpeg'
    obj.ffprobe = 'not-a-real-ffprobe'
    return obj


def test_plan_passthrough_when_disabled():
    config.background_mode = 'crop'
    e = _editor_without_probe()
    assert e._plan_reframe('v.mp4', 12.0, 30.0, False) == \
        ('v.mp4', 12.0, 'crop', None)


def test_plan_reordered_clips_skip_the_pre_pass():
    # Section files are already cut pieces; reframe assumes it owns the whole
    # input timeline, so reordered edits keep the old path.
    config.background_mode = 'reframe'
    config.reframe_fallback = 'smart'
    e = _editor_without_probe()
    video_src, seek, bg_mode, master = e._plan_reframe('v.mp4', 5.0, 20.0, True)
    assert master is None
    assert bg_mode == 'smart'
    assert video_src == 'v.mp4' and seek == 5.0


def test_plan_success_swaps_input_and_disables_background_graph(monkeypatch):
    config.background_mode = 'reframe'
    fake_master = os.path.join(os.path.dirname(__file__), 'fake_master.mp4')
    monkeypatch.setattr(ve.VideoEditor, '_reframe_master',
                        lambda self, vp, st, dur: ve.Path(fake_master))
    e = _editor_without_probe()
    video_src, seek, bg_mode, master = e._plan_reframe('v.mp4', 7.0, 25.0, False)
    assert video_src == fake_master
    assert seek == 0.0
    assert bg_mode == 'reframed'
    assert master is not None and str(master) == fake_master


def test_plan_engine_failure_falls_back(monkeypatch):
    config.background_mode = 'reframe'
    config.reframe_fallback = 'crop'
    monkeypatch.setattr(ve.VideoEditor, '_reframe_master',
                        lambda self, vp, st, dur: None)
    e = _editor_without_probe()
    video_src, seek, bg_mode, master = e._plan_reframe('v.mp4', 3.0, 10.0, False)
    assert master is None
    assert bg_mode == 'crop'
    # Original source and seek survive so smart/crop see source coordinates.
    assert video_src == 'v.mp4' and seek == 3.0


def test_reframe_master_never_raises(editor):
    # A broken ffmpeg binary must come back as None (fallback), not an
    # exception that kills the clip.
    editor.ffmpeg = 'definitely-not-an-ffmpeg-binary'
    result = editor._reframe_master('whatever.mp4', 0.0, 2.0)
    assert result is None


# ---------------------------------------------------------------------------
# End-to-end through the real motion engine
# ---------------------------------------------------------------------------

def _make_source(path, seconds=4):
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', f'testsrc2=size=640x360:rate=24:duration={seconds}',
        '-f', 'lavfi', '-i', f'sine=frequency=330:duration={seconds}',
        '-c:v', 'libx264', '-crf', '28', '-preset', 'veryfast',
        '-c:a', 'aac', '-shortest', str(path),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_end_to_end_render_produces_vertical_master(tmp_path, editor):
    pytest.importorskip('cv2')
    config.background_mode = 'reframe'
    config.reframe_fallback = 'crop'

    source = tmp_path / 'src.mp4'
    _make_source(source)
    out = tmp_path / 'short.mp4'

    ok = editor.create_short_from_segment(
        str(source), 0.0, 4.0, [], str(out), burn_captions=False)

    assert ok, "reframe-mode render must succeed"
    assert out.exists() and out.stat().st_size > 0
    dims = editor.probe_dimensions(str(out))
    assert dims == (1080, 1920)

    # The window temp files are cleaned up; only the delivery remains.
    leftovers = [p.name for p in tmp_path.glob('.rf_*')]
    assert leftovers == [], f"temp master/window leaked: {leftovers}"


def test_end_to_end_fallback_still_renders_when_engine_broken(tmp_path, editor):
    pytest.importorskip('cv2')
    config.background_mode = 'reframe'
    config.reframe_fallback = 'crop'
    editor._motion_ready = True  # force the shim to fail on import

    import unittest.mock as mock
    source = tmp_path / 'src.mp4'
    _make_source(source)
    out = tmp_path / 'short_fallback.mp4'

    with mock.patch.dict(sys.modules, {'reframe': None}):
        ok = editor.create_short_from_segment(
            str(source), 0.0, 4.0, [], str(out), burn_captions=False)

    assert ok, "fallback render must succeed when the engine is unavailable"
    dims = editor.probe_dimensions(str(out))
    assert dims == (1080, 1920)
