"""Geometry tests for person-aware framing (src/smart_crop.py).

These target the arithmetic that was actually broken in the original
implementation, so each test corresponds to a real defect:

* ``test_crop_window_stays_inside_source`` -- the original normalised
  source-pixel coordinates by the *output* width (1080), producing fractions
  above 1.0 and crop windows off the right edge of the frame.
* ``test_each_tile_centres_its_own_person`` -- the original averaged every
  detection into one point (nobody) and then used a single crop for all tiles.
* ``test_multi_person_graph_has_one_crop_per_person`` -- the multi-person
  branch computed regions and then discarded them with a plain centre crop.
* ``test_graph_has_no_synthetic_framerate_source`` -- the mosaic base was a
  ``color=...:r=30`` source, which capped the whole render at 30fps.

They run without OpenCV, a camera or a video file, because the geometry is
pure. That is the point of the split: the part that broke is now testable.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import smart_crop as sc  # noqa: E402


W, H = sc.SHORT_WIDTH, sc.SHORT_HEIGHT


# ---------------------------------------------------------------------------
# Row plans / layout shape
# ---------------------------------------------------------------------------
def test_row_plans_match_the_intended_arrangement():
    """Two people stack; three are two-up-one-down; four are a 2x2."""
    assert sc.row_plan(1) == (1,)
    assert sc.row_plan(2) == (1, 1)      # one above, one below
    assert sc.row_plan(3) == (2, 1)      # two up, one down
    assert sc.row_plan(4) == (2, 2)
    assert sc.row_plan(5) == (2, 2, 1)
    assert sc.row_plan(6) == (2, 2, 2)


def test_row_plan_degrades_gracefully_past_the_table():
    """An unexpected head count must not raise mid-render."""
    assert sc.row_plan(7) == (2, 2, 2, 1)
    assert sc.row_plan(8) == (2, 2, 2, 2)
    assert sc.row_plan(0) == (1,)


@pytest.mark.parametrize('count', [1, 2, 3, 4, 5, 6])
def test_cells_tile_the_frame_exactly(count):
    """No gaps, no overlap, no odd dimensions.

    A gap shows the black base canvas through the mosaic; an odd width or
    height makes yuv420p encoding fail outright.
    """
    cells = sc._grid_cells(count, W, H)
    assert len(cells) == count

    # Exact coverage: areas sum to the full frame.
    assert sum(w * h for _, _, w, h in cells) == W * H

    for x, y, w, h in cells:
        assert w % 2 == 0 and h % 2 == 0, "odd dimensions break yuv420p"
        assert x >= 0 and y >= 0
        assert x + w <= W and y + h <= H

    # Pairwise non-overlap.
    for i, (ax, ay, aw, ah) in enumerate(cells):
        for bx, by, bw, bh in cells[i + 1:]:
            overlap_x = max(0, min(ax + aw, bx + bw) - max(ax, bx))
            overlap_y = max(0, min(ay + ah, by + bh) - max(ay, by))
            assert overlap_x * overlap_y == 0


def test_two_people_are_stacked_not_side_by_side():
    """Vertical frame: side-by-side would give each a 540x1920 sliver."""
    top, bottom = sc._grid_cells(2, W, H)
    assert top[2] == W and bottom[2] == W        # both full width
    assert top[1] == 0                           # first is on top
    assert bottom[1] == top[3]                   # second directly below
    assert top[3] + bottom[3] == H


def test_three_people_are_two_up_one_down():
    cells = sc._grid_cells(3, W, H)
    a, b, c = cells
    # Top row: two half-width cells at y=0.
    assert a[1] == 0 and b[1] == 0
    assert a[2] == b[2] == W // 2
    # Bottom row: one full-width cell.
    assert c[2] == W
    assert c[1] == a[3]


# ---------------------------------------------------------------------------
# Crop window arithmetic (the original bug)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('src_w,src_h', [(1920, 1080), (1280, 720), (3840, 2160),
                                         (1080, 1920), (640, 480)])
@pytest.mark.parametrize('cx_frac,cy_frac', [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0),
                                             (0.05, 0.9), (0.95, 0.1)])
def test_crop_window_stays_inside_source(src_w, src_h, cx_frac, cy_frac):
    """THE regression test: a crop window may never leave the frame.

    The original code produced windows at x>src_w for any source wider than
    1080 because it divided source pixels by the output width. Faces at the
    very edge (fracs 0.0/1.0 here) are the exact case that used to fail.
    """
    box = sc.compute_crop_window(src_w * cx_frac, src_h * cy_frac,
                                 9 / 16, src_w, src_h)
    assert box.x >= 0 and box.y >= 0
    assert box.x + box.w <= src_w + 1e-6
    assert box.y + box.h <= src_h + 1e-6
    assert box.w > 0 and box.h > 0


def test_crop_window_matches_requested_aspect():
    """A wrong aspect here means the person is stretched in their tile."""
    for aspect in (9 / 16, 1.0, 16 / 9, 540 / 960, 1080 / 960):
        box = sc.compute_crop_window(960, 540, aspect, 1920, 1080)
        assert box.w / box.h == pytest.approx(aspect, rel=1e-6)


def test_crop_window_is_largest_fit_at_zoom_one():
    """Zoom 1.0 must use all available pixels: no needless upscaling later."""
    box = sc.compute_crop_window(960, 540, 9 / 16, 1920, 1080)
    assert box.h == pytest.approx(1080)          # height-limited
    assert box.w == pytest.approx(1080 * 9 / 16)


def test_zoom_tightens_the_crop():
    wide = sc.compute_crop_window(960, 540, 9 / 16, 1920, 1080, zoom=1.0)
    tight = sc.compute_crop_window(960, 540, 9 / 16, 1920, 1080, zoom=0.6)
    assert tight.w < wide.w and tight.h < wide.h
    assert tight.w / tight.h == pytest.approx(wide.w / wide.h, rel=1e-6)


def test_framing_centre_sits_below_the_face():
    """Centring the face itself crops the body off; real framing looks lower."""
    face = sc.Box(900, 100, 200, 200)
    cx, cy = sc.face_to_framing_center(face, 1920, 1080, headroom=0.55)
    assert cx == pytest.approx(face.cx)
    assert cy > face.cy
    # And it is clamped into the frame.
    low = sc.Box(900, 1000, 200, 200)
    _, cy_low = sc.face_to_framing_center(low, 1920, 1080, headroom=2.0)
    assert cy_low <= 1080


# ---------------------------------------------------------------------------
# Layout planning
# ---------------------------------------------------------------------------
def test_each_tile_centres_its_own_person():
    """Every person must be inside the crop that feeds their own tile.

    The original averaged all detections together, so with two people on
    opposite sides both tiles showed the empty middle. This asserts the
    property that fixes it.
    """
    faces = [sc.Box(200, 300, 160, 160), sc.Box(1600, 320, 160, 160)]
    tiles = sc.plan_layout(faces, 1920, 1080)
    assert len(tiles) == 2

    for face, tile in zip(sorted(faces, key=lambda f: f.cx), tiles):
        crop = tile.crop
        assert crop.x <= face.cx <= crop.x + crop.w, "person outside their crop"
        assert crop.y <= face.cy <= crop.y + crop.h


def test_people_are_ordered_left_to_right():
    """Stable mapping: the left-hand speaker always gets the first cell."""
    faces = [sc.Box(1500, 200, 100, 100), sc.Box(200, 200, 100, 100),
             sc.Box(800, 200, 100, 100)]
    tiles = sc.plan_layout(faces, 1920, 1080)
    centres = [t.crop.cx for t in tiles]
    assert centres == sorted(centres)


def test_layout_respects_max_people():
    faces = [sc.Box(100 + i * 250, 200, 120, 120) for i in range(6)]
    assert len(sc.plan_layout(faces, 1920, 1080, max_people=2)) == 2
    assert len(sc.plan_layout(faces, 1920, 1080, max_people=4)) == 4
    # Never exceeds the table, even if asked for more.
    tiles = sc.plan_layout(faces, 1920, 1080, max_people=99)
    assert len(tiles) <= sc.MAX_LAYOUT_PEOPLE


def test_layout_tiles_match_cell_aspect():
    """Each crop must match its cell's aspect or the person is distorted."""
    faces = [sc.Box(200, 300, 160, 160), sc.Box(1600, 320, 160, 160),
             sc.Box(900, 300, 160, 160)]
    for n in (1, 2, 3):
        tiles = sc.plan_layout(faces[:n], 1920, 1080)
        for tile in tiles:
            assert (tile.crop.w / tile.crop.h) == pytest.approx(
                tile.dest_w / tile.dest_h, rel=1e-3
            )


