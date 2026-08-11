# POV Pipeline

Curated channel (or a single URL) to a finished POV narrative documentary on
the **ExplaiNation** YouTube channel, unattended.

```
curated channels -> discover -> scrape transcript
  -> headless agent chain (7 agents, Milo-aware, gate loop)
  -> TTS -> images -> thumb -> assemble -> upload -> notify
```

All six milestones are in: **M1** headless agent-runner, **M2** discovery +
queue, **M3** upload, **M4** daemon, **M5** Telegram notifications, **M6**
[VPS deploy guide](../../docs/VPS_DEPLOY.md).

---

## Commands

```bash
# --- source curation (M2) ---------------------------------------------
python run_pov_pipeline.py --discover                     # all niches
python run_pov_pipeline.py --discover --niche hypothetical_what_if
python run_pov_pipeline.py --discover --channels @Invicta,@LEMMiNO
python run_pov_pipeline.py --queue                        # show the queue

# --- one project, stage by stage ---------------------------------------
python run_pov_pipeline.py "https://youtube.com/watch?v=..." --stage scrape
python run_pov_pipeline.py --project <NAME> --stage agents     # M1
python run_pov_pipeline.py --project <NAME> --stage gate
python run_pov_pipeline.py --project <NAME> --stage tts
python run_pov_pipeline.py --project <NAME> --stage video       # images+thumb+assemble
python run_pov_pipeline.py --project <NAME> --stage upload      # M3

# --- autonomous (M4) ----------------------------------------------------
python run_pov_pipeline.py --once      # next queue item, end to end
python run_pov_pipeline.py --daemon    # loop on a schedule (VPS)

# --- preflight + notifications -----------------------------------------
python run_pov_pipeline.py --check-profiles --flow-profiles flow-1,flow-2
python notify.py --test
```

### Flags worth knowing

| Flag | What it does |
| --- | --- |
| `--dry-run-agents` | Print the exact `opencode` invocation per agent, run nothing. |
| `--dry-run-upload` | Print the full upload payload, call no API. |
| `--model provider/model` | Model for the headless agent runs. |
| `--gate-retries N` | Scriptwriter re-dispatches after a gate FAIL (default 3). |
| `--agent-timeout SECONDS` | Override the per-agent budget. |
| `--flow-profiles a,b` | Google Flow profiles to rotate through. |
| `--privacy` / `--published-at` | Upload visibility and scheduling. |
| `--skip-upload` | `--once`/`--daemon`: stop after assembly. |
| `--ignore-window` | `--once`: run outside the posting window. |
| `--no-memory` | Skip the `milo remember` writes. |
| `--no-notify` | Disable Telegram for this run. |

---

## M1 - the headless agent-runner (`agent_runner.py`)

`run_agents()` is no longer a stub. For each of the 7 agents it:

1. **Refreshes `state/manifest.json`** (project path, source URL, stage,
   per-output byte sizes, timestamps, per-agent results, gate state) so the
   headless run can read the current truth.
2. **Writes a structured brief** to
   `state/briefs/<agent>.attempt<N>.brief.md`: the agent's `.md` contract
   verbatim, the project directory, the exact output path, the previous
   stage's output, and the manifest path. `%PROJECT_DIR%` is resolved.
3. **Dispatches** it: `opencode run "<short message>" --file <brief>`. The
   brief goes in a file because briefs run 12-20 KB, which overflows the
   8191-char Windows `CreateProcess` limit when passed inline. The positional
   message must come **before** `--file`.
4. **Verifies** the expected file exists and is non-empty before advancing.
5. **Logs** to `state/pipeline.log`, `state/runs/<agent>.attempt<N>.log` and
   Milo's memory.

Dispatch uses `Popen` with `CREATE_NEW_PROCESS_GROUP` and a file (never a
pipe) for output, with a `taskkill /T` tree-kill on timeout.
`subprocess.run(capture_output=True, timeout=...)` is **banned** here: on
Windows it orphans the node/opencode tree and hangs forever.

