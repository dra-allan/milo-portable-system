"""YOLO person-aware full-frame Shorts cropping.

The output is one real 9:16 crop, never a blurred background or a mosaic. The
source is sampled over the selected clip window. YOLO detects people, temporal
consensus rejects one-frame noise, and the strongest one or two subjects define
the crop. Captions and logos are composited after this stage.

Detection is a framing hint, not a render dependency. Missing ultralytics,
missing weights, a failed model load, or a difficult frame all fall back to a
mathematically correct centre crop.
"""

import os
from pathlib import Path
from typing import List, Optional, Tuple

from .utils import setup_logger

logger = setup_logger(__name__)

try:
    import cv2
except ImportError:  # pragma: no cover - optional runtime dependency
    cv2 = None

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover - optional runtime dependency
    YOLO = None


class Person:
    __slots__ = ('x', 'y', 'w', 'h', 'confidence', 'seen')

    def __init__(self, x: float, y: float, w: float, h: float,
                 confidence: float = 0.0, seen: int = 1):
        self.x, self.y, self.w, self.h = float(x), float(y), float(w), float(h)
        self.confidence, self.seen = float(confidence), int(seen)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def area(self) -> float:
        return self.w * self.h


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _model_path() -> str:
    return os.getenv('YOLO_MODEL_PATH', 'yolov8n.pt').strip() or 'yolov8n.pt'


_MODEL = None
_MODEL_KEY = None


def _load_model():
    """Load YOLO once per process; return None on any optional-runtime failure."""
    global _MODEL, _MODEL_KEY
    if YOLO is None:
        logger.info('YOLO_UNAVAILABLE install ultralytics for person detection')
        return None
    path = _model_path()
    if _MODEL_KEY == path:
        return _MODEL
    try:
        _MODEL = YOLO(path)
        _MODEL_KEY = path
        logger.info('YOLO_READY model=%s', path)
    except Exception as exc:
        _MODEL = None
        _MODEL_KEY = path
        logger.warning('YOLO_LOAD_FAILED model=%s error=%s', path, str(exc)[:180])
    return _MODEL


def _detect_frame(model, frame) -> List[Person]:
    if model is None or frame is None:
        return []
    try:
        result = model.predict(source=frame, classes=[0],
                               conf=_env_float('YOLO_PERSON_CONF', 0.35),
                               imgsz=_env_int('YOLO_IMAGE_SIZE', 640),
                               max_det=_env_int('YOLO_MAX_PERSONS', 8),
                               verbose=False)[0]
        boxes = getattr(result, 'boxes', None)
        if boxes is None or boxes.xyxy is None:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy() if boxes.conf is not None else []
        out = []
        for index, box in enumerate(xyxy):
            x1, y1, x2, y2 = [float(value) for value in box[:4]]
            w, h = x2 - x1, y2 - y1
            if w < 8 or h < 16:
                continue
            confidence = float(confs[index]) if len(confs) > index else 0.0
            out.append(Person(x1, y1, w, h, confidence))
        return out
    except Exception as exc:
        logger.warning('YOLO_FRAME_FAILED error=%s', str(exc)[:160])
        return []


def _match_tracks(tracks: List[Person], detections: List[Person],
                  src_w: int) -> None:
    """Associate detections by centre distance and update running tracks."""
    used = set()
    tolerance = _env_float('YOLO_TRACK_TOLERANCE', 0.18) * max(1, src_w)
    for detection in detections:
        candidates = [(index, track) for index, track in enumerate(tracks)
                      if index not in used]
        if candidates:
            index, track = min(candidates,
                               key=lambda item: ((item[1].cx - detection.cx) ** 2
                                                 + (item[1].cy - detection.cy) ** 2))
            distance = ((track.cx - detection.cx) ** 2
                        + (track.cy - detection.cy) ** 2) ** 0.5
        else:
            index, track, distance = -1, None, float('inf')
        if track is None or distance > tolerance:
            tracks.append(detection)
            continue
        n = track.seen + 1
        track.x += (detection.x - track.x) / n
        track.y += (detection.y - track.y) / n
        track.w += (detection.w - track.w) / n
        track.h += (detection.h - track.h) / n
        track.confidence += (detection.confidence - track.confidence) / n
        track.seen = n
        used.add(index)


