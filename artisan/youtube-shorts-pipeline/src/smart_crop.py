"""Person-aware framing for vertical Shorts: detect people, centre them.

WHY THE OLD SMART MODE NEVER WORKED
-----------------------------------
Four separate defects, each sufficient on its own to break the feature:

1. **Wrong coordinate space.** ``cv2`` returns face rectangles in *source*
   pixels (e.g. x=1400 in a 1920x1080 frame), but ``_build_smart_background_filters``
   normalised them by the *output* size::

       norm_x = x / width      # width = 1080, the OUTPUT width

   For any source wider than 1080 that yields fractions above 1.0 -- i.e. a
   crop window located off the right-hand edge of the frame. The subsequent
   ``crop=`` either clamped to a corner or failed outright.

2. **Averaging positions of different people.** Detections from all sample
   frames were pooled and arithmetically averaged, so two people standing on
   opposite sides produced a "person" in the empty space between them. With
   three people it converged on the centroid, which is nobody.

3. **The multi-person branch was a stub.** It computed ``left_region`` /
   ``right_region``, logged "detected multiple people", then executed
   ``return build_background_filters('crop', ...)`` -- discarding the regions
   and falling back to a plain centre crop. The grid layout was never built.

4. **No false-positive rejection.** Haar cascades routinely fire on
   patterned backgrounds. A single spurious detection in one sampled frame was
   enough to change the layout for the whole clip.

WHAT THIS MODULE DOES
---------------------
* Detects faces per sampled frame and keeps everything in source pixels until
  the final filter string.
* **Tracks** detections into persistent clusters across frames, so a person is
  identified by consistent presence rather than by one lucky frame. Clusters
  seen in fewer than ``min_presence`` of the frames are discarded as noise.
* Builds a layout: 1 person -> single centred crop; 2 -> stacked halves;
  3-4 -> 2x2 grid. **Each tile gets its own crop window, centred on its own
  person**, computed in source coordinates and clamped to the frame.
* Falls back to a plain centre crop whenever detection is unavailable or
  unconvincing, so the render never fails because of this stage.

The geometry is separated from OpenCV entirely (``compute_crop_window``,
``plan_layout``, ``build_layout_filters``) so the arithmetic that actually
broke is unit-testable without a video file or a working camera stack.
"""

import os
from typing import Dict, List, Optional, Sequence, Tuple

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger

logger = setup_logger(__name__)

try:
    import cv2
    OPENCV_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore
    OPENCV_AVAILABLE = False


SHORT_WIDTH = 1080
SHORT_HEIGHT = 1920


# ---------------------------------------------------------------------------
# Geometry (pure, testable)
# ---------------------------------------------------------------------------
class Box:
    """An axis-aligned rectangle in source-pixel coordinates."""

    __slots__ = ('x', 'y', 'w', 'h')

    def __init__(self, x: float, y: float, w: float, h: float):
        self.x = float(x)
        self.y = float(y)
        self.w = float(w)
        self.h = float(h)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    def as_int_tuple(self) -> Tuple[int, int, int, int]:
        return int(round(self.x)), int(round(self.y)), int(round(self.w)), int(round(self.h))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Box({self.x:.0f},{self.y:.0f},{self.w:.0f}x{self.h:.0f})"


def compute_crop_window(center_x: float, center_y: float, target_aspect: float,
                        src_w: int, src_h: int, zoom: float = 1.0) -> Box:
    """Largest ``target_aspect`` window that fits the source, centred on a point.

    ``target_aspect`` is width/height of the *destination tile*, so the crop is
    never distorted when it is scaled into that tile.

    ``zoom`` < 1.0 tightens the crop (a closer shot). The window is then
    translated so its centre is the requested point and **clamped inside the
    frame** -- the step whose absence produced off-frame crop windows before.
    Clamping rather than rejecting matters: a face near the edge is common and
    should yield a valid edge-aligned crop, not a fallback.
    """
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    target_aspect = max(1e-6, float(target_aspect))

    # Fit the aspect box inside the source.
    if src_w / src_h > target_aspect:
        h = float(src_h)
        w = h * target_aspect
    else:
        w = float(src_w)
        h = w / target_aspect

    zoom = min(1.0, max(0.25, float(zoom)))
    w *= zoom
    h *= zoom

    # Never exceed the source.
    if w > src_w:
        w = float(src_w)
        h = w / target_aspect
    if h > src_h:
        h = float(src_h)
        w = h * target_aspect

    x = center_x - w / 2.0
    y = center_y - h / 2.0
    # Clamp so the window stays fully inside the frame.
    x = max(0.0, min(x, src_w - w))
    y = max(0.0, min(y, src_h - h))
    return Box(x, y, w, h)


