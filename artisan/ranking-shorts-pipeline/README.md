# Ranking Shorts Pipeline

Builds monetisable top-5 countdown Shorts end to end: sources organic clips,
ranks them 5 -> 1, composes the edit with FFmpeg, writes and voices the
commentary, and publishes.

This is a **separate pipeline** from `artisan/youtube-shorts-pipeline`. It
borrows that pipeline's conventions and copies its patterns, but imports
nothing from it, writes to its own `data/`, and authenticates with its own
OAuth tokens (`youtube_token_ranking_<channel>.json`). The shorts pipeline is
in production; nothing here can destabilise it. The TTS is likewise a *copy* of
`artisan/gemini_tts_pipeline`, living in `ranking_tts/`.

## What it replaces

The manual workflow this automates is: find five clean clips, drop them in
CapCut, blur out any on-screen text, add rank numbers with gold/silver/bronze
strokes, title each clip, write a one-liner per clip and generate a voice-over,
drop in sound effects, add pull-in transitions with swooshes, punch in on the
opening clip, export, upload. Every one of those steps is a module here.

## Pipeline

```
discover ──► download ──► vet ──► rank ──► write copy ──► voice-over
              ▲            │                                  │
              └── reject ──┘                                  ▼
                                          SFX ──► render clips ──► stitch ──► upload
```

| Stage | Module | What it does |
|---|---|---|
| Discover | `sourcing.py` | `ytsearchdate` queries + configured creator/hashtag pages, metadata-only first pass |
| Vet | `vetting.py` | rejects commentary / music beds / static clips, OCRs on-screen text into blur boxes, finds the action peak, de-duplicates |
| Rank | `ranker.py` | scores and assigns 5 -> 1 |
| Copy | `scriptwriter.py` | clip titles + one voice-over line each, then SFX matching |
| Voice | `ranking_tts/` | forked Gemini TTS, one file per rank |
| Render | `assembler.py` + `overlays.py` | per-clip 1080x1920 render, then the stitch |
| Publish | `publisher.py` | YouTube upload on its own OAuth token |

## Setup

```bash
cd artisan/ranking-shorts-pipeline
python -m venv venv && venv\Scripts\activate      # Windows
pip install -r requirements.txt

copy config\.env.template config\.env             # then fill it in
python -m src.main --mode test                    # environment check
```

Required: FFmpeg on PATH, and `OVERLAY_FONT` pointing at a font that exists on
this machine (Impact is the genre standard). `--mode test` verifies both, plus
reports which optional features are available.

Sound effects go in `assets/sfx/` using the filenames in the `sfx_map` block of
`config/ranking.yaml`. `swoosh.mp3` is the important one - it plays under every
transition and under the opening zoom.

Authenticate the upload channel once:

```bash
python -c "from src.publisher import auth; print(auth('ranking_fishing'))"
```

## Running

```bash
# build one video for a topic, no upload, to eyeball the output first
python -m src.main --mode once --topic fishing_moments --no-upload

# what would be sourced and how it would rank - no download of the good ones,
# no render
python -m src.main --mode source --topic fishing_moments

# fully autonomous: least-recently-run topic, build, publish
python -m src.main --mode auto

# re-render from a saved plan (data/plans/<slug>.json) after tweaking style
python -m src.main --mode assemble --plan data/plans/fishing_moments_1234.json

# publish anything rendered but not yet uploaded
python -m src.main --mode upload
```

For a schedule, point Task Scheduler or cron at `--mode auto`. There is no
long-lived scheduler process on purpose: a crashed daemon is invisible, a failed
scheduled task is not.

## The vetting rules, and why they are strict

The reference workflow's requirement is that source clips have **no
commentary, no music, ideally no on-screen text**. That is not an aesthetic
preference, it is what separates a transformative edit from a reupload - and
it is where the monetisation problem in this niche comes from. So:

- **Commentary** - transcribed, then measured as words per second. Over
  `max_words_per_second`, the clip already has a narrator and ours would talk
  over it.
- **Music bed** - percussive energy plus beat regularity. A boat engine is loud
  but has no beat; a song has both. Either signal alone is easy to fool.
- **On-screen text** - OCR'd and **blurred**, not rejected. A good clip with a
  caption is still a good clip, and masking it costs less than discarding it.
  Past `max_text_coverage` the frame is more overlay than footage, and then it
  is rejected.
- **Static clips** - scene-change density. The same measurement locates the
  action peak, which is where the cut is centred and where the sound effect
  lands.
- **Duplicates** - perceptual hash, not just URL. The same moment is reuploaded
  across dozens of accounts, and URL history alone will happily let you publish
  it twice.

Expect most candidates to be rejected. That reject rate *is* the product. If a
topic starves, add queries before loosening thresholds.

## Ordering

A countdown plays 5 -> 1, and the naive assignment makes the weakest clip #5 -
which puts it in the first three seconds, the only part most viewers see. So
the best clip takes **#1** (the payoff, last), the runner-up opens at **#5**
(the hook), and the rest fill 2-4 rising toward #1.

## Notes and limits

- **Platform coverage.** YouTube search is driveable and therefore autonomous.
  TikTok and Instagram have no stable search endpoint, so those are configured
  as explicit creator or hashtag pages in `extra_sources` and walked for new
  uploads each run. Adding a topic means adding queries; adding a *platform*
  means finding the pages by hand once.
- **Optional dependencies degrade, never crash.** No `faster-whisper` means no
  commentary check; no `pytesseract` means no text blurring; no `librosa` means
  no music detection. `--mode test` tells you which are missing.
- **Overlay text is passed to FFmpeg by file**, never inline. See the module
  docstring in `overlays.py`: inline text with an apostrophe or a percent sign
  either aborts the render or, worse, draws nothing while reporting success.
- **Credit.** Every build's description lists each clip's original uploader and
  URL. Keep it that way.

## Testing

```bash
python -m pytest tests -q
```

FFmpeg-dependent tests skip when ffmpeg or a font is missing. The overlay tests
count drawn pixels rather than trusting the exit code, because the bug they
guard against exited zero.
