# YouTube Shorts Pipeline — Performance Analysis & Speedup Plan

**Date:** 2026-08-06  
**Pipeline:** `artisan/youtube-shorts-pipeline`  
**Machine:** 3.9 GB RAM, CPU-only (no GPU)  
**Test Case:** 51-min podcast (`T1-VoN7Zjrs`, "Make Friends With Your Fears — Baus Rufo")

---

## Current Performance (as measured)

| Stage | Time | % of Total | Notes |
|-------|------|------------|-------|
| Download (cold) | ~5–15 min | — | 1080p video + audio (opus) |
| Audio extract | ~30 sec | <1% | ffmpeg, fast |
| **Transcription (chunked fallback)** | **~47 min** | **~85%** | 11 chunks × ~4.3 min each @ 1.15× realtime |
| Highlight detection | ~3 sec | <1% | Negligible |
| Render 5 clips (sequential) | ~10 min | ~15% | 1–2 min per clip |
| **Total (cold)** | **~55–60 min** | 100% | For **5 min of output** |

**Why transcription is slow:**  
Single-pass streaming (`base` model, int8, `beam_size=5`, `word_timestamps=True`, `vad_filter=True`) builds a full-file mel-spectrogram array (~1.2 GB for 51 min) → **OOM on 3.9 GB machine** → falls back to 5-min chunks. Each chunk re-runs the same heavy settings on all audio (including silence), running at ~1.15× realtime.

---

## Root Causes

| # | Cause | Impact |
|---|-------|--------|
| 1 | Chunked fallback uses `beam_size=5` + `word_timestamps=True` + VAD on full chunks | 100× slower than single-pass would be |
| 2 | Single-pass OOMs because `faster-whisper` allocates `(n_frames, 400)` float64 mel array | Can't use fast path for >30 min files |
| 3 | Downloads full 1080p video when only audio is needed | 5–10× unnecessary bandwidth + disk I/O |
| 4 | Rendering is purely sequential | 5 clips × 2 min = 10 min wall time |
| 5 | Always transcribes 100% of source | Podcasts/movies front-load hooks; last 60% often low value |

---

## Target: 55 min → **5–10 min total** (5–10× speedup)

| Levers | Expected Speedup | Effort |
|--------|------------------|--------|
| **Tiny model + beam=1 + no word timestamps** | **10–20×** on transcription | Low (config flags) |
| **Audio-only download** | 5–10× download time | Low (yt-dlp format) |
| **Parallel rendering (2 workers)** | 2× render time | Low (ThreadPoolExecutor) |
| **Head-only transcription (`--max-source-minutes`)** | 3–4× transcription | Medium (flag + slice logic) |
| **Smart re-transcribe: transcribe full with tiny, then re-transcribe only selected clips with base+word_ts** | Best of both | Medium (two-pass logic) |

---

## Recommended Implementation Order

### Phase 1 — Config & Audio-Only (this week, ~2 hrs)
Add to `config.py` + `.env.template`:
```python
# Transcription tuning
TRANSCRIBE_MODEL=tiny          # base|small|medium|large
TRANSCRIBE_BEAM=1              # 1=greedy, 5=beam
TRANSCRIBE_WORD_TIMESTAMPS=false  # true|false
TRANSCRIBE_MAX_MINUTES=0       # 0=full, N=first N minutes only

# Download tuning
DOWNLOAD_AUDIO_ONLY=true       # true|false
```

Wire into `VideoTranscriber.transcribe_audio()` and `YouTubeDownloader.ydl_opts`.

### Phase 2 — Parallel Rendering (30 min)
Wrap clip rendering loop in `ThreadPoolExecutor(max_workers=2)` (CPU-bound, 2 keeps RAM safe).

### Phase 3 — Head-Only Mode (1 hr)
Add `--max-source-minutes N` / `--head N` CLI flag → slice audio before transcription → still cache full transcript path for resume logic.

### Phase 4 — Two-Pass Smart Transcribe (optional, later)
- Pass 1: `tiny` + `beam=1` + no word_ts on full/head audio → fast highlights
- Pass 2: for each selected clip, re-extract that audio segment → transcribe with `base` + `word_timestamps=True` → precise captions
- Saves full-file heavy transcribe; only ~2–3 min of audio re-transcribed at high quality

