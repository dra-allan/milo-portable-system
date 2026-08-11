# RANKING SHORTS PIPELINE — ONE-PASS SPEED + LAUNCHER PARITY FIX

Apply every fix below in ONE pass. Do not stop to ask questions. Where a
detail is ambiguous, make the safe choice yourself and document it in the
commit message. Do not touch anything outside the sections listed here.
Do not re-architect. One commit per fix.

Repo root: `artisan/ranking-shorts-pipeline/` inside the milo-portable-system
repo. Current HEAD is committed; start from it. Use the existing repo venv at
`../../.venv/Scripts/python.exe` when you need to run Python (`py_compile`).
Verify each fix with `python -m compileall -q src` after you finish.

---

## WHY THE PIPELINE IS SLOW (verified, trust this, don't re-investigate)

A sweep targets 12 builds (`QUEUE_TARGET_TOTAL=12`). Each build downloads 5
clips **serially**, vets them, runs TTS (up to a 30-min subprocess wall),
then renders each clip **serially** through ffmpeg (1080x1920@30) plus one
stitch pass. Three knobs already exist claiming to speed this up but are
**dead — parsed into config and never consumed**:

1. `RANKING_RENDER_WORKERS` → read into `config.render_workers`
   (`src/config.py:46`) but never used. `assembler.assemble`
   (`src/assembler.py:545`) renders clips in a plain `for` loop, and
   `main.py:265 _build_fresh` builds videos one at a time in a `while` loop.
   Result: 12 builds × 5 renders = 60 serial ffmpeg passes.

2. `RANKING_REJECT_BUDGET` → read into `config.reject_budget`
   (`src/config.py:46`) but never used. `main.py:61` hardcodes
   `reject_budget = max(12, needed * 4)` (`=20` for 5 clips), so a normal
   accept/reject stub always grinds through the full budget before giving
   up. This is the "too many rejects, too slow" drag.

3. `run_pipeline.bat` (the youtube-shorts launcher) is behind the ranking
   and POV launchers — missing scheduler control, log access, env editing,
   stop-daemon. Needs parity.

---

## FIX 1 — Honor `RANKING_RENDER_WORKERS` in `src/assembler.py`

Parallelize the stage-1 clip renders in `assemble()`. The stage render loop
(currently `assembler.py:545`) must render up to `config.render_workers`
clips concurrently instead of serially. The stitch stage (`stitch()`) stays
serial — it depends on all stage files.

Rules:
- Use a `ThreadPoolExecutor` with `max_workers=config.render_workers`.
  Threads are the right primitive here: each render is an ffmpeg subprocess
  that releases the GIL, so threads give real parallelism without a
  ProcessPool's fork cost, and `clip` dicts stay pickling-free.
- `clip['hook'] = index == 0` must be set on each clip **before** its render
  task is submitted, exactly as it is today.
- Do NOT parallelize across builds in `main.py _build_fresh`: builds touch
  the sqlite DB and different builds would race on `record_build`.
  Render-level parallelism is the win; that is all we are doing.
- Do not change the ffmpeg commands, the encoder resolution, or any overlay
  filter. Only the scheduling of the calls.

Result must keep the same output as today: same per-clip files in the same
`temp` work dir, same stitch, same final video. Only faster.

Sanity-check yourself: a 5-clip build should now render clips in ~2 waves
at `RANKING_RENDER_WORKERS=2` instead of 5 serial passes. Do not hand-test
with a real YouTube download — reason it out, run `py_compile`, and commit.

---

## FIX 2 — Honor `RANKING_REJECT_BUDGET` in `src/main.py`

In `collect_clips` (`main.py:61`), replace the hardcoded
`reject_budget = max(12, needed * 4)` with
`reject_budget = max(int(config.reject_budget), needed)` so the bat's
`RANKING_REJECT_BUDGET` actually gates how many consecutive rejects abort a
build. Floor it at `needed` so builds can never abort before one full pass
of candidates.

Then fix the bat default so the knob does not over-reject: in
`run_ranking_pipeline.bat:14` change `RANKING_REJECT_BUDGET=2` to
`RANKING_REJECT_BUDGET=20`. (2 means the first two rejects abort a build —
too strict given sourcing rejects the majority of candidates. 20 preserves
today's effective behavior while making the knob real.)

---

## FIX 3 — Bring `youtube-shorts-pipeline/run_pipeline.bat` to launcher parity

Make the youtube-shorts launcher feature-equal to
`run_ranking_pipeline.bat` / `start_pov_pipeline.bat`. Keep its existing
niche semantics; add the missing facilities:

- **Open log** menu item: open the shorts pipeline log file in notepad
  (mirror POV's `:open_log`, using the shorts runtime log path).
- **Stop scheduler daemon** menu item: kill `--mode schedule` processes
  launched from this pipeline directory, using the exact PowerShell pattern
  in `run_ranking_pipeline.bat:173` `:stop_daemon` (matched against the
  pipe dir + `--mode schedule`). The shorts scheduler currently has no stop.
- **Edit environment** menu item: open the `.env` (or the token/env file
  the shorts pipeline uses) in notepad, mirroring POV's `:edit_env` flow.
- **Scheduler install/remove** (persistent `schtasks` daemon) only if the
  shorts pipeline already supports a documented daemon cadence; if it does,
  mirror POV's `:install_task`/`:remove_task`. If the shorts scheduler runs
  only in-window, add the two POV-style `schtasks onlogon` items anyway so
  both pipelines behave the same.
- Keep `:load_env`, `:ensure_python` (with its yt_dlp check), `:start_timer`
  / `:stop_timer`, `:run`, compile check, reset-caps, cleanup, folders —
  already present; do not remove them.
- Align the header banner (echo of vars + runtime path) so both shorts
  panels print their live settings: `SCHEDULE_MAX_VIDEOS`, `CAPTION_STYLE`,
  runtime path.

Do not change `src/` behavior of the youtube-shorts pipeline at all. This
fix is launcher-only.

---

## MUST NOT DO (hard boundaries)

- Do not touch `artisan/pov_pipeline/` (agent_runner.py, povconfig.py,
  run_pov_pipeline.py, start_pov_pipeline.bat) or the POV factory routing.
- Do not touch `main.py`'s db interactions, sweep caps, daily cap logic, or
  the `allow_commentary`/`allow_music` per-topic behaviors in vetting — they
  are correct and deliberate.
- Do not "optimize" the ffmpeg overlays in `src/overlays.py`. If you must
  read it, follow the verified quoting rule: drawtext fontfile must be
  DOUBLE-quoted AND the drive colon escaped, e.g.
  `fontfile='C\:/Windows/Fonts/impact.ttf'`. Prefer not touching it at all.
- Do not change `rank.yaml` thresholds, `min_score`, or any topic config.
- Do not add new dependencies or change the venv.
- Do not touch `tests/`; `tests/test_overlays.py` has 5 pre-existing
  failures unrelated to this work — leave them.

## DELIVERABLE

Exactly three commits on top of the current HEAD, in order:
1. `perf(ranking): parallelize stage-1 clip renders honor RANKING_RENDER_WORKERS`
2. `fix(ranking): wire RANKING_REJECT_BUDGET into collect_clips`
3. `chore(shorts): bring run_pipeline.bat to launcher parity`

End your reply with: for each commit, its hash and a one-line summary of
what you changed. Do not paste large diffs back.