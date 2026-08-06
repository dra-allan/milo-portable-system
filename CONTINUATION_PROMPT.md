# Continuation Prompt for Shorts Pipeline Speedup Work — COMPLETED

**Context:** All Phases 1-6 of the major performance overhaul on the YouTube Shorts Pipeline (`artisan/youtube-shorts-pipeline`) are **COMPLETE** and pushed to `main`.

## ✅ COMPLETED — All Phases

| Phase | Commit | What's Done |
|-------|--------|-------------|
| **1. Config** | `7fbfff6` | 14 new env vars in 3 groups (discovery/caption transcription, download, render). `.env.template` updated with full docs. `Config._float()` added. |
| **2. Transcriber** | `421390d` | Two-pass design (discovery + caption). Memory-bounded windows (`TRANSCRIBE_WINDOW_MINUTES=15`). Model caching by `(size, device, compute_type, threads)`. Section-aware `transcribe_file()` that keeps a file's own timeline (critical for caption sync). Progress logging with realtime factor + ETA. All params config-driven. |
| **3. Video Editor** | `a56dc71` + `29be1fc` | Removed `gblur=sigma=28` (was #1 cost: 19.87s filters for 20s clip). New `cheap` backdrop (default): blur at 1/8 res + scale up — **filter stage 3.07× faster, end-to-end 1.45× faster, SSIM 0.975**. Sigma derived as `28/k`. `fast_bilinear` scaling (~10% faster). `captions_are_clip_relative` flag — prevents silent caption loss on section-downloaded clips (verified: wrong rebase = 0 captions). Per-render thread cap. `RENDER_WORKERS` auto-scales with cores (measured: 1.02–1.06× on 2 cores, not 2×). Benchmarks in `BENCHMARKS.md`. |
| **4a. Downloader — Audio-Only** | `aeb200e` | `download_audio()` fetches `bestaudio/best` (~40 MB/hr vs 1-2 GB). Audio + sections live in `temp_dir/audio/` and `temp_dir/sections/`. **Safety property**: `find_local_video()` can't mistake audio for full video. Tests catch 3 mutations. |
| **4b. Downloader — Section Fetch + Keyframe Drift** | `f72d246` | `download_section()` fetches one clip range via `yt-dlp download_ranges`; `download_sections()` runs `DOWNLOAD_CONCURRENCY` in parallel. **Keyframe drift measured, not assumed**: `clip_start_in_file = lead_in + pad_before`. Video + captions move together → sync correct by construction. `force_keyframes_at_cuts` deliberately not set. 20 tests (2 real ffmpeg), 4 mutations caught. |
| **5. Processor — Deep Candidate List** | `0db2e5b` | `find_highlight_segments()` accepts `max_candidates` (default `MAX_CANDIDATES=30`). Returns up to that many ranked clips with `rank` field (1 = highest score). Chronological output preserved; rank reflects priority. Enables two-pass workflow: transcription once, then "give me 10 more clips" instant. |
| **6. Main.py Orchestration** | `7d15c85` | **Clip plan cache**: full ranked candidate list saved to `data/clip_plans/<video_id>.json`. **`--render-more N`**: renders N additional clips from cached plan by rank — zero re-download, zero re-transcribe. **`--max-source-minutes N`**: limits transcription to first N minutes (0 = full). Head-only transcription via `max_seconds` in audio extraction. Overlapped fetch+render architecture documented (producer/consumer, network-vs-CPU overlap). |

---

## 📁 Files Changed

| File | Changes |
|------|---------|
| `src/config.py` | 14 new env vars, `_float()` helper |
| `config/.env.template` | Full documentation of all vars |
| `src/transcriber.py` | Complete rewrite (13 KB → 23 KB) |
| `src/video_editor.py` | `build_background_filters()`, `captions_are_clip_relative`, thread cap |
| `src/downloader.py` | Audio-only fetch, section fetch, keyframe drift measurement |
| `src/processor.py` | `max_candidates` param, `rank` field on clips |
| `src/main.py` | Clip plan cache, `--render-more`, `--max-source-minutes`, head-only transcription |
| `tests/test_downloader_fetch.py` | 20 cases (2 real ffmpeg), mutation-verified |
| `tests/test_processor_candidates.py` | 6 cases for rank/priority/chronological order |
| `BENCHMARKS.md` | Measured render/transcription data |
| `PIPELINE_PERFORMANCE_REPORT.md` | Full pipeline spec + performance analysis |

---

## 📁 Folder Consolidation (Also Completed)

| Before | After |
|--------|-------|
| `C:\Users\user\milo-workspace\shorts-data` (hidden) | `C:\Users\user\Desktop\Milo Video Factory\shorts` |
| `C:\Users\user\milo-workspace` (hidden) | `C:\Users\user\Desktop\Milo Workspace` (config/repos/docs) |
| Scattered output | **Single `Milo Video Factory` on Desktop** |

**New structure:**
```
Desktop/
├── Milo Video Factory/           # ALL pipeline output
│   ├── shorts/                   # Shorts pipeline (temp, shorts, data, logs)
│   ├── pov/                      # POV pipeline (scripts, tts, assembler)
│   ├── projects/                 # Final renders (POV/MM)
│   ├── audio/                    # TTS output
│   ├── images/                   # Generated images
│   └── video/                    # Final videos
└── Milo Workspace/               # Config, repos, docs only
    ├── .cursor/
    ├── docs/
    └── website-flip/
```

---

## 🔑 Key Architectural Decisions (Don't Re-Derive)

1. **Section-download caption-sync trap**: `yt-dlp --download-sections` starts at preceding keyframe (unknown 0-10s offset). **Fix is built in**: Pass 2 transcribes the section file's own audio → word timestamps native to that file → `captions_are_clip_relative=true` → zero offset arithmetic.

2. **`TRANSCRIBE_MAX_MINUTES=0` intentional** — truncating source discards clips. The goal is "as many good clips as possible." Keep it opt-in.

3. **`RENDER_WORKERS` auto-scales** — measured 1.02–1.06× on 2 cores. The real overlap win is **network-vs-CPU**, not CPU-vs-CPU.

3. **Gaussian blur scale-invariance**: `sigma = 28/k` must scale with downscale factor. Code derives it; a hand-picked sigma scored SSIM 0.870 (visibly wrong).

4. **`TRANSCRIBE_MAX_MINUTES=0` default is intentional** — truncating source discards clips. Opt-in only.

---

## 🚀 Ready for Production

The pipeline now achieves the target **55 min → 7–9 min** for a 51-min podcast:
- Audio-only discovery fetch: ~30s
- Transcription (tiny + beam=1, no word_ts): ~2-3 min
- Highlight detection: ~3 sec
- Render 5 clips (parallel): ~4-5 min
- **Total: ~7-9 min** (vs 55-60 min before)

All tests pass (29 tests), all Phases 1-6 complete and pushed to `main` at commit `7d15c85`. PR #2 is open with full details.