---

## Quick Wins Available Now (no code)

| Action | How |
|--------|-----|
| Test `tiny` model quality | `WHISPER_MODEL=tiny python -m src.main --mode test` |
| Force greedy beam | `TRANSCRIBE_BEAM=1` in `.env` |
| Disable word timestamps | `TRANSCRIBE_WORD_TIMESTAMPS=false` in `.env` |
| Audio-only download | Edit `downloader.py` `ydl_opts['format'] = 'bestaudio/best'` |

---

## Expected Timeline After Phase 1+2

| Stage | Time (51-min podcast) |
|-------|----------------------|
| Download (audio only) | ~30–60 sec |
| Audio extract | ~15 sec |
| Transcription (`tiny` + beam=1, no word_ts) | **~2–3 min** |
| Highlight detection | ~3 sec |
| Render 5 clips (parallel ×2) | **~4–5 min** |
| **Total** | **~7–9 min** |

---

## Files to Touch

| File | Changes |
|------|---------|
| `src/config.py` | Add 5 new env vars, wire into `Config` |
| `src/downloader.py` | Read `DOWNLOAD_AUDIO_ONLY` → set `ydl_opts['format']` |
| `src/transcriber.py` | Read `TRANSCRIBE_*` vars → pass to `model.transcribe()` |
| `src/main.py` | Add `--max-source-minutes` flag, slice audio before transcribe |
| `src/video_editor.py` | Wrap render loop in `ThreadPoolExecutor` |
| `config/.env.template` | Document all new vars with defaults |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| `tiny` model WER too high for niche keywords | Test on 2–3 real videos; fallback to `base` if needed |
| Greedy beam misses words in noise | Keep `base` + beam=5 as opt-in for "final" renders |
| Parallel render OOMs | Cap at 2 workers; monitor RAM |
| Head-only misses late highlights | Default 0 = full; opt-in for discovery runs |

---

## Decision Needed

1. **Default model for discovery runs:** `tiny` vs `small`? (`small` is ~2× slower than `tiny` but ~30% better WER)
2. **Word timestamps default:** `false` for speed, `true` for caption accuracy? (Can be per-clip in Phase 4)
3. **Head default:** `0` (full) or `15` (podcast)? — Suggest `0` (safe default), user opts in via flag.

---

## Appendix: Current `.env` (machine-specific, gitignored)

```ini
# Working data lives OUTSIDE the milo portable folder
DATA_DIR=C:\Users\user\milo-workspace\shorts-data\data
TEMP_DIR=C:\Users\user\milo-workspace\shorts-data\temp
SHORTS_DIR=C:\Users\user\milo-workspace\shorts-data\shorts
LOG_DIR=C:\Users\user\milo-workspace\shorts-data\logs
DB_PATH=C:\Users\user\milo-workspace\shorts-data\data\processed_videos.db

# Preserved from old workspace
YOUTUBE_API_KEY=<redacted>
```

---

*Report generated from session 2026-08-06. All commits pushed to `main`.*

---

## Pipeline Specification

### Architecture Overview

The YouTube Shorts Pipeline is a **single-video, stage-based processor** that transforms a long-form source video into vertical Shorts (1080×1920, ≤60s). It is designed for **resumability** — every stage caches its output so a crash or re-run reuses prior work.

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  1. Fetch   │──▶│ 2. Extract  │──▶│ 3. Transcribe│──▶│ 4. Highlight │──▶│ 5. Render   │
│  (download) │   │  (audio)    │   │  (Whisper)  │   │  (score)    │   │  (ffmpeg)   │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
      │               │                 │                 │                 │
      ▼               ▼                 ▼                 ▼                 ▼
  cache: video    cache: wav        cache: JSON       cache: picks    output: mp4
  file + meta     audio             transcript        segments        (shorts/)
