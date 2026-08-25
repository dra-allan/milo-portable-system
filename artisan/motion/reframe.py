"""Reframe engine: analyze in Python, render natively in ffmpeg.

Vendored from openshorts reframe_v2.py (MIT) on 2026-08-25, per the
self-contained rule. Splits a vertical reframing of one clip into:

  1. ANALYSIS — one ffmpeg-decoded pass at <=640px feeding the face/person
     detectors and the SmoothedCameraman/SpeakerTracker state machines,
     producing the camera trajectory (crop x per frame).
  2. RENDER — one ffmpeg process per scene doing decode -> dynamic crop
     (sendcmd) -> scale -> encode natively (TRACK scenes), or the blurred
     background filtergraph (GENERAL scenes); segments are then concatenated
     with stream copy and the audio mapped straight from the source clip.

No raw-frame piping, no second full-res decode, one less intermediate encode.
Callers must treat any exception as "fall back to their static crop".

Deviations from upstream:
  * SPLIT / SCREENCAST / WIDE / INSET / ALTERNATE layouts are NOT here —
    their modules (split_layout, screencast_layout, active_speaker) are not
    vendored yet; this file renders TRACK + GENERAL (+ punch-in on beats).
    ``scene_frame_ranges`` still carries strategies so those layouts slot in
    without reshaping the render loop.
  * scene detection lives in sibling ``scene_detection`` (int-frame contract);
    face detection in sibling ``face_detect`` (mediapipe-or-Haar backends).
  * intermediate segments encode libx264 veryfast crf20 — they are re-encoded
    by the caller's final pass anyway, so hardware encoders buy nothing here.
  * prints use plain ``[motion]`` prefixes instead of emoji.

Pure helpers (sendcmd/concat generation, scene slicing, delivery sizing)
have no heavy imports so they stay unit-testable without cv2/numpy.
"""
import os
import shutil
import subprocess
import tempfile

from scene_detection import detect_scenes, _probe_fps_total, ffmpeg_binary
from face_detect import detect_face_candidates
from person_detect import detect_person
import punch_in
from cameraman import SmoothedCameraman
from speaker_tracker import SpeakerTracker

ANALYSIS_MAX_WIDTH = 640

# Short-form platforms expect a 1080-wide vertical upload; anything smaller is
# treated as low quality and re-encoded from the already-soft source. The crop
# region is whatever the source height allows, so a 720p input yields a
# 406x720 crop — we scale that up to the delivery floor rather than shipping
# sub-HD. Sources that already exceed it are left alone (never downscale
# quality the user supplied).
DELIVERY_MIN_WIDTH = 1080

# Detect every Nth frame; SmoothedCameraman interpolates between updates.
# YOLO fallback (no face found) is far heavier than face detection.
DETECT_STRIDE = max(int(os.environ.get("DETECT_STRIDE", "4")), 1)
YOLO_FALLBACK_STRIDE = DETECT_STRIDE * 2


def run_ffmpeg(cmd, timeout=1800):
    subprocess.run([ffmpeg_binary(), "-loglevel", "error", *cmd], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   timeout=timeout)


# --- pure helpers (CI-testable) ---------------------------------------------

def delivery_size(orig_w, orig_h, aspect_ratio):
    """Output (width, height) for a reframe of this source.

    Picks the largest crop the source allows, then upscales to
    ``DELIVERY_MIN_WIDTH`` if that crop is narrower. Both dimensions come back
    even (x264/NVENC reject odd ones).
    """
    out_h = orig_h
    out_w = int(out_h * aspect_ratio)
    if out_w > orig_w:
        out_w = orig_w
        out_h = int(out_w / aspect_ratio)

    if out_w < DELIVERY_MIN_WIDTH:
        out_w = DELIVERY_MIN_WIDTH
        out_h = int(round(out_w / aspect_ratio))

    return out_w + (out_w % 2), out_h + (out_h % 2)


def dedupe_sendcmd_lines(xs, fps, target="crop@c"):
    """sendcmd lines setting crop x per frame, deduped to change-points.

    Timestamps are relative to the segment (the render seeks per scene).
    """
    lines = []
    prev = None
    for i, x in enumerate(xs):
        if x != prev:
            lines.append(f"{i / fps:.4f} {target} x {x};")
            prev = x
    return lines


