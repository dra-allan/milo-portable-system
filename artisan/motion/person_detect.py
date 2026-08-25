"""Person detection fallback for framing.

Vendored + made standalone from openshorts (MIT, github.com/mutonby/
openshorts, main.py::detect_person_yolo). The original reached back into the
app's module globals; here the model and lock are owned locally.

Detects the largest person via YOLO when face detection fails. Returns
[x, y, w, h] of an upper-body/head-region approximation in ORIGINAL frame
coordinates (inference runs on a downscaled copy — detector cost is dominated
by preprocessing, and boxes scale back up linearly).
"""
import threading

DETECT_MAX_WIDTH = 640

_model = None
_model_lock = threading.Lock()
# The YOLO model object is not thread-safe across inference calls.
_infer_lock = threading.Lock()


def _get_model():
    """Lazy-load YOLOv8n on first use; returns None if ultralytics absent."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    from ultralytics import YOLO
                except ImportError:
                    return None
                _model = YOLO('yolov8n.pt')
    return _model


def _detection_frame(frame):
    """Downscale to <= DETECT_MAX_WIDTH for inference; returns (small, scale)."""
    import cv2

    h, w = frame.shape[:2]
    if w <= DETECT_MAX_WIDTH:
        return frame, 1.0
    scale = DETECT_MAX_WIDTH / float(w)
    small_w = max(int(w * scale) // 2 * 2, 2)
    small_h = max(int(h * scale) // 2 * 2, 2)
    small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
    return small, w / float(small_w)


def detect_person(frame):
    """
    Fallback: detect largest person using YOLO when face detection fails.
    Returns [x, y, w, h] of the person's 'upper body' approximation, in
    ORIGINAL frame coordinates, or None when no model/person is found.
    """
    import cv2  # noqa: F401  (frame comes in as a cv2/numpy array)

    model = _get_model()
    if model is None:
        return None

    small, scale = _detection_frame(frame)
    with _infer_lock:
        results = model(small, verbose=False, classes=[0])  # class 0 = person

    if not results:
        return None

    best_box = None
    max_area = 0

    for result in results:
        boxes = result.boxes
        for box in boxes:
            x1, y1, x2, y2 = [int(i * scale) for i in box.xyxy[0]]
            w = x2 - x1
            h = y2 - y1
            area = w * h

            if area > max_area:
                max_area = area
                # Focus on the top 40% of the person (head/chest) for framing.
                # Approximates where the face is when it cannot be seen directly.
                face_h = int(h * 0.4)
                best_box = [x1, y1, w, face_h]

    return best_box