**Gate loop.** After `POV-scriptwriter`, the existing `script_gate` runs
(injected as `gate_fn`; thresholds are never re-implemented). On FAIL the
draft is moved to `state/rejected/` and the scriptwriter is re-dispatched
with the failure report appended, up to 3 times, then the project is parked
`NEEDS_REVIEW` and the batch moves on. The gate only judges a script the
scriptwriter **produced this run** - a skipped, pre-existing script is never
re-judged.

**Resume-safe.** A non-empty output file means the agent is skipped.

---

## M2 - discovery and the queue (`discovery.py`)

Sources live in [`config/pov_channels.yaml`](config/pov_channels.yaml):
50 curated channels across four niches (`pov_immersive_history`,
`hypothetical_what_if`, `faceless_documentary`, `dark_mystery_narrative`).

**Filter order**, cheapest first:

1. `require_keywords` - when non-empty the title must contain **at least one**
   (OR match), else reject.
2. `negative_keywords` - **any** match rejects. Per-niche plus
   `global_negative_keywords`.
3. Dedupe: the `processed_videos` ledger, live `pov_queue` rows, and any
   `<video_id>_*` folder already in `POV_PROJECTS_DIR`.
4. `videos.list` for the survivors only, then `min_duration` / `max_duration`
   / `min_views` / `preferred_upload_days`.
5. `min_score`, then `max_videos` per channel per run.

**Score** (deterministic, 0.35-1.00):

```
0.35 base
+ 0.35 * min(keyword_hits, 3) / 3
+ 0.15 * max(0, 1 - age_days / preferred_upload_days)
+ 0.15 * min(1, log10(views) / 6)
```

**Quota.** The API allows 10,000 units/day. `search.list` costs 100 and is
never used; `channels.list` (1) + `playlistItems` (1/page) + `videos.list`
(1 per 50 ids) are. A default run touches 5 channels x 2 pages, well under
300 units. The guard estimates the spend before the first request and refuses
to start when it exceeds `api.quota_budget` (default 500). Unresolvable
`@handles` are logged and skipped, never raised.

**Storage** is one sqlite file, `data/processed_videos.db` (created on first
run). The queue is a table, not a JSON file, so nothing can half-write it:

| Table | Purpose |
| --- | --- |
| `processed_videos` | dedupe ledger, keyed by `video_id` |
| `pov_queue` | ordered work items: `queued` / `processing` / `done` / `failed` / `needs_review` |
| `pipeline_runs` | one row per started pipeline; the daily-cap ledger |

---

## M3 - upload (`uploader.py`)