def test_no_faces_means_no_layout():
    """Empty result signals the caller to fall back, rather than raising."""
    assert sc.plan_layout([], 1920, 1080) == []
    assert sc.build_layout_filters([]) == ([], '')
    assert sc.build_smart_filters.__doc__  # public entry point exists


# ---------------------------------------------------------------------------
# Filter graph construction
# ---------------------------------------------------------------------------
def test_single_person_graph_is_one_crop_and_scale():
    tiles = sc.plan_layout([sc.Box(900, 300, 160, 160)], 1920, 1080)
    filters, label = sc.build_layout_filters(tiles)
    assert label == 'vsmart'
    assert len(filters) == 1
    assert filters[0].startswith('[0:v]crop=')
    assert f"scale={W}:{H}" in filters[0]
    assert 'split' not in filters[0]


@pytest.mark.parametrize('count', [2, 3, 4])
def test_multi_person_graph_has_one_crop_per_person(count):
    """One crop per person, and they are genuinely different windows.

    The old multi-person branch fell back to a single centre crop, so this
    checks both the count and that the windows actually differ.
    """
    faces = [sc.Box(150 + i * 420, 250, 150, 150) for i in range(count)]
    tiles = sc.plan_layout(faces, 1920, 1080)
    filters, label = sc.build_layout_filters(tiles)

    assert label == 'vsmart'
    graph = ';'.join(filters)
    assert graph.count('crop=') == count
    assert f"split={count}" in graph
    # Distinct crop offsets => not one shared window.
    offsets = {(t.crop.as_int_tuple()) for t in tiles}
    assert len(offsets) == count


