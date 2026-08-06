# Continuation Prompt for Shorts Pipeline Speedup Work

**Context:** You (the AI) just completed Phases 1-3 of a major performance overhaul on the YouTube Shorts Pipeline (`artisan/youtube-shorts-pipeline`). The work has been merged to `main` and pushed to GitHub.

## Current State (as of commit `46aeb63`)

### ✅ COMPLETED — Phases 1-3

| Phase | Commit | What's Done |
|-------|--------|-------------|
| **1. Config** | `7fbfff6` | 14 new env vars in 3 groups (discovery/caption transcription, download, render). `.env.template` updated with full docs. `Config._float()` added. |
| **2. Transcriber** | `421390d` | Two-pass design (discovery + caption). Memory-bounded windows (`TRANSCRIBE_WINDOW_MINUTES=15`). Model caching by `(size, device, compute_type, threads)`. Section-aware `transcribe_file()` that keeps a file's own timeline (critical for caption sync). Progress logging with realtime factor + ETA. All params config-driven. |
| **3. Video Editor / Render** | `a56dc71` + `29be1fc` | Removed `gblur=sigma=28` (was #1 cost: 19.87s filters for 20s clip). New `cheap` backdrop (default): blur at 1/8 res + scale up — **filter stage 3.07× faster, end-to-end 1.45× faster, SSIM 0.975**. Sigma derived as `28/k`. `fast_bilinear` scaling (~10% faster). `captions_are_clip_relative` flag — prevents silent caption loss on section-downloaded clips (verified: wrong rebase = 0 captions). Per-render thread cap. `RENDER_WORKERS` auto-scales with cores (measured: 1.02–1.06× on 2 cores, not 2×). Benchmarks in `BENCHMARKS.md`. |

### 📁 Files Changed
- `src/config.py` — 14 new env vars, `_float` helper
- `config/.env.template` — full documentation of all vars
- `src/transcriber.py` — complete rewrite (13 KB → 23 KB)
- `src/video_editor.py` — new `build_background_filters()`, `captions_are_clip_relative`, thread cap
- `BENCHMARKS.md` — new, measured render/transcription data
- `PIPELINE_PERFORMANCE_REPORT.md` — updated with full pipeline spec

### 🔧 Config Defaults Now Active
```bash
# Discovery transcription (fast, cheap — only to FIND highlights)
TRANSCRIBE_MODEL=tiny
TRANSCRIBE_BEAM=1
TRANSCRIBE_WORD_TIMESTAMPS=false
TRANSCRIBE_VAD=true
TRANSCRIBE_MAX_MINUTES=0
TRANSCRIBE_WINDOW_MINUTES=15

# Caption transcription (accurate, word-level — ONLY on selected clips)
TWO_PASS_CAPTIONS=true
CAPTION_MODEL=base
CAPTION_BEAM=5

# Download
DOWNLOAD_AUDIO_ONLY=true      # not yet wired in downloader.py
DOWNLOAD_SECTIONS=true        # not yet wired
SECTION_PADDING=8
DOWNLOAD_HEIGHT=1080

# Render
RENDER_WORKERS=auto           # scales with cores (1 on 2-core box)
BACKGROUND_MODE=cheap         # SSIM 0.975, 3.07x filter speedup
MAX_CANDIDATES=30
```

---

## ⏳ PENDING — Phases 4-8 (WHERE YOU PICK UP)

### Phase 4: Downloader — Audio-only + Section Fetch (HIGHEST PRIORITY)
**Goal:** Wire the config flags that already exist but aren't used yet.

| Task | File | Details |
|------|------|---------|
| **Audio-only discovery fetch** | `downloader.py` | `ydl_opts['format'] = 'bestaudio/best'` when `config.download_audio_only`. ~40 MB/hr vs 1-2 GB. |
| **Section fetch** | `downloader.py` | `yt-dlp --download-sections "*START-END"` for each clip range. Uses `config.section_padding` (±8s). |
| **Keyframe drift handling** | `downloader.py` | Section files start at preceding keyframe (unknown offset). **Fix:** In Pass 2, transcribe the section file's own audio — its word timestamps are already in that file's timeline. This is why `transcribe_file(captions_are_clip_relative=true)` exists. |
| **Parallel section fetch** | `downloader.py` / `main.py` | `DOWNLOAD_CONCURRENCY=2` — overlap with rendering (producer/consumer). |

**Acceptance:** Discovery fetch = audio only (~30s). Section fetch = only the chosen clip ranges. Caption Pass 2 runs on section audio → sync correct by construction.

### Phase 5: Processor — Deep Candidate List
| Task | File | Details |
|------|------|---------|
| Return ranked candidates up to `MAX_CANDIDATES=30` | `processor.py` | Currently caps at `max_clips_per_video` (5). Change `find_highlight_segments()` to return all scored candidates (or take `max_candidates` param). |
| Persist full ranked list | `main.py` / `database.py` | Save to `clip_plans/<video_id>.json` so `--render-more N` reuses the plan (no re-download, no re-transcribe). |

### Phase 6: Main.py — New Orchestration
| Task | File | Details |
|------|------|---------|
| **Overlapped fetch+render** | `main.py` | Producer: downloads next clip's section while Consumer: renders current clip. ThreadPoolExecutor for download concurrency. |
| **Clip plan cache** | `main.py` | Load/save ranked candidates + clip metadata. `--render-more N` renders additional clips from cached plan. |
| **CLI flags** | `main.py` | `--max-source-minutes N` (head-only discovery), `--render-more N` (additional clips from plan). |

### Phase 7: Tests + E2E Verification
- Test on a **short video first** (not hour-long) to validate full flow
- Verify **caption sync** on section-downloaded clips (the trap)
- Benchmark: audio-only discovery → 20+ clips in 6-8 min target

### Phase 8: Docs + PR
- Update `PIPELINE_PERFORMANCE_REPORT.md` with actual measured results
- PR to `main` (already on main after merge)

---

## Critical Architecture Notes (Don't Re-Derive)

1. **Section-download caption-sync trap**: `yt-dlp --download-sections` starts at the **preceding keyframe** (unknown offset up to ~10s). Subtracting nominal start from source-timeline captions desyncs EVERY caption. **Fix is built in:** Pass 2 transcribes the section file's own audio → word timestamps live in that file's timeline natively → `captions_are_clip_relative=true` → zero offset arithmetic.

2. **`TRANSCRIBE_MAX_MINUTES=0` is intentional** — truncating the source discards clips. The goal is "as many good clips as possible." Keep it opt-in.

3. **`RENDER_WORKERS` auto-scales** — measured 1.02–1.06× on 2 cores. The real overlap win is **network-vs-CPU**, not CPU-vs-CPU.

4. **Gaussian blur scale-invariance**: `gblur` is only scale-invariant if `sigma` scales with downscale factor `k` (i.e., `sigma = 28/k`). A hand-picked sigma scored SSIM 0.870 and looked wrong. The code derives it.

---

## How to Resume

```bash
cd /home/user/webapp/artisan/youtube-shorts-pipeline  # or your local path
git status  # should be clean on main at 46aeb63
# Start Phase 4: edit src/downloader.py
# Test: python -m src.main --mode once <video_id> --from-library --niche podcast
```

The config is all in place. The transcriber and renderer are ready. The only missing piece is wiring the downloader to actually USE `DOWNLOAD_AUDIO_ONLY`, `DOWNLOAD_SECTIONS`, `SECTION_PADDING`.

Good luck — the hard architectural decisions are done. Now it's just wiring and testing.