Reads `07_METADATA.txt` (tolerant title / description / tags parser matching
the plain-text format `agents/POV-seo-specialist.md` emits, tags capped at
YouTube's 500-char total), `04_THUMBNAIL/thumbnail.png` and the single
`output_pro/*.mp4`, then does a resumable chunked `videos.insert`.

One-time auth, on a machine with a browser:

```bash
python -m uploader auth --channel explaination
# -> config/youtube_token_explaination.json   (secret, gitignored)
```

After that the upload runs on `google-api-python-client` when it is
installed, and otherwise on the standard library alone (refresh token ->
access token -> resumable PUT). Copy the token file to the VPS and it needs
no Google Python packages at all.

Behaviour: privacy defaults to `unlisted` for review; `--published-at`
schedules (the video stays private until then); a missing thumbnail warns and
uploads without one rather than failing the batch; on success
`youtube_video_id` and `uploaded_video_url` are written into
`state/manifest.json`, the queue row is marked `done`, and the URL is sent to
Telegram.

**Test with `--dry-run-upload` first.** It prints the exact payload and calls
nothing.

---

## M4 - the daemon (`daemon.py`)

`--once` takes the highest-scoring queued item (running discovery first if
the queue is empty) and drives it through agents, TTS, images, thumbnail,
assembly and upload, notifying at every boundary. `--daemon` does that on a
loop.

Bounds, all from `cadence` in `pov_channels.yaml`:

* `posting_window` (`"09:00-21:00"`, may wrap midnight) + `timezone`
* `videos_per_day` - counted in `pipeline_runs`, so a restart cannot reset it
* `daemon_interval_minutes`
* one project at a time. No parallelism anywhere.

SIGTERM/SIGINT set a stop flag checked between stages: the current step
finishes, the log is flushed, exit 0. A project failure marks the item
`failed` with a reason and the loop continues; an unexpected exception fires
`daemon.fatal` and the loop **still** continues.

Scheduling examples: [`cron/pov-daemon.example`](../../cron/pov-daemon.example)
and a systemd unit in the [VPS guide](../../docs/VPS_DEPLOY.md).

### Windows Task Scheduler (dev)

```powershell
$py     = "C:\Users\user\Desktop\milo-portable-system\.venv\Scripts\pythonw.exe"
$script = "C:\Users\user\Desktop\milo-portable-system\artisan\pov_pipeline\run_pov_pipeline.py"
$action  = New-ScheduledTaskAction -Execute $py -Argument "`"$script`" --daemon" `
                                   -WorkingDirectory (Split-Path $script)
$trigger = New-ScheduledTaskTrigger -AtLogOn
$set     = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "POV Pipeline Daemon" -Action $action `
                       -Trigger $trigger -Settings $set
```

`pythonw.exe` keeps it windowless. For a cron-style single pass instead, swap
`--daemon` for `--once` and use a repeating time trigger.

---

## M5 - notifications (`notify.py`)

`make_notifier()` returns the `notify(event, message)` callable every stage
is handed, including `agent_runner`. Copy
`config/notify.env.template` to `config/notify.env`, or just set
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the environment - a
`{{PLACEHOLDER}}` resolves from the env var of the same name.

**Missing config is a silent no-op.** Events still go to the pipeline log, and
no stage can ever fail because notifications are not set up. Identical
messages inside 60 seconds are dropped so a retry loop cannot flood the chat.

Events: `project.started`, `agents.done`, `gate.fail`, `gate.needs_review`,
`agent.failed`, `chain.abort`, `images.done`, `images.failed`,
`video.assembled`, `upload.success` (carries the URL), `upload.failed`,
`discover.done`, `queue.empty`, `daemon.started`, `daemon.stopped`,
`daemon.fatal`.

---

## Config keys

`config/pov_channels.yaml`. `defaults` is inherited by every niche unless the
niche overrides the key.

| Key | Default | Meaning |
| --- | --- | --- |
| `min_duration` / `max_duration` | 480 / 1500 | source length bounds, seconds |
| `min_views` | 25000 | hard floor |
| `preferred_upload_days` | 60 | recency window (also feeds the score) |
| `min_score` | 0.50 | reject below this |
| `max_videos` | 2 | per channel, per run |
| `upload_channel` | `explaination` | OAuth token key |
| `privacy` | `unlisted` | upload visibility |
| `published_at` | `null` | ISO8601 scheduled publish |
| `cadence.videos_per_day` | 1 | daily cap on NEW pipelines |
| `cadence.posting_window` | `09:00-21:00` | when the daemon may start work |
| `cadence.timezone` | `{{POV_TIMEZONE}}` | window timezone; unset = local |
| `cadence.daemon_interval_minutes` | 30 | loop tick |
| `api.youtube_api_key` | `{{YOUTUBE_API_KEY}}` | discovery key |
| `api.quota_guard` | `true` | refuse runs over budget |
| `api.quota_budget` | 500 | units per discovery run |
| `api.max_pages_per_channel` | 2 | 50 videos per page |
| `api.max_channels_per_run` | 5 | channels touched per run |

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `POV_PROJECTS_DIR` | `C:\Users\user\Desktop\Milo Video Factory\pov\projects` | project folders. **Set this on the VPS.** |
| `POV_DATA_DIR` | `<pipeline>/data` | sqlite ledger + queue |
| `POV_STATE_DIR` | `<pipeline>/state` | pipeline-level log |
| `POV_CHANNELS_YAML` | `config/pov_channels.yaml` | source config |
| `POV_OPENCODE_BIN` | from PATH | opencode executable |
| `POV_OPENCODE_MODEL` | unset | `--model` for every agent run |
| `POV_AGENT_TIMEOUT` | per-agent (15-40 min) | timeout override |
| `POV_GATE_MAX_RETRIES` | 3 | scriptwriter retries |
| `POV_MILO_BIN` | `milo` / `mylo` / vendored | Milo CLI |
| `POV_MEMORY_PROJECT` | `pov-pipeline` | Milo memory project |
| `YOUTUBE_API_KEY` | unset | discovery |
| `POV_YOUTUBE_TOKEN` | `config/youtube_token_<channel>.json` | upload token |
| `POV_OAUTH_CLIENT_SECRETS` | shorts `credentials.json` | one-time auth |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | unset | notifications |

The Windows path above is the **only** hardcoded absolute path in the
pipeline, it lives in `povconfig.py`, and it is a default, not a requirement.
All secrets come from env or untracked files; every committed template uses
`{{PLACEHOLDER}}`.

---

## Project layout

```
<POV_PROJECTS_DIR>/<PROJECT>/
  00_SOURCE_URL.txt                     where it came from
  00_SOURCE_SCRIPT.txt                  scraped transcript
  00_RESEARCH_NOTES.txt                 POV-researcher
  01_SCRIPT_RAW.txt                     POV-scriptwriter   <- gated
  02_SCRIPT_ELEVENLABS.txt              POV-voice-engineer
  04_THUMBNAIL/THUMBNAIL_PROMPT.txt     POV-thumbnail-artist
  04_THUMBNAIL/thumbnail.png            thumb stage
  05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt   POV-image-director
  05_IMAGES/<SEG_ID>.jpeg               images stage  (naming contract: do not change)
  06_AUDIO/                             TTS
  07_METADATA.txt                       POV-seo-specialist
  COMPLETENESS_REPORT.txt               POV-archive-manager
  output_pro/*.mp4                      assembler
  NEEDS_REVIEW.txt                      present only when parked
  state/manifest.json                   pipeline state the agents read
  state/pipeline.log                    lifecycle log (rotates at 5 MB, 3 kept)
  state/briefs/                         every brief, as dispatched
  state/runs/                           raw opencode output per attempt
  state/rejected/                       gate-rejected drafts
```

---

## Known risks

**Chrome Browser Bridge on a headless VPS (the big one).** Google Flow image
generation needs the Chrome Browser Bridge profiles to be OPEN; a closed
profile produces `BROWSER_CONNECT`. Flow is a browser-bound Google Labs
product, and **whether the bridge and reCAPTCHA survive on a headless VPS is
UNKNOWN and must be tested before declaring VPS readiness.** If the bridge
cannot run there, everything except `images` and `thumb` still works, and
both of those fail loudly (missing-segment report, `images.failed`
notification, daemon marks the item failed) rather than silently. Login is a
one-time human step and is never automated. Run `--check-profiles` before
every batch.

**API quota.** Discovery is cheap by design, but an aggressive
`max_channels_per_run` plus `max_pages_per_channel` will burn the daily 10k.
The guard refuses to start a run it estimates as too expensive; raise
`api.quota_budget` deliberately, not reflexively.

**opencode CLI drift.** The non-interactive surface has changed before. Flag
support is probed at runtime rather than hardcoded, but a breaking change to
`opencode run` itself would stop the chain. `--dry-run-agents` prints the
exact invocation, which is the fastest way to confirm.

**Agent output is verified by existence, not quality.** Only the scriptwriter
is quality-gated (wordcount + rewrite-originality). The other six are checked
for "file exists and is non-empty"; `COMPLETENESS_REPORT.txt` from
`POV-archive-manager` is the backstop.

**Upload is irreversible-ish.** Default privacy is `unlisted` for exactly
that reason. Watch one all the way through before switching
`defaults.privacy` to `public`.