def _even(value: float, minimum: int = 2) -> int:
    """Round to an even integer -- yuv420p requires even width/height."""
    v = int(round(value / 2.0)) * 2
    return max(minimum, v)


def face_to_framing_center(face: Box, src_w: int, src_h: int,
                           headroom: float = 0.55) -> Tuple[float, float]:
    """Convert a face box into the point a camera operator would centre on.

    Centring the *face* puts it dead centre and crops the body off. Real
    framing places the face in the upper third, so the centre of interest sits
    below the face by a fraction of a head height. ``headroom`` is that offset
    in face-heights.
    """
    cx = face.cx
    cy = face.cy + face.h * float(headroom)
    cy = max(0.0, min(cy, float(src_h)))
    cx = max(0.0, min(cx, float(src_w)))
    return cx, cy


# ---------------------------------------------------------------------------
# Layout planning
# ---------------------------------------------------------------------------
class Tile:
    """One destination cell plus the source window that feeds it."""

    __slots__ = ('dest_x', 'dest_y', 'dest_w', 'dest_h', 'crop')

    def __init__(self, dest_x: int, dest_y: int, dest_w: int, dest_h: int, crop: Box):
        self.dest_x = int(dest_x)
        self.dest_y = int(dest_y)
        self.dest_w = int(dest_w)
        self.dest_h = int(dest_h)
        self.crop = crop

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"Tile(dest={self.dest_w}x{self.dest_h}@{self.dest_x},{self.dest_y}"
                f" crop={self.crop})")


def _grid_cells(count: int, width: int, height: int) -> List[Tuple[int, int, int, int]]:
    """Destination cells for ``count`` people in a 1080x1920 frame.

    * 1 -> the whole frame.
    * 2 -> stacked halves (the classic vertical split-screen for a two-person
      conversation; side-by-side would give each person a 540x1920 sliver).
    * 3 -> one full-width row on top, two side-by-side beneath.
    * 4 -> 2x2.
    """
    if count <= 1:
        return [(0, 0, width, height)]
    if count == 2:
        half = _even(height / 2)
        return [(0, 0, width, half), (0, half, width, height - half)]
    if count == 3:
        top = _even(height / 2)
        half_w = _even(width / 2)
        return [
            (0, 0, width, top),
            (0, top, half_w, height - top),
            (half_w, top, width - half_w, height - top),
        ]
    half_w = _even(width / 2)
    half_h = _even(height / 2)
    return [
        (0, 0, half_w, half_h),
        (half_w, 0, width - half_w, half_h),
        (0, half_h, half_w, height - half_h),
        (half_w, half_h, width - half_w, height - half_h),
    ]


def plan_layout(faces: Sequence[Box], src_w: int, src_h: int,
                width: int = SHORT_WIDTH, height: int = SHORT_HEIGHT,
                zoom: float = 1.0, headroom: float = 0.55,
                max_people: int = 4) -> List[Tile]:
    """Assign each detected person a destination cell and a centred crop.

    People are ordered left-to-right by their position in the source so the
    on-screen arrangement matches the real scene -- otherwise two speakers can
    swap places between clips, which is disorienting.

    Returns [] when there is nothing usable, letting the caller fall back.
    """
    if not faces:
        return []

    people = sorted(faces, key=lambda f: f.cx)[:max(1, int(max_people))]
    cells = _grid_cells(len(people), width, height)

    tiles: List[Tile] = []
    for face, (dx, dy, dw, dh) in zip(people, cells):
        cx, cy = face_to_framing_center(face, src_w, src_h, headroom=headroom)
        crop = compute_crop_window(cx, cy, dw / float(dh), src_w, src_h, zoom=zoom)
        tiles.append(Tile(dx, dy, dw, dh, crop))
    return tiles