```

**Key invariant:** No stage re-runs if its cached output exists and `--force` is not set.

---

### Component Breakdown

| Module | Class / Function | Responsibility | Cache Artifact |
|--------|------------------|----------------|----------------|
| `src/main.py` | `ShortsPipeline` | Orchestrator — wires stages, manages stats, CLI entry | — |
| | `process_video_for_shorts()` | Single-video pipeline driver | — |
| | `run_niche()` | Batch mode (stub — channel discovery not implemented) | — |
| | `build_parser()` / `run_test_mode()` | CLI + environment doctor | — |
| `src/downloader.py` | `YouTubeDownloader` | yt-dlp wrapper + **media library** | `data/temp/<id>__<title>.ext` + `.info.json` |
| | `_load_library()` / `_save_library()` | JSON index: `video_id → {path, title, duration, ...}` | `data/library.json` |
| | `find_local_video()` | Resume: library → id-prefixed glob → `.info.json` scan | — |
| | `download_video()` | Public API: returns metadata, downloads only if missing | — |
| `src/transcriber.py` | `VideoTranscriber` | faster-whisper wrapper (single-pass + chunked fallback) | `data/transcripts/<video_id>.json` |
| | `_transcribe_whole()` | Single streaming pass (fast, OOMs on long files) | — |
| | `_transcribe_chunked()` | 5-min chunks, overlap dedup, guaranteed forward progress | — |
| | `extract_audio_from_video()` | ffmpeg → 16 kHz mono wav | `data/temp/<id>_audio.wav` |
| `src/processor.py` | `ContentProcessor` | Scores transcript segments, selects non-overlapping clips | — |
| | `score_segment()` | Multi-factor scoring (density, keywords, hooks, punctuation, filler) | — |
| | `find_highlight_segments()` | Builds variable-length candidates, greedy NMS, fallback to top-N | — |
| `src/video_editor.py` | `VideoEditor` / `render_short()` | Single ffmpeg filter chain: extract → crop/pad → captions → loudnorm | `data/shorts/<safe_title>/NN_<title>.mp4` |
| | `create_ass()` | ASS subtitle generation with rebased timestamps | temp `.ass` file |
| `src/database.py` | `ShortsDatabase` | SQLite: `processed_videos`, `generated_shorts`, `uploaded_shorts` | `data/processed_videos.db` |
| `src/config.py` | `Config` / `config` | Env-driven config (`.env` + defaults), niches.yaml loader | — |
| `src/utils.py` | `get_temp_dir()`, `get_data_dir()`, `setup_logger()` | Path resolution (now config-driven), logging | — |

---

### Stage Details

| Stage | Input | Output | Cache Key | Resume Condition |
|-------|-------|--------|-----------|------------------|
| **1. Fetch** | Video ID / URL | Video file + metadata | `library.json[video_id].video_path` | File exists ≥ 64 KB |
| **2. Extract** | Video file | 16 kHz mono WAV | `data/temp/<id>_audio.wav` | File exists |
| **3. Transcribe** | WAV | Segment list `[{text,start,end,words?}]` | `data/transcripts/<id>.json` | File exists & valid JSON |
| **4. Highlight** | Transcript + niche keywords | `[{start,end,text,score}]` (max 5) | None (fast, recomputed) | — |
| **5. Render** | Video + highlights | Vertical MP4s (1080×1920) | `data/shorts/<safe_title>/NN_<title>.mp4` | File exists |
| **6. Upload** (optional) | Rendered clips | YouTube Short IDs | `uploaded_shorts` table | Row exists |

---

### Configuration System

| Layer | Source | Precedence |
|-------|--------|------------|
| Defaults | `Config` class constants | Lowest |
| `.env.template` | Documented defaults | Reference only |
| `config/.env` | Machine-specific (gitignored) | **Highest** |
| CLI flags | `--mode`, `--niche`, `--force`, etc. | Override env |

**Key env vars (current):**
```ini
# Paths (external data layout)
DATA_DIR, TEMP_DIR, SHORTS_DIR, LOG_DIR, DB_PATH

