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
| `reframe.py` | The v2 reframe engine: trajectory analysis pass + ffmpeg-native sendcmd render (TRACK dynamic crop / GENERAL blurred bed), punch-in on beats | composes the layout upgrades below; each degrades independently |
| `split_layout.py` | SPLIT: two-shot conversation stacked into half-frames (`SPLIT_LAYOUT=1`) | int-frame scenes; `face_detect` instead of openshorts main.py globals |
| `panel_layout.py` | PANEL: 3-4 people from one wide shot tiled 2x2 (`PANEL_LAYOUT=1`) | int-frame scenes; face_detect |
| `screencast_layout.py` | SCREENCAST (content stacked over speaker) + WIDE (side-crop disabled), Gemini width-fraction gated (`SCREENCAST_LAYOUT=1`, needs `GEMINI_API_KEY`) | int-frame scenes; full-res detection via `face_detect.detect_face_candidates_full_res`; prompts from `gemini_layout` |
| `gemini_layout.py` | The two measured Gemini prompts + response schemas + block check the layouts need | verbatim subset of upstream gemini_worker |
| `layout_picker.py` | One Gemini call per video picks none/screencast/split from 12 sampled frames (`AUTO_LAYOUT=1`; `=shadow` logs without applying) | gemini_layout import; plain log prefixes |

Wired into the ranking pipeline stage-1 (`RANKING_REFRAME=1`): assembler cuts
the clip's window and runs `reframe.render()` over it, swapping the static
centre-crop fill for a subject-tracked 9:16 crop; any engine failure falls
back to the old fill path. Layout upgrades stack on top of that routing in
upstream order — SPLIT, then the active-speaker conversation gate
(`SPEAKER_SIGNAL=1`, hard cuts with `SPEAKER_CUT=1`), then PANEL on scenes
still GENERAL, then SCREENCAST/WIDE/INSET which beat any face arrangement.

Not yet vendored (tracked for follow-up): nothing from openshorts' render
path. Remaining candidates live outside it (FunClip speaker-aware clipping).

Tests: `tests/` — run with `python -m pytest tests/ -q` from this directory.
