# artisan/motion — subject-tracking render intelligence

Vendored from [openshorts](https://github.com/mutonby/openshorts) (MIT, itself
forked from kamilstanuch/Autocrop-vertical) on 2026-08-24, per Milo's
self-contained rule: capability lives inside the milo repo, never referenced
from outside.

Logic is unchanged from upstream except where noted. Upstream's measured
tuning notes are preserved in the docstrings — they are the reason the
constants are what they are.

| Module | What it gives us | Changes vs upstream |
|---|---|---|
| `cameraman.py` | `SmoothedCameraman` — heavy-tripod camera smoothing with jump confirmation | extracted from openshorts `main.py` |
| `speaker_tracker.py` | `SpeakerTracker` — sticky multi-speaker target selection with cooldown hold | extracted from openshorts `main.py` |
| `active_speaker.py` | Per-window speaker attribution (mouth motion, per-person normalized, audio-gated); speaker-cut x-trajectory | `split_layout.as_box` inlined as `_as_box` |
| `punch_in.py` | Beat-synced 1.12x punch-ins driven by the audio envelope | none |
| `camera_inset.py` | Webcam-inset detector + layout for screen recordings | `import main as m` -> `person_detect as m` |
| `person_detect.py` | Standalone YOLO person fallback (lazy model, own lock) | de-globalized from openshorts `main.py` |
| `word_snapping.py` | `snap_clip_to_words` — snap proposed cuts onto word boundaries + silence | extracted verbatim |
| `face_detect.py` | Face candidates: mediapipe when importable, else YuNet DNN | Haar-free; YuNet ONNX vendored in `models/` (cv2 5.x removed CascadeClassifier) |
| `scene_detection.py` | TransNetV2 -> PySceneDetect -> single-scene chain, int-frame contract | lazy engine imports; single-scene fallback added (clip windows are short) |
| `reframe.py` | The v2 reframe engine: trajectory analysis pass + ffmpeg-native sendcmd render (TRACK dynamic crop / GENERAL blurred bed), punch-in on beats | SPLIT/SCREENCAST/WIDE/INSET/ALTERNATE layouts deferred until their modules are vendored |

Wired into the ranking pipeline stage-1 (`RANKING_REFRAME=1`): assembler cuts
the clip's window and runs `reframe.render()` over it, swapping the static
centre-crop fill for a subject-tracked 9:16 crop; any engine failure falls
back to the old fill path.

Not yet vendored (tracked for follow-up): openshorts layout modules
(`split_layout`, `screencast_layout`, `active_speaker` render layouts) to
light up reframe's stacked-screen scenes; quality_probe dotenv-free copy
(done); layout router (needs Gemini key wiring).

Tests: `tests/` — run with `python -m pytest tests/ -q` from this directory.
