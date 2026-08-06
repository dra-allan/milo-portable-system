# HANDOFF TO GENSPARK — CONTINUATION BRIEF

**Written by Milo (Allan's assistant) — 2026-08-06. Read this whole file before doing anything.**

You are continuing work on Allan's YouTube Shorts automation pipeline. A previous
Genspark session was fixing it and was cut off mid-task. **Most of that work is
already committed and pushed** — do NOT redo it. This file tells you exactly what
exists, what is verified, and what is left.

---

## 0. The repo

- **Remote:** `https://github.com/dra-allan/milo-portable-system.git`
- **Branch to work on:** `main` (a copy of your old work sits on
  `genspark_ai_developer` — already merged, you can ignore it)
- **Pipeline location:** `artisan/youtube-shorts-pipeline/`
- **Entry points:** `run_pipeline.bat` (Windows interactive) or
  `python -m src.main --mode <test|once|schedule>` from the pipeline folder.

## 1. What is already DONE (all merged into main, pushed)

Your previous session produced these, and they are in `main`:

1. **Highlight detector fixed** — `src/processor.py`. Root cause of "Found 0
   highlight segments from 723 windows": candidates were fixed 5-second windows
   filtered by a 15-60s band, so zero could ever pass. Now variable-length
   candidates along transcript boundaries, capped extensions per start point
   (~4.6k candidates in 0.35s instead of 22.8k), keyword scoring, filler
   penalty, never-return-zero fallback, max_clips cap. Verified: 8 clips from a
   simulated 1169-segment transcript.
2. **Imports + config hardened** — `src/config.py`, all module imports.
   Relative-first imports so `python -m src.main` works; Config() no longer
   crashes at import on a missing niches.yaml; paths anchored to project root
   (not CWD); UPLOAD_ENABLED defaults false (never auto-publish on first run);
   WHISPER_MODEL/WHISPER_DEVICE keys added.
3. **SQLite tracking wired** — `src/database.py` (new). Dedup of processed
   videos + generated shorts, idempotent upserts, in-place migration. Verified.
4. **main.py restored** — was a truncated fragment with no class/entrypoint.
   Now a full orchestrator: `ShortsPipeline`, `extract_video_id()` (watch /
   youtu.be / /shorts/ / /embed/ / /live/ / bare ID — the batch file was
   passing full URLs as IDs before), `guess_niche()`, `--mode test` doctor,
   opt-in upload, audio cleanup in finally, run_niche() honesty. Verified:
   `python -m src.main --mode test` runs and flags missing deps.
5. **Renderer rewritten** — `src/video_editor.py`. Single-pass FFmpeg
   1080x1920 vertical with blurred fill (was 4 re-encodes + wrong aspect
   405x720), captions rebased to clip timeline (were written at absolute
   times), float-second cut points (no truncation), timeouts scale with clip
   length (was fixed 30-60s killing long clips), cross-device rename fixed
   (shutil.move), ASS escaping fixed. Verified against a real 40s source:
   1080x1920, 30fps, 22.000s, audio, captions in sync (frames inspected).
6. **Transcriber rewritten** — `src/transcriber.py`. This was the file your
   session wrote last and got cut off before committing. Milo committed it as
   `9c246d8`. One streaming pass over the whole file (faster-whisper native),
   with an opt-in chunked fallback that guarantees forward progress and
   de-duplicates the overlap. Fixes the infinite loop at the tail that burned
   ~935 redundant Whisper calls (that's why a 30-min video took 22 minutes to
   transcribe). **IMPORTANT: this rewrite is committed but NOT yet tested
   end-to-end on a real file. Verify it.**

## 2. Known state after the fixes

- `python -m src.main --mode test` runs (doctor-style check).
- Components were tested individually: processor (8 clips), database
  (dedup/stats), renderer (real 40s video → valid 1080x1920 short), URL
  parsing (Allan's exact GTA URL).
- **The full end-to-end flow (URL → download → transcribe → detect → render →
  keep clips) has NOT been run on the merged code.** That is the first thing
  to verify.

## 3. What is LEFT to do (in priority order)

### P0 — End-to-end verification of the shorts pipeline
Run a complete `once` run on a real URL with a working environment and confirm
it produces Shorts files on disk:
- `python -m src.main --mode once "<URL>" --niche gaming` (or without niche to
  test guessing)
- Confirm: download, audio extract, transcription (fast now), highlight clips,
  rendered vertical MP4s, DB dedup entries, clips kept on disk even if upload
  disabled.
- If any stage fails, fix it and push immediately.
- **Test the transcriber specifically** — it is the newest code and never ran
  for real in your session.

### P1 — Upload path made real (YouTube API)
- `UPLOAD_ENABLED=false` is correct as the default. To actually publish, Allan
  needs real YouTube Data API v3 OAuth credentials.
- Make the uploader's auth story clean: document exactly what creds are needed
  and how to supply them (config/.env + GOOGLE_APPLICATION_CREDENTIALS /
  YOUTUBE_OAUTH_CLIENT_SECRETS / YOUTUBE_OAUTH_TOKEN_FILE — keys already exist
  in config.py). Do NOT invent or hardcode credentials.

### P1 — Channel discovery (currently a stub)
- `search_channels()` in downloader.py logs "limited; consider using YouTube
  Data API" and returns []. The scheduled/discovery mode can't really find new
  source channels yet. Decide the right approach (YouTube Data API search vs
  yt-dlp) and implement, or clearly document the limitation and leave it.

### P2 — Long-form Video Factory improvements (separate pipeline)
`artisan/mm_pipeline/` — the long-form Money Matrix engine (1920x1080, 7 stage
agents, charts via matplotlib). A full report on the last project
(`20260805_Equation_To_Wealth`) lists wanted improvements, in order:
1. **Segment sync** — break segments at sentence boundaries (40-50 chars) so
   visual changes match spoken words.
2. **Visual quality** — integrate Pexels video clips (short videos) instead of
   static photos.
3. **Visual lexicon** — double the keyword lexicon with context/tension terms
   instead of single keywords.
4. **Visual+script match** — if the script says "Amazon solved a
   billion-dollar problem", media should be Jeff Bezos/Amazon visuals, not
   generic money stock.
Also: the YouTube transcript fetch step (fetch_transcript → twist_script) is
the core replication loop for the long-form side; check its current state.

### P2 — Shorts enhancement with OpenShorts + FunClip (Allan's plan)
Allan wants the shorts pipeline enhanced using two existing repos:
- `C:\Users\user\Desktop\openshorts` — OpenShorts.app (open-source AI video
  platform: Clip Generator for 9:16 from long-form, AI Shorts/UGC, YouTube
  Studio tools). Has an MCP server and REST API; self-hostable.
- `C:\Users\user\Desktop\FunClip` — speaker-aware video clipping tool.
Integrate what's useful INTO the shorts pipeline (per Milo's self-contained
rule: vendor a copy inside the repo, never depend on an external path). Ideas:
auto-generate more clip layouts, smarter clip selection, caption styling.
Do NOT set up the hosted openshorts.app SaaS — self-host/integrate locally.

## 4. Standing rules (from Allan, non-negotiable)

1. **PUSH EVERY SINGLE FIX IMMEDIATELY.** One fix = one commit = one push to
   `main` (or a short-lived branch + PR if you prefer, but never let work sit
   uncommitted). Allan's words: "make sure you always push any single fix you
   make in order not to lose context." A previous session lost work to a
   sudden stop — never again.
2. **SELF-CONTAINED.** Any tool/repo/skill you integrate must be COPIED into
   the milo project (vendored), never imported from an external path. Milo
   grows by absorbing capabilities.
3. **No secrets.** Never write a password, API key, or token value into
   commits, logs, or docs. Reference where credentials live; use
   `{{PLACEHOLDER}}` style templates.
4. **Don't publish by accident.** UPLOAD_ENABLED stays false unless Allan
   explicitly says to enable upload.
5. **Verify before claiming.** Run it. Check output. Then commit.
6. Windows is the target platform (Allan's machine). Your sandbox may be
   Linux — keep code cross-platform (paths via pathlib, no hardcoded /home).

## 5. What Allan wants overall (context)

Two businesses, neither has posted anything yet:
1. **YouTube channel replication** — 5 channels (Money Matrix, ExplaiNation,
   god did fx, moviegasm, flick rush). One codebase: the long-form Video
   Factory + this shorts pipeline. Goal: URL in → finished video/short out.
2. **Website flip** — global Google Maps prospect hunt, build premium sites,
   Gmail cold outreach, sell one-off. (Separate stack in
   `milo-workspace/website-flip/` — 3 demo sites already shipped, cold emails
   sent 2026-08-03.)
The immediate job is fixing the YouTube pipelines so they start producing.
Website flip is a separate workstream — don't let it block this one.
