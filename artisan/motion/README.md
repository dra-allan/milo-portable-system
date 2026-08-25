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

Not yet vendored (tracked for follow-up): `reframe_v2.py` dynamic-crop render
engine (needs its sendcmd pipeline wired to our assembler), `quality_probe`
dotenv-free copy (done), layout router (needs openshorts' screencast/split
render layouts to be meaningful here).

Tests: `tests/` — run with `python -m pytest tests/ -q` from this directory.
