# POV Pipeline

Curated channel (or a single URL) to a finished POV narrative documentary on
the **ExplaiNation** YouTube channel, unattended.

```text
curated channels -> discover -> scrape transcript
  -> headless agent chain (7 agents, Milo-aware, gate loop)
  -> TTS -> images -> thumb -> assemble -> upload -> notify
```

All six milestones are in: **M1** headless agent-runner, **M2** discovery +
queue, **M3** upload, **M4** daemon, **M5** Telegram notifications, **M6**
[VPS deploy guide](../../docs/VPS_DEPLOY.md).

---

## Windows control panel

Use [`start_pov_pipeline.bat`](start_pov_pipeline.bat) as the main Windows
launcher. It is an interactive control panel, not just a one-shot script:

```bat
start_pov_pipeline.bat
```

It can discover sources, inspect the queue, process one item, run the daemon,
install or remove a Windows Scheduled Task, stop daemon processes, dry-run or
perform an unlisted upload, check Flow profiles, edit config, run `py_compile`,
open logs, and start the one-time YouTube auth flow.

The scheduled task is named `POV Pipeline Daemon`, starts at Windows logon, and
runs the daemon in the foreground under the repo `.venv`. The daemon itself
controls the real cadence from `config/pov_channels.yaml`:

```yaml
cadence:
  videos_per_day: 1
  posting_window: "09:00-21:00"
  timezone: "{{POV_TIMEZONE}}"
  daemon_interval_minutes: 30
```

Use menu 6 to install/update the task, menu 7 to remove it, and menu 8 to
stop a currently running daemon. Menu 10 always asks before making a real
YouTube upload and uses `unlisted` by default. Menu 9 is the safe payload-only
upload test.

The launcher looks for `%REPO%\\.venv\\Scripts\\python.exe` first, then falls
back to `python` on `PATH`. Keep real secrets in the repo `.env` or untracked
config files. Do not paste API keys into the batch file.

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

```text
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
no Google Python packages at all. A missing thumbnail warns and uploads
without one. Use `--dry-run-upload` before the first real post.

---

## M4 - the daemon (`daemon.py`)

`--once` takes the highest-scoring queued item (running discovery first if
the queue is empty) and drives it through agents, TTS, images, thumbnail,
assembly and upload, notifying at every boundary. `--daemon` does that on a
loop. It respects `cadence.posting_window`, `cadence.timezone`,
`cadence.videos_per_day`, and `cadence.daemon_interval_minutes`.

Windows: use the control panel's menu 6 to install the `POV Pipeline Daemon`
Scheduled Task. Linux: see [`cron/pov-daemon.example`](../../cron/pov-daemon.example)
and the [VPS guide](../../docs/VPS_DEPLOY.md).

SIGTERM/SIGINT set a stop flag checked between stages: the current step
finishes, the log is flushed, exit 0. A project failure marks the item
`failed`; unexpected exceptions fire `daemon.fatal` and the loop continues.

---

## M5 - notifications (`notify.py`)

`make_notifier()` returns the `notify(event, message)` callable every stage is
handed, including `agent_runner`. Missing credentials is a silent no-op;
events still go to `pipeline.log`. Identical messages inside 60 seconds are
dropped. Copy `config/notify.env.template` or set
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the environment.

---

## Config and environment

`config/pov_channels.yaml` provides filters, cadence, privacy and API quota.
`POV_PROJECTS_DIR`, `POV_DATA_DIR`, `POV_STATE_DIR`, `POV_OPENCODE_MODEL`,
`POV_MEMORY_PROJECT`, `YOUTUBE_API_KEY`, Gemini keys and Telegram keys are
environment-driven. All committed templates use `{{PLACEHOLDER}}`; real
credentials are untracked.

---

## Known risks

**Chrome Browser Bridge on a headless VPS:** Google Flow is browser-bound;
whether the bridge plus reCAPTCHA survive on a VPS is **UNKNOWN**. Run
`--check-profiles` before batches. If it is down, images and thumb fail
loudly, notify, and block only those stages. Login is a one-time human step,
never automated.

**Upload safety:** default privacy is `unlisted`. Use the dry-run first.

For full Debian setup, auth, cron, systemd, log rotation and rollback, see
[docs/VPS_DEPLOY.md](../../docs/VPS_DEPLOY.md).