def build_layout_filters(tiles: Sequence[Tile], width: int = SHORT_WIDTH,
                         height: int = SHORT_HEIGHT) -> Tuple[List[str], str]:
    """Turn tiles into an FFmpeg filter graph, returning (filters, out_label).

    Implemented as a black base canvas plus one ``overlay`` per tile rather
    than ``vstack``/``xstack``. Overlay is explicit about destination position,
    so a tile whose rounded size is a pixel off cannot shift the whole mosaic
    -- stack filters reject mismatched inputs outright.
    """
    if not tiles:
        return [], ''

    filters: List[str] = []
    n = len(tiles)

    if n == 1:
        tile = tiles[0]
        x, y, w, h = tile.crop.as_int_tuple()
        w, h = _even(w), _even(h)
        filters.append(
            f"[0:v]crop={w}:{h}:{x}:{y},"
            f"scale={width}:{height}:flags=bicubic,setsar=1[vsmart]"
        )
        return filters, 'vsmart'

    # Split the source once per tile; each branch crops its own region.
    filters.append(f"[0:v]split={n}" + ''.join(f"[s{i}]" for i in range(n)))
    for i, tile in enumerate(tiles):
        x, y, w, h = tile.crop.as_int_tuple()
        w, h = _even(w), _even(h)
        filters.append(
            f"[s{i}]crop={w}:{h}:{x}:{y},"
            f"scale={tile.dest_w}:{tile.dest_h}:flags=bicubic,setsar=1[t{i}]"
        )

    filters.append(
        f"color=c=black:s={width}x{height}:r=30,format=yuv420p[canvas0]"
    )
    current = 'canvas0'
    for i, tile in enumerate(tiles):
        nxt = f"canvas{i + 1}" if i < len(tiles) - 1 else 'vsmart'
        # shortest=1 stops the synthetic colour source from extending the clip.
        filters.append(
            f"[{current}][t{i}]overlay={tile.dest_x}:{tile.dest_y}"
            f":shortest=1[{nxt}]"
        )
        current = nxt
    return filters, 'vsmart'


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def _iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def _dedupe(boxes: List[Box], iou_threshold: float = 0.35) -> List[Box]:
    """Suppress overlapping detections, keeping the largest.

    The frontal and profile cascades both fire on a head turned partway, so
    without this a single person is counted twice and triggers a split screen.
    """
    kept: List[Box] = []
    for box in sorted(boxes, key=lambda b: b.w * b.h, reverse=True):
        if all(_iou(box, k) < iou_threshold for k in kept):
            kept.append(box)
    return kept


def _load_cascades() -> List:
    """Load available Haar cascades, tolerating a partial OpenCV install."""
    if not OPENCV_AVAILABLE or not hasattr(cv2, 'CascadeClassifier'):
        return []
    base = getattr(getattr(cv2, 'data', None), 'haarcascades', None)
    if not base:
        return []
    cascades = []
    for name in ('haarcascade_frontalface_default.xml',
                 'haarcascade_profileface.xml'):
        path = os.path.join(base, name)
        if not os.path.exists(path):
            continue
        try:
            clf = cv2.CascadeClassifier(path)
            if not clf.empty():
                cascades.append(clf)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Could not load cascade %s: %s", name, exc)
    return cascades