def scene_frame_ranges(scene_boundaries, strategies, total_frames):
    """Clamp scene (start, end) frame ranges to the decoded frame count,
    dropping empty ranges. Each range keeps its strategy so later indices
    can't misalign when a range is dropped."""
    ranges = []
    for i, (start_f, end_f) in enumerate(scene_boundaries):
        strategy = strategies[i] if i < len(strategies) else 'TRACK'
        start_f = max(0, min(start_f, total_frames))
        end_f = max(start_f, min(end_f, total_frames))
        if end_f > start_f:
            ranges.append((start_f, end_f, strategy))
    return ranges


def concat_list_content(segment_paths):
    # Single quotes per concat-demuxer spec; our paths are tempfile-generated
    # (no quotes in them).
    return "".join(f"file '{p}'\n" for p in segment_paths)


# How much of the frame height the real content should fill in GENERAL layout.
#
# Fitting a 16:9 source to the full output width leaves it 608px tall in a
# 1920px frame — the content is 32% of the screen and 68% is blurred filler.
# Scaling the content up and letting the sides overflow trades width for
# presence, and the trade has to stay conservative: GENERAL is chosen for group
# shots and landscapes, exactly the material where cropping the sides cuts
# someone out of frame. At 0.42 a 16:9 source keeps ~76% of its width while
# going from 32% to 42% of the frame height.
GENERAL_CONTENT_HEIGHT_RATIO = float(
    os.environ.get("GENERAL_CONTENT_HEIGHT_RATIO", "0.42"))


def full_width_content_height(orig_w, orig_h, out_w):
    """Height the source fills when its FULL width is kept (even)."""
    fg_h = int(round(out_w * orig_h / float(orig_w)))
    return fg_h + (fg_h % 2)


def general_filtergraph(out_w, out_h, content_h=None):
    """Blurred-background 'general shot' layout: bg fills the frame (centre-
    cropped, blurred), fg is scaled to a readable share of the height and
    centred, overflowing the sides rather than floating small in the middle."""
    fg_h = content_h if content_h else int(out_h * GENERAL_CONTENT_HEIGHT_RATIO)
    fg_h += fg_h % 2
    return (
        f"[0:v]split=2[bga][fga];"
        f"[bga]scale=-2:{out_h},crop=w=min(iw\\,{out_w}):h={out_h},"
        f"scale={out_w}:{out_h},gblur=sigma=12[bg];"
        # Scale by HEIGHT, then trim any overflow to the output width. crop
        # centres by default, and min() makes it a no-op when the scaled source
        # is already narrower than the frame (portrait/square sources).
        f"[fga]scale=-2:{fg_h},crop=w=min(iw\\,{out_w}):h=ih[fg];"
        f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1[v]"
    )


def track_filtergraph(cmd_path, init, out_w, out_h):
    """Dynamic-crop layout: sendcmd drives crop x (and w/h/y under punch-in)
    per change-point, then one scale to the delivery size."""
    return (
        f"[0:v]sendcmd=f='{cmd_path}',"
        f"crop@c={init},"
        f"scale={out_w}:{out_h},setsar=1[v]"
    )


# --- strategy ---------------------------------------------------------------

def strategy_from_face_counts(counts):
    """TRACK vs GENERAL verdict from sampled face counts.

    0 faces -> GENERAL (Landscape/B-roll); 1 face -> TRACK;
    > 1.2 average faces -> GENERAL (Group).
    """
    avg_faces = (sum(counts) / len(counts)) if counts else 0
    if avg_faces > 1.2 or avg_faces < 0.5:
        return 'GENERAL'
    return 'TRACK'


