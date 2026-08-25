# Work Claims

Coordination ledger for the two Milo machines sharing this repo.

- **brain** = AWS instance (this box, hostname EC2AMAZ). Production daemons live here.
- **pc** = Allan's original machine. Development / experimentation lives here.

## Rules

1. Read this file at boot. If a task is already claimed OPEN, do not start it —
   pick something else or coordinate with the claimant.
2. Before starting non-trivial work: add an OPEN row. After pushing it: move it
   to DONE with the commit that landed it.
3. Never push a state/ snapshot from two machines in the same window. The
   `backup/<machine>` branch pattern is single-writer; `main` is portable code
   only.
4. If you resolve a rebase/merge conflict, add a DONE row noting what you kept.

## OPEN

(claim format: `- [<machine>] YYYY-MM-DD <task>`)

(none)

## Actions the main PC must take (from the AWS brain, 2026-08-15)

1. `git pull --rebase origin main` (state/ no longer tracked — the old
   skip-worktree files are gone; plain pull works now)
2. Set your identity once: `git config user.name "Milo (PC)"` and
   `git config user.email "milo.pc@milo.local"`
3. Run `python -m miloctl.cli backup --no-push` to create the backup worktree
   locally (verify it commits to `backup/pc`, NOT main), then push that branch
   with `git -C .backup/pc push origin backup/pc`
4. Delete the now-obsolete `scripts/fix-vps-state.ps1` skip-worktree flow —
   the per-machine branch pattern replaces it
5. From now on use `powershell -File scripts/git-sync.ps1` instead of
   hand-rolled add/commit/push

## DONE

- [pc] 2026-08-25 layout modules vendored + wired: artisan/motion gains split_layout (SPLIT_LAYOUT=1), panel_layout (PANEL_LAYOUT=1), screencast_layout (SCREENCAST_LAYOUT=1, Gemini width-fraction gate), gemini_layout (verbatim prompt subset), layout_picker (AUTO_LAYOUT=1|shadow); face_detect adds detect_face_candidates_full_res; reframe.render composes SPLIT -> speaker gate -> PANEL -> SCREENCAST/WIDE/INSET in upstream order, each upgrade degrading independently, static dispatch pinned in test_reframe_layouts; motion tests 187/187+1 skip, pipeline suite 45/45, live smoke render green defaults + layouts-on - 493911c; follow-up fix: camera_inset stale upstream refs (detect_faces_full_res -> face_detect, detect_person_yolo -> detect_person) found by real-footage demo runs - 6f8f9e7
- [pc] 2026-08-25 'unbound' niche verdict (Allan): intended behaviour, not a bug — row closed
- [pc] 2026-08-25 reframe engine vendored + wired: artisan/motion gains reframe.py (sendcmd dynamic-crop render, TRACK/GENERAL + punch-in), face_detect.py (mediapipe-or-vendored-YuNet; cv2 5.x killed Haar), scene_detection.py (transnetv2->pyscenedetect->single-scene); ranking stage-1 RANKING_REFRAME=1 swaps centre-crop fill for tracked crop with fallback; motion tests 83/83, pipeline suite 45/45 - 5c1798a
- [pc] 2026-08-25 vendor batch 1: artisan/motion (cameraman, speaker_tracker, active_speaker, punch_in, camera_inset, person_detect, word_snapping, quality_probe) + tests 62/62 + nvenc AQ both pipelines — 06a3ef5

- [brain] 2026-08-15 multi-machine sync setup: git identity, work-claims ledger, git-sync.ps1, per-machine backup branches. Decision: state/ untracked from main; `milo backup` now pushes each machine's snapshot to its own branch (`backup/brain` / `backup/pc`) via a git worktree at `.backup/<machine>`. Main = portable code only. Redacted a live COMPOSIO API key that had leaked into memory.
- [brain] 2026-08-18 bake external_directory permission fix into harness sync (Windows backslash path matching, upstream #7279/#11042/#36681) — landed cf5f447
- [brain] 2026-08-15 reconcile caption burn after merge with AWS brain (1eca44c, 2d58d0a) — kept AWS base, re-applied tests/YAML knobs/keyword sourcing