def test_graph_has_no_synthetic_framerate_source():
    """No ``color=...:r=30`` base -- it capped every render at 30fps.

    It also required ``shortest=1``, which truncated clips. The base layer is
    now the first tile padded to full frame, so the source's own framerate,
    timestamps and duration pass through untouched.
    """
    faces = [sc.Box(200, 300, 150, 150), sc.Box(1500, 300, 150, 150)]
    graph = ';'.join(sc.build_layout_filters(sc.plan_layout(faces, 1920, 1080))[0])
    assert 'color=' not in graph
    assert 'r=30' not in graph
    assert 'shortest' not in graph
    assert 'pad=' in graph          # first tile becomes the full-frame base


def test_graph_covers_every_destination_cell():
    """Overlay positions must equal the planned cell origins."""
    faces = [sc.Box(150 + i * 420, 250, 150, 150) for i in range(4)]
    tiles = sc.plan_layout(faces, 1920, 1080)
    graph = ';'.join(sc.build_layout_filters(tiles)[0])
    for tile in tiles[1:]:
        assert f"overlay={tile.dest_x}:{tile.dest_y}" in graph


def test_graph_uses_the_requested_scaler():
    """Quality knob must reach the filter string, not be silently dropped."""
    tiles = sc.plan_layout([sc.Box(900, 300, 160, 160)], 1920, 1080)
    assert 'flags=lanczos' in sc.build_layout_filters(tiles)[0][0]
    assert 'flags=bicubic' in sc.build_layout_filters(tiles, scaler='bicubic')[0][0]


@pytest.mark.parametrize('count', [1, 2, 3, 4, 5, 6])
def test_graph_labels_are_consistent(count):
    """Every referenced label must be produced exactly once.

    A dangling or duplicated label makes FFmpeg fail at parse time, which in
    the original code only surfaced as a failed render in production.
    """
    import re
    faces = [sc.Box(120 + i * 300, 250, 140, 140) for i in range(count)]
    filters, out_label = sc.build_layout_filters(
        sc.plan_layout(faces, 1920, 1080, max_people=count)
    )
    produced, consumed = [], []
    for stage in filters:
        # Inputs are the leading [..] groups; outputs the trailing ones.
        head = re.match(r'^((?:\[[^\]]+\])+)', stage)
        tail = re.search(r'((?:\[[^\]]+\])+)$', stage)
        if head:
            consumed += re.findall(r'\[([^\]]+)\]', head.group(1))
        if tail:
            produced += re.findall(r'\[([^\]]+)\]', tail.group(1))

    assert out_label in produced
    assert len(produced) == len(set(produced)), "a label is produced twice"
    for label in consumed:
        if label != '0:v':
            assert label in produced, f"label {label} is never produced"


# ---------------------------------------------------------------------------
# Detection helpers (pure parts)
# ---------------------------------------------------------------------------
def test_dedupe_merges_overlapping_detections():
    """Frontal + profile cascades both fire on one turned head."""
    a = sc.Box(100, 100, 200, 200)
    b = sc.Box(110, 105, 190, 195)       # same face, both cascades
    far = sc.Box(1000, 100, 200, 200)
    kept = sc._dedupe([a, b, far])
    assert len(kept) == 2


def test_cluster_tracks_keeps_two_people_apart():
    """The averaging bug: two people must not collapse into one centre."""
    left = sc.Box(200, 300, 150, 150)
    right = sc.Box(1500, 300, 150, 150)
    per_frame = [[left, right]] * 6
    tracks = sc._cluster_tracks(per_frame, 1920, 1080)
    assert len(tracks) == 2
    xs = sorted(box.cx for box, _ in tracks)
    assert xs[0] < 500 and xs[1] > 1400      # no invented middle person


def test_cluster_tracks_counts_presence_for_noise_rejection():
    """A one-frame false positive must be distinguishable by its count."""
    real = sc.Box(300, 300, 150, 150)
    blip = sc.Box(1600, 800, 150, 150)
    per_frame = [[real]] * 8
    per_frame[3] = [real, blip]              # spurious detection in one frame
    tracks = sc._cluster_tracks(per_frame, 1920, 1080)
    counts = sorted(n for _, n in tracks)
    assert counts == [1, 8]
    # With min_presence semantics (>= 34% of 8 frames = 3), the blip loses.
    threshold = max(2, int(round(8 * 0.34)))
    survivors = [b for b, n in tracks if n >= threshold]
    assert len(survivors) == 1


def test_describe_layout_tracks_the_row_plan():
    """Log text is derived, so it can never drift from the geometry."""
    assert 'single' in sc.describe_layout(1)
    assert 'stacked' in sc.describe_layout(2)
    assert sc.describe_layout(3) == '2+1 grid'
    assert sc.describe_layout(4) == '2+2 grid'