def analyze_scenes_strategy(video_path, scenes):
    """Per-scene TRACK (single person) vs GENERAL (group/wide) verdicts.

    Samples 5 frames spread across each scene (clamped inside short scenes),
    skipping near-black frames — fades used to drag single-person scenes into
    GENERAL. A hysteresis pass folds a short scene whose neighbours agree into
    their strategy: each flip is a full layout change, so flapping is worse
    than an occasional wrong-but-stable choice.
    """
    import cv2
    cap = cv2.VideoCapture(video_path)
    strategies = []

    if not cap.isOpened():
        return ['TRACK'] * len(scenes)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    for start_f, end_f in scenes:
        margin = min(2, max(0, (end_f - start_f - 1) // 2))
        frames_to_check = sorted(set(
            int(round(f)) for f in
            _linspace(start_f + margin, end_f - 1 - margin, 5)))

        face_counts = []
        for f_idx in frames_to_check:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret:
                continue
            # Near-black frames (fades, cut-to-black) carry no faces.
            if frame.mean() < 16:
                continue
            face_counts.append(len(detect_face_candidates(frame)))

        strategies.append(strategy_from_face_counts(face_counts))

    cap.release()

    max_flip_frames = int(2.0 * fps)
    for i in range(1, len(strategies) - 1):
        dur = scenes[i][1] - scenes[i][0]
        if (dur < max_flip_frames
                and strategies[i - 1] == strategies[i + 1] != strategies[i]):
            strategies[i] = strategies[i - 1]

    return strategies


def _linspace(a, b, n):
    """numpy.linspace for two scalars without importing numpy at module load."""
    if n <= 1:
        return [a]
    step = (b - a) / (n - 1)
    return [a + step * i for i in range(n)]


# --- analysis ---------------------------------------------------------------

def analyze_trajectory(input_video, scene_boundaries, scene_strategies,
                       fps, orig_w, orig_h, cameraman, tracker):
    """Per-frame decision loop on a downscaled ffmpeg-decoded stream.
    Returns xs: crop x per frame (None on non-TRACK frames)."""
    import numpy as np

    small_w = min(ANALYSIS_MAX_WIDTH, orig_w)
    if small_w % 2:
        small_w -= 1
    small_h = max(int(orig_h * small_w / orig_w), 2)
    if small_h % 2:
        small_h += 1
    scale = orig_w / small_w
    frame_bytes = small_w * small_h * 3

    proc = subprocess.Popen(
        [ffmpeg_binary(), "-loglevel", "error", "-i", input_video,
         "-vf", f"scale={small_w}:{small_h}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=frame_bytes * 4)

    xs = []
    frame_number = 0
    current_scene_index = 0
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(
                (small_h, small_w, 3))

            if current_scene_index < len(scene_boundaries):
                start_f, end_f = scene_boundaries[current_scene_index]
                if (frame_number >= end_f
                        and current_scene_index < len(scene_boundaries) - 1):
                    current_scene_index += 1

            strategy = (scene_strategies[current_scene_index]
                        if current_scene_index < len(scene_strategies)
                        else 'TRACK')

            # Static layouts need no camera trajectory; only tracked scenes
            # produce moving crop values.
            if strategy != 'TRACK':
                cameraman.current_center_x = orig_w / 2
                cameraman.target_center_x = orig_w / 2
                xs.append(None)
            else:
                if frame_number % DETECT_STRIDE == 0:
                    candidates = detect_face_candidates(frame)
                    for cand in candidates:
                        cand['box'] = [int(v * scale) for v in cand['box']]
                        cand['score'] = cand['box'][2] * cand['box'][3]
                    target_box = tracker.get_target(
                        candidates, frame_number, orig_w)
                    if target_box:
                        cameraman.update_target(target_box)
                    elif frame_number % YOLO_FALLBACK_STRIDE == 0:
                        person_box = detect_person(frame)
                        if person_box:
                            cameraman.update_target(
                                [int(v * scale) for v in person_box])

                is_scene_start = (
                    current_scene_index < len(scene_boundaries)
                    and frame_number == scene_boundaries[current_scene_index][0])
                x1, _y1, _x2, _y2 = cameraman.get_crop_box(
                    force_snap=is_scene_start)
                xs.append(x1)

            frame_number += 1
    finally:
        proc.stdout.close()
        proc.wait()

    return xs


# --- render -----------------------------------------------------------------

# Intermediate-segment encode: these get re-encoded again by the caller's
# final pass, so keep them cheap and lossless-ish; crf 20 veryfast on libx264.
_SEGMENT_ENCODE = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                   "-pix_fmt", "yuv420p"]


def render(input_video, final_output_video, aspect_ratio=9 / 16):
    """Full reframe of one clip to ``aspect_ratio``. Raises on failure — the
    caller must treat any exception as "fall back to static crop"."""
    print("   [motion] Reframe engine (ffmpeg-native render)")
    scenes, fps = detect_scenes(input_video)
    fps = float(fps)
    orig_w, orig_h = _video_resolution(input_video)

    out_w, out_h = delivery_size(orig_w, orig_h, aspect_ratio)

    if not scenes:
        fps, total = _probe_fps_total(input_video)
        scenes = [(0, total)]

    scene_boundaries = list(scenes)
    strategies = analyze_scenes_strategy(input_video, scenes)

    cameraman = SmoothedCameraman(out_w, out_h, orig_w, orig_h,
                                  aspect_ratio=aspect_ratio)
    tracker = SpeakerTracker(cooldown_frames=30)

    xs = analyze_trajectory(input_video, scene_boundaries, strategies, fps,
                            orig_w, orig_h, cameraman, tracker)
    if not xs:
        raise RuntimeError("analysis produced no frames")

    # Beats are found once per clip; each scene takes the ones inside it.
    beats = []
    if punch_in.ENABLED:
        beats = punch_in.emphasis_times(input_video, len(xs) / fps)
        if beats:
            print(f"   [motion] Punch-in on {len(beats)} beat(s)")

    crop_w, crop_h = cameraman.crop_width, cameraman.crop_height

    ranges = scene_frame_ranges(scene_boundaries, strategies, len(xs))
    if not ranges:
        raise RuntimeError("no usable scene ranges")
    workdir = tempfile.mkdtemp(prefix="motion_reframe_")
    segments = []
    try:
        for idx, (start_f, end_f, strategy) in enumerate(ranges):
            seg_path = os.path.join(workdir, f"seg_{idx:03d}.mp4")
            ss = start_f / fps
            dur = (end_f - start_f) / fps

            if strategy == 'GENERAL':
                graph = general_filtergraph(out_w, out_h)
            else:
                seg_xs = [x if x is not None else 0 for x in xs[start_f:end_f]]
                cmd_path = os.path.join(workdir, f"cmd_{idx:03d}.txt")
                if beats:
                    zooms = punch_in.zoom_curve(len(seg_xs), fps, beats,
                                                start_offset=ss)
                    boxes = punch_in.crop_boxes(seg_xs, zooms, crop_w, crop_h,
                                                orig_w, orig_h)
                    lines = punch_in.sendcmd_lines(boxes, fps)
                    first = boxes[0]
                    init = f"w={first[0]}:h={first[1]}:x={first[2]}:y={first[3]}"
                else:
                    lines = dedupe_sendcmd_lines(seg_xs, fps)
                    init = f"w={crop_w}:h={crop_h}:x={seg_xs[0]}:y=0"
                with open(cmd_path, "w") as f:
                    f.write("\n".join(lines) + "\n")
                graph = track_filtergraph(cmd_path, init, out_w, out_h)

            run_ffmpeg([
                "-y",
                "-ss", f"{ss:.4f}", "-t", f"{dur:.4f}", "-i", input_video,
                "-filter_complex", graph, "-map", "[v]",
                *_SEGMENT_ENCODE, "-an", seg_path,
            ])
            segments.append(seg_path)

        list_path = os.path.join(workdir, "concat.txt")
        with open(list_path, "w") as f:
            f.write(concat_list_content(segments))

        # Concat video segments (stream copy) + audio straight from the clip.
        run_ffmpeg([
            "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-i", input_video,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "copy",
            "-map_metadata", "-1", "-map_chapters", "-1",
            "-movflags", "+faststart",
            final_output_video,
        ])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"   [motion] Reframed clip saved to {final_output_video}")
    return True


def _video_resolution(video_path):
    import cv2
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video file {video_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return width, height
