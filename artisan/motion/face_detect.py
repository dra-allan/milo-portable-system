"""Face candidate detection for the motion package.

Vendored from openshorts main.py (MIT) on 2026-08-25: detect_face_candidates
plus its downscaled-inference helper, de-globalized.

Adaptation: openshorts hard-requires MediaPipe. This environment does not
have it, and the self-contained rule forbids reaching outside the repo for a
replacement — so the backend is chosen lazily at first use:

  1. ``mediapipe`` (upstream behaviour, relative-coord boxes) when importable
  2. YuNet via ``cv2.FaceDetectorYN`` otherwise — a small DNN detector whose
     ~230KB ONNX model is VENDORED in ``models/`` (Apache-2.0, from
     opencv/opencv_zoo), so no machine ever downloads or installs anything.
     Chosen over Haar cascades because cv2 >= 5.0 removed CascadeClassifier
     and stopped shipping cascade files entirely.

Both backends return candidates in ORIGINAL frame coordinates as
``{'box': [x, y, w, h], 'score': w * h}`` — the contract SpeakerTracker
consumes. Score is face area, exactly like upstream (YuNet's own confidence
is applied as the detection threshold, not as the score).
"""
import os
import threading

DETECT_MAX_WIDTH = 640

# YuNet detection confidence; mediapipe upstream used 0.5.
DETECT_CONFIDENCE = float(os.environ.get("MOTION_FACE_CONFIDENCE", "0.5"))

# The MediaPipe graph / YuNet model are NOT thread-safe; clips render in
# parallel, so every inference goes through this lock. Contention is small
# (a few ms per call) — the ffmpeg renders are where parallel time goes.
DETECT_LOCK = threading.Lock()

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_YUNET_PATH = os.path.join(_MODELS_DIR, "face_detection_yunet_2023mar.onnx")

_mp_face_detection = None
_yunet = None
_BACKEND = None


def _detection_frame(frame):
    """Downscaled copy for detectors. Returns (small_frame, scale) with
    scale mapping small-frame pixel coords back to the original frame."""
    import cv2
    h, w = frame.shape[:2]
    if w <= DETECT_MAX_WIDTH:
        return frame, 1.0
    scale = w / DETECT_MAX_WIDTH
    small = cv2.resize(frame, (DETECT_MAX_WIDTH, max(int(h / scale), 2)),
                       interpolation=cv2.INTER_AREA)
    return small, scale


def _init_backend():
    global _mp_face_detection, _yunet, _BACKEND
    if _BACKEND is not None:
        return
    try:
        import mediapipe as mp
        _mp_face_detection = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5)
        _BACKEND = 'mediapipe'
        return
    except ImportError:
        pass
    if not os.path.exists(_YUNET_PATH):
        raise RuntimeError(
            f"YuNet model missing at {_YUNET_PATH} and mediapipe is not "
            "installed — face tracking unavailable")
    import cv2
    # Input size is set per frame in detect(); constructor size is a dummy.
    _yunet = cv2.FaceDetectorYN_create(
        _YUNET_PATH, "", (320, 320), score_threshold=DETECT_CONFIDENCE)
    _BACKEND = 'yunet'
    print("   [motion] mediapipe unavailable — face detection on YuNet "
          "(vendored ONNX)")


def detect_face_candidates(frame):
    """All faces in ``frame`` (BGR ndarray), original-frame coordinates."""
    _init_backend()
    height, width = frame.shape[:2]
    with DETECT_LOCK:
        if _BACKEND == 'mediapipe':
            return _detect_mediapipe(frame, width, height)
        return _detect_yunet(frame, width, height)


def _detect_mediapipe(frame, width, height):
    import cv2
    small, _scale = _detection_frame(frame)
    rgb_frame = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    results = _mp_face_detection.process(rgb_frame)

    candidates = []
    if not results.detections:
        return []

    # MediaPipe reports RELATIVE coords against the frame it was given, so
    # they map exactly onto the original regardless of our downscale.
    for detection in results.detections:
        bboxC = detection.location_data.relative_bounding_box
        x = int(bboxC.xmin * width)
        y = int(bboxC.ymin * height)
        w = int(bboxC.width * width)
        h = int(bboxC.height * height)
        candidates.append({'box': [x, y, w, h], 'score': w * h})
    return candidates


def _detect_yunet(frame, width, height):
    import cv2
    small, scale = _detection_frame(frame)
    _yunet.setInputSize((small.shape[1], small.shape[0]))
    _ret, faces = _yunet.detect(small)

    candidates = []
    if faces is None:
        return []
    for face in faces:
        x, y, w, h = [int(v * scale) for v in face[:4]]
        candidates.append({'box': [x, y, w, h], 'score': w * h})
    return candidates
