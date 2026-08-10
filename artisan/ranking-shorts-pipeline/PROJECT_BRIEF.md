# Ranking Shorts Pipeline: Project Brief

## Goal

Build autonomous ranking Shorts from original-looking source clips: discover
YouTube videos, collect clean moments, rank them from 5 to 1, add commentary
voice-over, text and sound design with FFmpeg, stitch the countdown, and publish
to the ranking channel.

## Scope

The pipeline owns the full flow:

`YouTube discovery -> metadata filtering -> download -> vet -> dedupe -> rank -> copy -> forked TTS -> FFmpeg render -> stitch -> upload`

It uses yt-dlp for the same YouTube search/channel-feed pattern as the existing
Shorts pipeline. Search queries and optional YouTube channel handles live in
`config/ranking.yaml`; the shipped config has no TikTok or Instagram source.

## Isolation rules

- Existing `artisan/youtube-shorts-pipeline` is not modified or imported.
- Existing `artisan/gemini_tts_pipeline` is not modified or imported.
- Ranking TTS is a copied fork in `ranking_tts/`.
- Ranking has its own SQLite state and namespaced OAuth tokens.
- The Git repository contains code and safe configuration only, never downloads,
  temp renders, databases, voice files, logs, plans, exports or tokens.

## Runtime layout

Set `VIDEO_FACTORY_ROOT` to the same shared parent used by the other pipelines.
The ranking pipeline then writes only under:

```text
<VIDEO_FACTORY_ROOT>/ranking-shorts-pipeline/
  data/                 database, plans, downloaded clips, voice files, logs
  temp/                 OCR frames, stage renders and FFmpeg working files
  output/               final MP4 exports
  config/               runtime OAuth token files
```

If `VIDEO_FACTORY_ROOT` is not set, the default is outside the repository:
`%LOCALAPPDATA%/DRA/VideoFactory` on Windows or
`~/.local/share/dra-video-factory` on Linux/macOS. Relative path overrides are
anchored to this external runtime root, not the shell's current directory.

## Ranking behaviour

The strongest clip is #1 and appears last as the payoff. The runner-up opens at
#5 as the hook. Remaining clips fill #4 through #2 in rising quality. This is
intentional: opening with the weakest clip is a retention tax.

## Deployment checklist

- [ ] Set `VIDEO_FACTORY_ROOT` to the shared external video-factory parent.
- [ ] Copy `config/.env.template` to `config/.env`; do not commit it.
- [ ] Set `GEMINI_API_KEYS` if model copy or ranking TTS is enabled.
- [ ] Set `OVERLAY_FONT` to an installed font, preferably Impact.
- [ ] Install FFmpeg and verify it is on PATH.
- [ ] Install Python dependencies from `requirements.txt`.
- [ ] Add licensed sound effects to `assets/sfx/`, especially `swoosh.mp3`.
- [ ] Add YouTube queries and/or channel handles under the topic's `channels`.
- [ ] Authenticate the ranking upload channel with the ranking publisher.
- [ ] Run `python -m src.main --mode test`.
- [ ] Run `python -m src.main --mode source --topic fishing_moments`.
- [ ] Run one render with `--no-upload` and review it manually.
- [ ] Confirm the final MP4 is in the external `output/` folder.
- [ ] Confirm no runtime files appeared under the Git checkout.
- [ ] Keep `UPLOAD_PRIVACY=private` until the first render is approved.
- [ ] Only then enable the scheduled `--mode auto` run.

## Acceptance criteria

A deployment is ready when it can produce a 1080x1920 MP4 under 59 seconds,
with valid rank overlays, readable clip titles, commentary audio, transitions,
and a credit-bearing description, while all runtime files remain outside the
repository and the existing Shorts pipeline still passes its own tests.
