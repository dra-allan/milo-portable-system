"""Scene detection engines for the reframe pipeline.

Vendored from openshorts scene_detection.py (MIT) on 2026-08-25.
Deviations from upstream, both required by this environment:

  * ``scenedetect`` imports moved inside the legacy function so the module
    loads (and the TransNetV2 engine stays reachable) without that package.
  * a last-resort SINGLE-SCENE fallback: upstream raises when every engine
    fails, but here the unit of work is a short clip window, not an hour-long
    source, so treating the whole clip as one scene is a usable answer and
    lets the strategy classifier decide TRACK vs GENERAL for it.

Primary engine: TransNetV2, a small neural shot-boundary detector that is
markedly more accurate than threshold-based detection (handles fast camera
motion, flashes and gradual transitions such as fades/dissolves). Runs on
48x27 frames, faster than realtime even on CPU.

Fallback/legacy engine: PySceneDetect ContentDetector, byte-for-byte the
pre-existing behavior. Any TransNetV2 failure (missing package, corrupt
weights, decode error) falls back automatically so a scene-detection edge
case can never kill a job.

Environment variables:
  SCENE_ENGINE          "transnetv2" (default) | "pyscenedetect" (legacy)
  SCENE_MIN_SEC         minimum scene length in seconds; shorter scenes are
                        merged into a neighbor (default 0.4, TransNetV2 path
                        only — the legacy path stays untouched)
  TRANSNETV2_THRESHOLD  shot-boundary probability threshold (default 0.5)
  TRANSNETV2_DEVICE     torch device: "auto" (default) | "cpu" | "cuda" | "mps"
"""

import os
import subprocess
import threading

_TN2_W, _TN2_H = 48, 27

# One shared model instance; clips can process in parallel so inference is
# serialized like the other detectors in this package.
_TN2_LOCK = threading.Lock()
_tn2_model = None


def detect_scenes(video_path):
    """Detect scenes. Returns (scene_list, fps) where scene_list is a list of
    (start_frame, end_frame) integer pairs with EXCLUSIVE ends.

    Upstream handed back PySceneDetect FrameTimecode pairs; frames are the
    only thing every consumer here actually reads, so the contract is
    narrowed to plain ints (documented adaptation).
    """
    engine = os.environ.get("SCENE_ENGINE", "transnetv2").strip().lower()
    if engine != "pyscenedetect":
        try:
            return _detect_transnetv2(video_path)
        except Exception as e:
            print(f"   [motion] TransNetV2 scene detection failed "
                  f"({type(e).__name__}: {e}) — trying PySceneDetect")
    try:
        return _detect_pyscenedetect(video_path)
    except Exception as e:
        print(f"   [motion] PySceneDetect unavailable/failed "
              f"({type(e).__name__}: {e}) — treating clip as one scene")
    return _detect_single_scene(video_path)


# --- single-scene fallback (local addition) ---------------------------------

def _probe_fps_total(video_path):
    import cv2
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video file {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return float(fps), max(total, 1)


def _detect_single_scene(video_path):
    fps, total = _probe_fps_total(video_path)
    print(f"   [motion] single-scene fallback — {total} frames @ {fps:.2f}fps")
    return [(0, total)], fps


# --- legacy engine ----------------------------------------------------------

def _detect_pyscenedetect(video_path):
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector())
    scene_manager.detect_scenes(video=video)
    scene_list = scene_manager.get_scene_list()
    fps = float(video.frame_rate)
    return [(s.get_frames(), e.get_frames()) for s, e in scene_list], fps


# --- TransNetV2 engine ------------------------------------------------------

def _get_tn2_model():
    global _tn2_model
    if _tn2_model is None:
        from transnetv2_pytorch import TransNetV2
        device = os.environ.get("TRANSNETV2_DEVICE", "auto")
        model = TransNetV2(device=device)
        model.eval()
        _tn2_model = model
    return _tn2_model


def _extract_frames_small(video_path):
    """Decode the whole clip as 48x27 RGB frames via ffmpeg (~4KB/frame)."""
    cmd = [
        ffmpeg_binary(), "-nostdin", "-i", video_path,
        "-vf", f"scale={_TN2_W}:{_TN2_H}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, check=True, timeout=900)
    frame_bytes = _TN2_H * _TN2_W * 3
    n = len(proc.stdout) // frame_bytes
    if n == 0:
        raise RuntimeError("ffmpeg produced no frames")
    import numpy as np
    return np.frombuffer(proc.stdout[:n * frame_bytes],
                         dtype=np.uint8).reshape(n, _TN2_H, _TN2_W, 3)


def _detect_transnetv2(video_path):
    import torch

    fps, total_frames = _probe_fps_total(video_path)

    frames = _extract_frames_small(video_path)
    model = _get_tn2_model()
    threshold = float(os.environ.get("TRANSNETV2_THRESHOLD", "0.5"))

    with _TN2_LOCK, torch.no_grad():
        tensor = torch.from_numpy(np.ascontiguousarray(frames)).to(model.device)
        single_frame_pred, _ = model.predict_frames(tensor, quiet=True)

    # predictions_to_scenes returns [[start, end], ...] with INCLUSIVE ends;
    # downstream expects exclusive ends.
    raw = model.predictions_to_scenes(single_frame_pred.numpy(), threshold=threshold)
    bounds = [(int(s), int(e) + 1) for s, e in raw]
    if not bounds:
        raise RuntimeError("TransNetV2 produced no scene boundaries")

    # The probed frame count can differ by a few frames from what ffmpeg
    # decodes; downstream loops run to the decoder's count, so cover the gap.
    if total_frames > bounds[-1][1]:
        bounds[-1] = (bounds[-1][0], total_frames)

    min_sec = float(os.environ.get("SCENE_MIN_SEC", "0.4"))
    bounds = _merge_short_scenes(bounds, fps, min_sec)

    scene_list = bounds
    print(f"   [motion] Scene engine: TransNetV2 — {len(scene_list)} scenes")
    return scene_list, fps


def _merge_short_scenes(bounds, fps, min_sec):
    """Absorb scenes shorter than min_sec into a neighbor. Ultra-short scenes
    cause camera-snap bursts and starve the per-scene strategy sampling."""
    if len(bounds) <= 1 or min_sec <= 0:
        return bounds
    min_frames = max(1, int(round(min_sec * float(fps))))

    merged = []
    for s, e in bounds:
        if merged and (e - s) < min_frames:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    # The first scene can still be short — fold it into the one after it.
    if len(merged) > 1 and (merged[0][1] - merged[0][0]) < min_frames:
        merged[1] = (merged[0][0], merged[1][1])
        merged.pop(0)
    return merged


def ffmpeg_binary():
    """FFmpeg executable: MILO_FFMPEG override, else PATH."""
    return os.environ.get("MILO_FFMPEG") or "ffmpeg"
