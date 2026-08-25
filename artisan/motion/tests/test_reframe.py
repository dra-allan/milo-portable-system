"""Tests for the reframe engine's pure contracts.

Everything here runs without cv2/numpy/mediapipe: the sendcmd/scene-slicing
math and the filtergraph strings are the part that breaks silently in prod,
so they get pinned hard. The full-render smoke test skips when cv2 is
missing rather than failing.
"""
import os
import shutil

import pytest

import reframe


def _ffmpeg():
    return os.environ.get("MILO_FFMPEG") or shutil.which("ffmpeg")


# --- delivery_size ----------------------------------------------------------

def test_delivery_size_720p_landscape_upscales_to_floor():
    # 720p 16:9 source -> 406x720 crop -> upscaled to the 1080 delivery floor.
    w, h = reframe.delivery_size(1280, 720, 9 / 16)
    assert w == 1080
    assert abs(h - 1920) <= 2  # rounding to even


def test_delivery_size_keeps_large_source_height():
    # A 1080p source allows a 608x1080 crop; no upscale needed.
    w, h = reframe.delivery_size(1920, 1080, 9 / 16)
    assert w == 1080
    assert h == 1920


def test_delivery_size_narrow_source_upscales_to_floor():
    # Crop would exceed source width -> clamped to width first, then the
    # delivery floor still applies (never ship sub-HD).
    w, h = reframe.delivery_size(600, 1200, 9 / 16)
    assert w == 1080
    assert abs(h - 1920) <= 2


def test_delivery_size_even_dimensions():
    for ow, oh in [(1279, 719), (1919, 1079), (641, 361)]:
        w, h = reframe.delivery_size(ow, oh, 0.5625)
        assert w % 2 == 0 and h % 2 == 0


# --- dedupe_sendcmd_lines ---------------------------------------------------

def test_sendcmd_dedupes_to_change_points():
    xs = [10, 10, 12, 12, 12, 5]
    lines = reframe.dedupe_sendcmd_lines(xs, fps=30)
    assert lines == [
        "0.0000 crop@c x 10;",
        f"{2 / 30:.4f} crop@c x 12;",
        f"{5 / 30:.4f} crop@c x 5;",
    ]


def test_sendcmd_all_constant_single_line():
    lines = reframe.dedupe_sendcmd_lines([7] * 90, fps=30)
    assert len(lines) == 1
    assert lines[0] == "0.0000 crop@c x 7;"


def test_sendcmd_custom_target():
    lines = reframe.dedupe_sendcmd_lines([1, 2], fps=10, target="crop@z")
    assert all("crop@z" in l for l in lines)


# --- scene_frame_ranges -----------------------------------------------------

def test_ranges_clamp_and_keep_strategy():
    ranges = reframe.scene_frame_ranges(
        [(0, 50), (50, 200)], ['GENERAL', 'TRACK'], total_frames=100)
    assert ranges == [(0, 50, 'GENERAL'), (50, 100, 'TRACK')]


def test_ranges_drop_empty_and_align_strategies():
    # Second range starts past EOF -> dropped; third keeps ITS OWN strategy.
    ranges = reframe.scene_frame_ranges(
        [(0, 30), (500, 600), (30, 60)],
        ['TRACK', 'GENERAL', 'GENERAL'], total_frames=60)
    assert ranges == [(0, 30, 'TRACK'), (30, 60, 'GENERAL')]


def test_ranges_missing_strategy_defaults_track():
    ranges = reframe.scene_frame_ranges([(0, 10)], [], total_frames=10)
    assert ranges == [(0, 10, 'TRACK')]


def test_ranges_all_empty_returns_nothing():
    assert reframe.scene_frame_ranges([(0, 0)], ['TRACK'], 0) == []


# --- concat list ------------------------------------------------------------

def test_concat_list_content_quotes_each_path():
    content = reframe.concat_list_content(['/tmp/a_000.mp4', '/tmp/a_001.mp4'])
    assert content == "file '/tmp/a_000.mp4'\nfile '/tmp/a_001.mp4'\n"


# --- filtergraphs -----------------------------------------------------------

def test_general_filtergraph_shape():
    g = reframe.general_filtergraph(1080, 1920)
    assert '[0:v]split=2[bga][fga];' in g
    assert 'gblur=sigma=12[bg];' in g
    assert 'overlay=x=(W-w)/2:y=(H-h)/2,setsar=1[v]' in g
    # Default ratio drives the foreground height.
    fg_h = int(1920 * reframe.GENERAL_CONTENT_HEIGHT_RATIO)
    assert f"scale=-2:{fg_h + fg_h % 2}," in g.replace('crop', 'crop')


def test_general_filtergraph_full_width_override():
    h = reframe.full_width_content_height(1920, 1080, 1080)
    g = reframe.general_filtergraph(1080, 1920, content_h=h)
    assert f"scale=-2:{h}" in g


def test_full_width_content_height_is_even():
    assert reframe.full_width_content_height(1919, 1079, 1080) % 2 == 0


def test_track_filtergraph_wires_sendcmd_to_crop():
    g = reframe.track_filtergraph("/t/cmd_000.txt", "w=406:h=720:x=0:y=0",
                                  1080, 1920)
    assert "[0:v]sendcmd=f='/t/cmd_000.txt'," in g
    assert "crop@c=w=406:h=720:x=0:y=0," in g
    assert "scale=1080:1920,setsar=1[v]" in g


# --- strategy decision ------------------------------------------------------

def test_strategy_zero_faces_is_general():
    assert reframe.strategy_from_face_counts([]) == 'GENERAL'
    assert reframe.strategy_from_face_counts([0, 0]) == 'GENERAL'


def test_strategy_single_face_is_track():
    assert reframe.strategy_from_face_counts([1, 1, 1]) == 'TRACK'


def test_strategy_group_is_general():
    assert reframe.strategy_from_face_counts([2, 1, 2]) == 'GENERAL'


def test_strategy_boundary_values():
    # avg exactly 0.5 stays TRACK (>= 0.5 is not "< 0.5").
    assert reframe.strategy_from_face_counts([0, 1]) == 'TRACK'
    # avg 1.2 inclusive stays TRACK; just above flips.
    assert reframe.strategy_from_face_counts([1, 1, 1, 1, 1, 2]) == 'TRACK'
    assert reframe.strategy_from_face_counts([1, 1, 1, 1, 2, 2]) == 'GENERAL'


# --- render smoke (needs cv2 + ffmpeg) --------------------------------------

cv2 = pytest.importorskip("cv2", reason="smoke render needs opencv")


@pytest.mark.skipif(_ffmpeg() is None, reason="ffmpeg not on PATH/env")
def test_render_landscape_clip_end_to_end(tmp_path):
    """Full pipeline on a synthetic 3s landscape clip.

    Synthetic footage has no real faces, so this exercises the GENERAL path;
    what is under test is the contract: no exception, vertical output, audio
    carried through from the source."""
    import subprocess

    src = tmp_path / "src.mp4"
    ff = _ffmpeg()
    subprocess.run(
        [ff, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         str(src)], check=True)

    out = tmp_path / "out.mp4"
    assert reframe.render(str(src), str(out), 9 / 16) is True

    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened()
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()

    dw, dh = reframe.delivery_size(640, 360, 9 / 16)
    assert (w, h) == (dw, dh)
    assert abs(frames / fps - 3.0) < 0.2  # duration survives the round trip

    # Audio stream mapped straight from the source.
    probe = subprocess.run(
        [ff, "-i", str(out)], capture_output=True, text=True)
    assert "Audio" in probe.stderr
