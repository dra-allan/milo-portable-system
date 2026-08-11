# NXS autonomous workflow

NXS runs like the existing Shorts pipeline. No manual watch, private staging, or per-upload approval is part of the operating flow.

## One-time setup

1. Merge the GTA config PR.
2. Append the corrected `niches.nxs.gta_hype.yaml` mapping to `youtube-shorts-pipeline/config/niches.yaml`, replacing the old `gta_hype` mapping rather than creating duplicate YAML keys.
3. Authenticate the two upload targets:

```bat
cd artisan\youtube-shorts-pipeline
python -m src.uploader auth --channel NXS

cd ..\ranking-shorts-pipeline
python -c "from src.publisher import auth; print(auth('NXS'))"
```

4. Enable autonomous uploads in the Shorts pipeline `.env`:

```env
UPLOAD_ENABLED=true
PRIVACY_STATUS=public
UPLOAD_MAX_PER_CHANNEL=6
UPLOAD_MAX_PER_SOURCE=3
UPLOAD_BACKLOG=true
SCHEDULE_BACKLOG_FIRST=true
```

The ranking pipeline publishes automatically when run without `--no-upload`; its channel binding is already `NXS`.

## Runtime loop

Each scheduled sweep does this automatically:

`discover -> title/topic gate -> audio-only download -> captions/Whisper -> highlight scoring -> section download -> vertical render -> title/description -> pacing/caps -> public upload -> database record`

Rendered clips that miss a quota or daily cap stay in the backlog and are uploaded by a later sweep. The system skips duplicate sources, respects the one-source-per-24h discovery guard, caps NXS at six Shorts per day, and spaces uploads to avoid burst posting.

## Daily operation

Run the existing scheduler, not the interactive menu:

```bat
cd artisan\youtube-shorts-pipeline
venv\Scripts\activate
python -m src.main --mode schedule
```

For Lane A, run the ranking scheduler/automation with topic `gta6_countdown`. Do not pass `--no-upload`.

## Surge operation

On Aug 27 after the Extended Look drops, let the normal scheduler collect and publish automatically. The only special operation is resetting the GTA discovery cap between surge sweeps if more than one source is needed that day. Do not manually approve clips; the caps, title gate, negatives, and backlog are the safety system.

## What remains manual

Only one-time setup remains manual: create the NXS YouTube channel, authenticate OAuth once, merge the config, and start the scheduler. After that, publishing is hands-off. Analytics can be reviewed later, but it is not a gate in the pipeline.