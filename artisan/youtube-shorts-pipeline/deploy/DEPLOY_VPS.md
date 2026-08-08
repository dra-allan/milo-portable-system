# Deploy the Shorts pipeline to a VPS

The pipeline is fully headless: local Whisper, rule-based titles, no LLM, no
browser. Everything it needs to run unattended is either in git or in the
state bundle below. Two files carry the identity you *must* move by hand
(they are gitignored / not committed): the OAuth client secrets and the
per-channel tokens.

## What ships where

| Piece | Lives in git | Must be migrated |
|---|---|---|
| Code (src/, config/, requirements.txt) | yes | clone |
| `config/niches.yaml` | yes | clone |
| `config/.env` | no | bundle |
| `credentials.json` (OAuth client secrets) | no | bundle |
| `config/youtube_token*.json` (per-channel auth) | no | bundle |
| `data/processed_videos.db` (dedup + stats) | no | bundle |
| `data/library.json`, `data/transcripts/`, `data/clip_plans/` | no | bundle |
| `data/shorts/` (rendered MP4s), `data/temp/`, venv | no | NOT shipped (regenerable) |

## Steps

### On the old machine (Windows)

1. `cd artisan/youtube-shorts-pipeline`
2. `python deploy/bundle_state.py --out state_bundle.tar.gz`
   - This packs tokens, credentials, niches, .env, DB, transcripts and clip
     plans. It skips venv, shorts, temp — those are recreated on the VPS.

### On the VPS (Ubuntu 22.04+)

1. `git clone https://github.com/dra-allan/milo-portable-system.git`
2. `cd milo-portable-system/artisan/youtube-shorts-pipeline`
3. `scp user@old-machine:state_bundle.tar.gz /tmp/` (from the old machine)
4. `bash deploy/setup_vps.sh /tmp/state_bundle.tar.gz`

The script installs `ffmpeg` + python venv, `pip install -r
requirements.txt`, restores the bundle, rewrites the Windows `SHORTS_DIR` path
to a Linux one, runs `--mode test` to verify, and installs a user systemd
service that runs `--mode schedule` (which keeps running and fires on the
`RUN_TIMES` cron expressions in `.env`).

## Post-install sanity

- `venv/bin/python -m src.main --mode test` — all green?
- `venv/bin/python -m src.main --mode discover` — shows candidates per bound
  niche without downloading anything.
- `systemctl --user status shorts-schedule` — active (running).
- `journalctl --user -u shorts-schedule -f` — watch a sweep live.
- Whisper models download to `~/.cache/huggingface` on first run, so the first
  scheduled run may be slow before the model cache is warm.

## Notes

- **Memory:** `MemoryMax=4G` is set in the unit. If the box has less RAM, lower
  it and set `RENDER_WORKERS=1` + `TRANSCRIBE_WINDOW_MINUTES=10` in `.env`.
- **Upload quota:** the pipeline is paced (see `.env` `UPLOAD_PACING_*`) and
  capped (`SCHEDULE_MAX_TOTAL`), so it will not blow the daily Data API quota.
- **Tokens are portable:** OAuth tokens contain a refresh token and work from
  any machine with the same `credentials.json`. You should not need to re-run
  `python -m src.uploader auth --channel <name>` on the VPS.
- **If a token has expired/been revoked:** re-auth on the VPS with
  `venv/bin/python -m src.uploader auth --channel flick_shorts` (or
  `capital_mindset`), which opens a localhost flow — tunnel it if headless.