def detect_faces_in_frame(frame, cascades: Sequence, min_size_ratio: float = 0.045
                          ) -> List[Box]:
    """Detect faces in one BGR frame, in source pixels.

    Detection runs on a frame downscaled to ~640px wide (Haar cost grows with
    area, and a 1080p frame is ~3x the pixels for no extra accuracy), then the
    boxes are scaled back up. ``min_size_ratio`` rejects tiny detections, which
    are overwhelmingly background texture rather than people.
    """
    if frame is None or not cascades:
        return []

    src_h, src_w = frame.shape[:2]
    scale = 1.0
    work = frame
    if src_w > 640:
        scale = 640.0 / float(src_w)
        work = cv2.resize(frame, (640, max(1, int(round(src_h * scale)))),
                          interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)      # helps a lot on dark/backlit footage

    min_side = max(24, int(round(min(gray.shape[:2]) * float(min_size_ratio))))
    found: List[Box] = []
    for clf in cascades:
        try:
            rects = clf.detectMultiScale(
                gray, scaleFactor=1.15, minNeighbors=6,
                minSize=(min_side, min_side),
                flags=getattr(cv2, 'CASCADE_SCALE_IMAGE', 0),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("detectMultiScale failed: %s", exc)
            continue
        for (x, y, w, h) in rects:
            found.append(Box(x / scale, y / scale, w / scale, h / scale))

    return _dedupe(found)


def _cluster_tracks(per_frame: Sequence[List[Box]], src_w: int, src_h: int,
                    tol: float = 0.16) -> List[Tuple[Box, int]]:
    """Group per-frame detections into persistent people.

    This replaces the old "average every detection together" step, which
    invented a person in the gap between two real ones. Detections are matched
    to a track when their centre is within ``tol`` (as a fraction of frame
    width) of the track's running mean; each track then reports its mean box
    and how many frames it appeared in.

    Returns [(mean_box, frames_seen)], largest-presence first.
    """
    diag = float(max(1, src_w))
    tracks: List[Dict] = []

    for frame_boxes in per_frame:
        used = set()
        for box in frame_boxes:
            best, best_dist = None, None
            for ti, track in enumerate(tracks):
                if ti in used:
                    continue           # one detection per track per frame
                dx = (track['cx'] - box.cx) / diag
                dy = (track['cy'] - box.cy) / diag
                dist = (dx * dx + dy * dy) ** 0.5
                if dist <= tol and (best_dist is None or dist < best_dist):
                    best, best_dist = ti, dist
            if best is None:
                tracks.append({
                    'cx': box.cx, 'cy': box.cy,
                    'w': box.w, 'h': box.h, 'n': 1,
                })
            else:
                t = tracks[best]
                n = t['n'] + 1
                # Running mean: stable against one noisy frame.
                t['cx'] += (box.cx - t['cx']) / n
                t['cy'] += (box.cy - t['cy']) / n
                t['w'] += (box.w - t['w']) / n
                t['h'] += (box.h - t['h']) / n
                t['n'] = n
                used.add(best)

    out: List[Tuple[Box, int]] = []
    for t in tracks:
        box = Box(t['cx'] - t['w'] / 2.0, t['cy'] - t['h'] / 2.0, t['w'], t['h'])
        out.append((box, int(t['n'])))
    out.sort(key=lambda pair: pair[1], reverse=True)
    return out


def analyse_people(video_path: str, start_time: float, end_time: float,
                   samples: int = 9, min_presence: float = 0.34,
                   max_people: int = 4) -> Tuple[List[Box], int, int]:
    """Sample the clip and return (people, src_w, src_h) in source pixels.

    ``min_presence`` is the fraction of sampled frames a track must appear in
    to count as a person. This is the false-positive filter that was missing:
    Haar cascades fire on background texture in isolated frames, and without a
    persistence requirement one such frame changed the layout for the whole
    clip.
    """
    if not OPENCV_AVAILABLE:
        logger.info("OpenCV not installed; smart framing unavailable")
        return [], 0, 0

    cascades = _load_cascades()
    if not cascades:
        logger.warning("No Haar cascade data found in this OpenCV install; "
                       "smart framing unavailable")
        return [], 0, 0

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.error("Could not open %s for person detection", video_path)
        return [], 0, 0

    try:
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = max(0.0, float(end_time) - float(start_time))
        samples = max(3, int(samples))

        per_frame: List[List[Box]] = []
        for i in range(samples):
            # Sample strictly inside the clip: the first and last frames are
            # often a cut or a transition, which detects poorly.
            frac = (i + 0.5) / samples
            ts = float(start_time) + duration * frac
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            if not src_w or not src_h:
                src_h, src_w = frame.shape[:2]
            per_frame.append(detect_faces_in_frame(frame, cascades))
    finally:
        cap.release()

    if not per_frame or not src_w or not src_h:
        logger.info("Smart framing: no frames could be sampled")
        return [], src_w, src_h

    tracks = _cluster_tracks(per_frame, src_w, src_h)
    frames = len(per_frame)
    threshold = max(2, int(round(frames * float(min_presence))))
    people = [box for box, seen in tracks if seen >= threshold][:max(1, int(max_people))]

    logger.info(
        "Smart framing: %d frame(s) sampled, %d track(s), %d person(s) kept "
        "(needed presence in %d/%d frames)",
        frames, len(tracks), len(people), threshold, frames,
    )
    return people, src_w, src_h


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def build_smart_filters(video_path: str, start_time: float, end_time: float,
                        width: int = SHORT_WIDTH, height: int = SHORT_HEIGHT,
                        zoom: float = 1.0, headroom: float = 0.55,
                        samples: int = 9, max_people: int = 4,
                        min_presence: float = 0.34
                        ) -> Optional[Tuple[List[str], str, int]]:
    """Build the smart-framing filter graph for a clip.

    Returns ``(filters, output_label, people_count)``, or None when detection
    found nobody -- in which case the caller should use a normal backdrop mode.
    """
    people, src_w, src_h = analyse_people(
        video_path, start_time, end_time, samples=samples,
        min_presence=min_presence, max_people=max_people,
    )
    if not people or not src_w or not src_h:
        return None

    tiles = plan_layout(people, src_w, src_h, width=width, height=height,
                        zoom=zoom, headroom=headroom, max_people=max_people)
    if not tiles:
        return None

    filters, label = build_layout_filters(tiles, width=width, height=height)
    if not filters:
        return None

    layout = {1: 'single centred crop', 2: 'stacked split-screen',
              3: '1-over-2 grid', 4: '2x2 grid'}.get(len(tiles), 'grid')
    logger.info(
        "Smart framing: %d person(s) -> %s (source %dx%d)",
        len(tiles), layout, src_w, src_h,
    )
    for i, tile in enumerate(tiles, start=1):
        x, y, w, h = tile.crop.as_int_tuple()
        logger.debug(
            "  person %d: crop %dx%d at (%d,%d) -> tile %dx%d at (%d,%d)",
            i, w, h, x, y, tile.dest_w, tile.dest_h, tile.dest_x, tile.dest_y,
        )
    return filters, label, len(tiles)