def _sample_people(video_path: str, start: float, end: float,
                   samples: int) -> Tuple[List[Person], int, int]:
    if cv2 is None:
        logger.info('YOLO_CROP_FALLBACK reason=opencv_missing')
        return [], 0, 0
    model = _load_model()
    if model is None:
        return [], 0, 0
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning('YOLO_CROP_FALLBACK reason=video_open_failed file=%s',
                       Path(video_path).name)
        return [], 0, 0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    samples = max(3, int(samples))
    tracks: List[Person] = []
    sampled = 0
    try:
        for index in range(samples):
            fraction = (index + 0.5) / samples
            timestamp = float(start) + max(0.1, float(end) - float(start)) * fraction
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            sampled += 1
            if not src_w or not src_h:
                src_h, src_w = frame.shape[:2]
            _match_tracks(tracks, _detect_frame(model, frame), src_w)
    finally:
        cap.release()

    required = max(2, int(round(sampled * _env_float('YOLO_MIN_PRESENCE', 0.34))))
    stable = [track for track in tracks if track.seen >= required]
    stable.sort(key=lambda person: person.area * max(0.1, person.confidence), reverse=True)
    logger.info('YOLO_SAMPLE frames=%d tracks=%d stable=%d required=%d',
                sampled, len(tracks), len(stable), required)
    return stable, src_w, src_h


def _clamp_crop(cx: float, cy: float, src_w: int, src_h: int,
                target_aspect: float, zoom: float = 1.0) -> Tuple[int, int, int, int]:
    """Return an even, in-bounds crop centred on a subject point."""
    src_w, src_h = max(2, int(src_w)), max(2, int(src_h))
    if src_w / src_h > target_aspect:
        crop_h, crop_w = float(src_h), float(src_h) * target_aspect
    else:
        crop_w, crop_h = float(src_w), float(src_w) / target_aspect
    zoom = max(0.65, min(1.0, float(zoom)))
    crop_w, crop_h = crop_w * zoom, crop_h * zoom
    crop_w = min(crop_w, src_w)
    crop_h = min(crop_h, src_h)
    x = max(0.0, min(float(cx) - crop_w / 2, src_w - crop_w))
    y = max(0.0, min(float(cy) - crop_h / 2, src_h - crop_h))
    w = max(2, int(crop_w // 2 * 2))
    h = max(2, int(crop_h // 2 * 2))
    x = max(0, min(int(x // 2 * 2), src_w - w))
    y = max(0, min(int(y // 2 * 2), src_h - h))
    return x, y, w, h


def plan_crop(video_path: str, start: float, end: float,
              target_w: int = 1080, target_h: int = 1920) -> Optional[Tuple[int, int, int, int]]:
    """Plan a full 9:16 crop around one streamer or two people."""
    if not _env_bool('CLIPPER_SMART_CROP', True):
        logger.info('YOLO_CROP_FALLBACK reason=disabled')
        return None
    people, src_w, src_h = _sample_people(
        video_path, start, end, _env_int('YOLO_SAMPLES', 9))
    if not people or not src_w or not src_h:
        logger.info('SMART_CROP_FALLBACK reason=no_stable_person file=%s',
                    Path(video_path).name)
        return None

    subjects = people[:max(1, _env_int('YOLO_MAX_SUBJECTS', 2))]
    left = min(person.x for person in subjects)
    top = min(person.y for person in subjects)
    right = max(person.x + person.w for person in subjects)
    bottom = max(person.y + person.h for person in subjects)
    union_w, union_h = right - left, bottom - top
    cx = (left + right) / 2.0
    cy = top + union_h * 0.42
    zoom = _env_float('YOLO_SINGLE_ZOOM', 0.82) if len(subjects) == 1 else 1.0
    if union_w > src_w * _env_float('YOLO_PAIR_WIDE_RATIO', 0.42):
        zoom = 1.0
    crop = _clamp_crop(cx, cy, src_w, src_h,
                       float(target_w) / float(target_h), zoom=zoom)
    logger.info('YOLO_CROP_APPLIED people=%d source=%dx%d crop=%s',
                len(subjects), src_w, src_h, crop)
    return crop


def centre_crop(src_w: int, src_h: int, target_w: int = 1080,
                target_h: int = 1920) -> Tuple[int, int, int, int]:
    return _clamp_crop(src_w / 2.0, src_h / 2.0, src_w, src_h,
                       float(target_w) / float(target_h), zoom=1.0)