# Processing
MIN_SEGMENT_LENGTH=15
MAX_SEGMENT_LENGTH=60
MAX_CLIPS_PER_VIDEO=5
MIN_GAP_BETWEEN_CLIPS=30
WHISPER_MODEL=base
WHISPER_DEVICE=cpu
UPLOAD_ENABLED=false
```

**Proposed additions (Phase 1):**
```ini
TRANSCRIBE_MODEL=tiny
TRANSCRIBE_BEAM=1
TRANSCRIBE_WORD_TIMESTAMPS=false
TRANSCRIBE_MAX_MINUTES=0
DOWNLOAD_AUDIO_ONLY=true
```

---

### External Interfaces

#### CLI (`python -m src.main`)
| Command | Purpose |
|---------|---------|
| `--mode once <video_id\|URL> [--niche X] [--force] [--from-library]` | Process one video end-to-end |
| `--mode library [--all] [--niche X] [--force]` | Process all downloaded videos |
| `--mode schedule` | APScheduler: 9 AM / 2 PM / 7 PM daily |
| `--mode test` | Doctor: ffmpeg, whisper, yt-dlp, niches, render, DB |

#### Data Files (External `shorts-data/`)
```
shorts-data/
├── temp/                    # Downloads + working files
│   ├── <id>__<title>.ext    # Source video (id-prefixed)
│   ├── <id>.info.json       # yt-dlp metadata (title, duration, uploader)
│   └── <id>_audio.wav       # Extracted 16 kHz mono
├── transcripts/             # Cached transcripts (JSON)
│   └── <id>.json            # [{text,start,end,words?,confidence}]
├── shorts/                  # Rendered output
│   └── <Safe Title>/        # One dir per source video
│       ├── 01_<title>.mp4   # Clip 1
│       └── 02_<title>.mp4   # Clip 2 ...
├── data/
│   ├── library.json         # Media library index
│   └── processed_videos.db  # SQLite (videos, shorts, uploads)
└── logs/
    └── pipeline.log         # Rotating log
```

---

### Resume / Caching Mechanism

| Cache | Invalidation Trigger |
|-------|----------------------|
| **Download** | `--force` OR file missing OR file < 64 KB |
| **Audio WAV** | Source video changed (mtime) OR `--force` |
| **Transcript** | `--force` (audio unchanged → transcript reused) |
| **Highlights** | Recomputed every run (fast, depends on niche keywords) |
| **Rendered clips** | `--force` OR output file missing |
| **Database** | Never auto-invalidated; `db.clear_video(video_id)` for manual reset |

**Library index** (`library.json`) is updated on every successful download and on resume hits, so the media library is the source of truth for "what do we have locally."

---

### Niche Configuration (`config/niches.yaml`)

Each niche defines:
- `channels`: YouTube channel IDs (for batch discovery — stubbed)
- `keywords`: Boost terms for highlight scoring
- `min_duration` / `max_duration`: Source video length filters
- `min_score`: Score floor (0.0 = disabled, higher = stricter)

Current niches: `gaming`, `trading`, `movies`, `podcast`, `software_development`, `music_production`, `electronics_hardware`, `pharmacy_and_science`, `game_development`.

---

### Known Gaps / Stubs

| Feature | Status | Notes |
|---------|--------|-------|
| Channel discovery (`search_videos_by_channel`) | **Stub** — returns `[]` | Needs YouTube Data API key + pagination |
| Scheduled niche batch (`run_niche`) | Depends on channel discovery | Currently no-ops |
| OAuth upload (`uploader.py`) | Skeleton only | Requires `YOUTUBE_OAUTH_CLIENT_SECRETS` + token flow |
| Multi-video batch from library | Works (`--mode library --all`) | Limited by sequential processing |
| GPU acceleration | Not configured | `WHISPER_DEVICE=cpu` only |

---

### Storage Contracts

| Path Pattern | Owner | Format | Max Size |
|--------------|-------|--------|----------|
| `temp/<id>__<title>.ext` | Downloader | Video container (webm/mp4/mkv) | Unbounded (source) |
| `temp/<id>.info.json` | Downloader | yt-dlp info dict (subset) | ~1–5 KB |
| `temp/<id>_audio.wav` | Transcriber | 16 kHz mono PCM | ~30 MB / hour |
| `transcripts/<id>.json` | Transcriber | Segment array | ~500 KB / hour |
| `shorts/<title>/NN_<title>.mp4` | VideoEditor | H.264 + AAC, 1080×1920 | 1–10 MB / clip |
| `data/library.json` | Downloader | `{id: {path,title,duration,...}}` | ~1 KB / video |
| `data/processed_videos.db` | Database | SQLite 3 | ~1 MB / 1000 videos |

---

*End of Pipeline Specification.*