# POV PIPELINE — M2–M6 BUILD PROMPT (ONE PASS)

You are implementing the REST of the POV pipeline in ONE pass: **M2 discovery+queue,
M3 upload, M4 daemon, M5 notifications, M6 VPS doc**. M1 (headless agent-runner) is
DONE and acceptance-tested. Do not re-open M1.

Work ONLY from this file + the existing repo. Read the files it points at. Implement
everything below, commit per milestone (conventional commits), push to `main` once at
the end. Do NOT pause for review between milestones. There is no reviewer in this loop.

A separate reviewer (Milo) will audit the whole push afterward.

## 0. M1 is done — the dispatch contract is now locked. DO NOT CHANGE IT.

`agent_runner.py` (commit `07ee8f4`) was fixed on the dev machine after live testing.
The current behavior is CORRECT and must be preserved exactly:

- Briefs are written to `<project>/state/briefs/<agent>.attempt<N>.brief.md` and passed
  via `opencode run "<short message>" --file <brief-file>`. The positional message MUST
  come before `--file` (opencode treats a trailing positional as a file path). Never pass
  the brief text as argv — briefs are 12-20 KB and overflow the 8191-char Windows
  `CreateProcess` limit ("command line is too long").
- Subprocess dispatch uses `run_cmd_timed()`: `Popen` with `CREATE_NEW_PROCESS_GROUP`,
  output to `<project>/state/runs/<agent>.attempt<N>.log` (never a pipe), `taskkill /T`
  tree-kill on timeout. `subprocess.run(capture_output=True, timeout=)` is BANNED for
  agent dispatch — on Windows it orphans the node/opencode tree and hangs forever.
- All `subprocess.run` calls use `encoding="utf-8", errors="replace"`. Log tails are
  echoed with stdout/stderr reconfigured utf-8/replace (done at import in agent_runner).
- `read_source_url` reads `00_SOURCE_URL.txt` with `encoding="utf-8-sig"` (BOM-safe).
- The script gate only runs when the scriptwriter **produced** output this run
  (`gate_agent_ran`). Skipped (pre-existing) scripts are NEVER re-judged — re-judging a
  completed project's script archives it and parks the project (that happened to WW1).
- POV agents are NOT registered in opencode's `agent list`; `agent_slug()` falls back to
  slug=None and the `.md` contract embedded in the brief carries the behaviour. Leave that.
- Manifest structure (`state/manifest.json`) and `state/pipeline.log` format: keep stable,
  extend if needed, never remove keys the assembler/uploader reads.

## 1. Where the code lives

- Repo: `https://github.com/dra-allan/milo-portable-system` — branch `main`.
- Dev machine (Windows): `C:\Users\user\Desktop\milo-portable-system`
- Target: Linux VPS. Every path from env/config, never hardcoded (one documented Windows
  default is allowed).
- **HARD RULE (self-contained Milo):** every tool/script called must be vendored INSIDE
  this repo. Runtime deps allowed: `python3`, `node`, `ffmpeg`, Chrome, `opencli` (global
  npm), bundled `milo`/`miloctl`, Google/YouTube APIs (keys via env/config). No new third-
  party Python deps beyond what's already vendored/installed.

## 2. What exists today

Orchestrator `artisan/pov_pipeline/run_pov_pipeline.py` — stages wired:
`scrape`, `agents` (M1, headless), `gate`, `tts` (Gemini, voice Fenrir, multi-key,
resume-safe), `images` (`opencli flow images`, Chrome bridge, profile rotation, hardened —
DO NOT REGRESS), `thumb`, `assemble`, `video` (= images+thumb+assemble).
`--project <NAME> --stage <X>` works. `PROJECTS_DIR` is env-configurable (`POV_PROJECTS_DIR`).
Projects live under `C:\Users\user\Desktop\Milo Video Factory\pov\projects` (Windows default).

New since M1:
- `artisan/pov_pipeline/agent_runner.py` — the headless 7-agent runner (locked, see §0).
- `artisan/pov_pipeline/README.md` — M1 docs. Extend it, don't rewrite.
- `artisan/pov_pipeline/config/pov_channels.yaml` — source curation, **50 verified
  channels, 4 niches** (`pov_immersive_history`, `hypothetical_what_if`,
  `faceless_documentary`, `dark_mystery_narrative`). All handles verified real
  (commit `045b681`). Schema mirrors shorts `niches.yaml` + POV additions
  (`defaults`, `cadence`, `privacy`, `api`). Read it.

Patterns to copy:
- `artisan/youtube-shorts-pipeline/` — `config/niches.yaml`, `src/main.py --mode discover`,
  `data/processed_videos.db` (sqlite dedupe by `video_id`), `src/uploader.py`
  (`python -m src.uploader auth --channel <name>`, `config/youtube_token_<name>.json`).
- `artisan/ranking-shorts-pipeline/` — sibling, read both.

## 3. M2 — Discovery + queue

New files: `artisan/pov_pipeline/discovery.py`, `data/processed_videos.db` (created on
first run).

### 3.1 Config semantics (define now, document in code)
- `require_keywords` (when non-empty): the video **title must contain at least ONE**
  (OR-match) or the video is rejected. Empty list = no title requirement.
- `keywords`: scoring terms (boost the rank score when present in title).
- `negative_keywords`: ANY match in title → reject (OR of rejections). Global +
  per-niche both apply.
- `min_score`: below → reject.
- `min_views` / `preferred_upload_days` (recency) / `min_duration` / `max_duration`
  (seconds, filter AFTER resolving exact durations): hard filters.
- `max_videos`: per channel, per discovery run.
- `max_pages_per_channel` and `quota_guard`: API budget controls.

### 3.2 Flow (quota-aware)
1. `channels.list?forHandle=<@handle>` → channel id + uploads playlist id.
   Handle fails to resolve → log to `state/pipeline.log`, skip channel, never abort.
2. `playlistItems` on the uploads playlist (respect `max_pages_per_channel`, order newest
   first) → candidate video ids + titles.
3. Title filters (require_keywords OR, negative_keywords reject) applied here — cheap.
4. `videos.list?part=contentDetails,statistics` **only for the surviving candidates** of
   the top N channels this run (exact duration + view count + publishedAt). Apply
   min/max_duration, min_views, preferred_upload_days, min_score here.
5. Dedupe: query `data/processed_videos.db` for `video_id` (and any POV row with
   status `done|processing`). Already seen → skip.
6. Score = keyword hits + engagement signal (views within recency). Keep the scoring in
   `discovery.py`, simple, deterministic, documented.
7. Emit ordered queue rows: `state/queue.json` (or a `pov_queue` table in the sqlite DB —
   pick one, document it). Queue item: `{video_id, url, channel_id, niche, title, score,
   enqueued_at, status}`.

**Quota guard:** YouTube Data API = 10k units/day. `search.list` costs 100; prefer
`playlistItems` (1 unit) + `videos.list` (1 unit each). Default discovery run budget:
process at most `max_channels_per_run` channels (default 5, configurable) and `max_videos`
per channel, so a run stays under ~300 units. `quota_guard: true` in config is the default
— refuse to run if estimated units exceed a configured budget.

### 3.3 CLI + queue integration
- `python run_pov_pipeline.py --discover [--niche <name>] [--channels a,b]` — runs
  discovery for the configured niches (or one), appends to the queue, prints a summary,
  does NOT process anything.
- `--input <youtube-url>` (already exists for scrape) also enqueues: after scrape, the
  project is created and the queue row marked processed.
- The daemon (M4) consumes the queue: next item → build project via scrape (or reuse a
  `00_SOURCE_SCRIPT.txt` if the URL was already scraped) → full chain → upload.
- Dedupe must also catch projects that already exist on disk: if `POV_PROJECTS_DIR`
  contains a project named `<video_id>_*`, treat the video as processed. (Project naming
  already encodes the video id: `make_project_name()`.)

### 3.4 Manifest
Add the source video's `video_id`, `channel_id`, `niche`, `score` to `state/manifest.json`
via `write_manifest(extra=...)`. The uploader and notifier want them.

## 4. M3 — Upload stage (ExplaiNation)

### 4.1 Command
`python run_pov_pipeline.py --project <NAME> --stage upload [--privacy unlisted|public]`
New stage `upload`. Read `07_METADATA.txt` (title, description, tags — read
`agents/POV-seo-specialist.md` for the exact format), `04_THUMBNAIL/thumbnail.png`,
and the assembled video `output_pro/*.mp4` (glob for the single output).

### 4.2 Implementation
- Reuse/adapt `artisan/youtube-shorts-pipeline/src/uploader.py` (resumable chunked
  `videos.insert`). Parameterize the token file so it works for any channel
  (`config/youtube_token_explaination.json`).
- Auth one-time: `python -m src.uploader auth --channel explaination` (document in README).
- Config keys (from `pov_channels.yaml` `defaults.privacy`, `defaults.published_at` or
  CLI override): privacy default `unlisted` for review; `published_at` ISO8601 for
  scheduled posts, null = immediate.
- Thumbnail must be a valid image file; if missing, upload WITHOUT thumbnail and warn
  (never fail the batch).
- On success: write `uploaded_video_url` + `youtube_video_id` into `state/manifest.json`,
  mark the queue/DB row `done`, log + notify (M5 hook).
- Tags from `07_METADATA.txt` are the `tags` field; don't exceed 500 chars total.

### 4.3 Test policy
Do NOT upload to the real ExplaiNation channel during testing unless it's a throwaway —
prefer `--privacy unlisted` + a short clip, or a test-only token. Leave a documented
`--dry-run-upload` flag that prints what WOULD be sent (title/description/tags/thumbnail
path/video path/size) without calling the API.

## 5. M4 — Daemon + scheduler

New file: `artisan/pov_pipeline/daemon.py` (+ `cron/pov-daemon.example`).

### 5.1 Modes (via `run_pov_pipeline.py` flags)
- `--once` — single pass: if queue has items and inside posting window, process the
  highest-score item end-to-end (discover if queue empty). Dev default.
- `--daemon` — loop: every `daemon_interval_minutes` (config, default 30), if inside the
  posting window (`cadence.posting_window` e.g. "09:00-21:00", `cadence.timezone`) AND
  queue non-empty → process next item; else sleep and re-check. VPS mode.
- Clean shutdown: handle SIGTERM/SIGINT → finish current step, flush log, exit 0.
- Health line: write `state/pipeline.log` heartbeat each loop tick.

### 5.2 Bounds
- Respect `cadence.videos_per_day` (default 1): never start more than that many NEW video
  pipelines per calendar day. Track count in the sqlite DB (a `pipeline_runs` table).
- Cap concurrency: one project at a time (sequential). No parallelism.
- If a stage hard-fails, mark the queue item `failed` (with reason), log, notify, and
  continue to the next item — the daemon must survive individual project failures.

### 5.3 Scheduler examples
- `cron/pov-daemon.example` — cron line for Linux VPS (e.g. `@reboot` or a short interval
  calling the daemon).
- README section: Windows Task Scheduler example for dev (or `pythonw` + `--daemon`).

## 6. M5 — Notifications (Telegram)

New file: `artisan/pov_pipeline/notify.py`. Config: `config/notify.env` with
`{{TELEGRAM_BOT_TOKEN}}` and `{{TELEGRAM_CHAT_ID}}` placeholders (never real values).
If the file or keys are missing → module is a silent no-op (never crash the pipeline).

### 6.1 API
```python
def make_notifier(config_path: Path) -> Notify  # Notify = Callable[[str, str], None]
```
`notify(event: str, message: str)`. `agent_runner` already accepts a `notify` callable and
invokes it at every lifecycle boundary (project.started, gate.fail, gate.needs_review,
agent.failed, agents.done, chain.abort). Wire `make_notifier` into `run_pov_pipeline.py`
and pass it through to `run_agent_chain` AND the new M2/M3/M4 code.

Events to send (one message per milestone, no spam):
`project.started`, `agents.done`, `gate.fail`, `gate.needs_review`, `images.done`
(with failure count), `video.assembled`, `upload.success` (include the video URL!),
`upload.failed`, `daemon.fatal`, `queue.empty` (optional, daily).

Telegram Bot API: `sendMessage` to `{{TELEGRAM_CHAT_ID}}` with the bot token. No new deps —
use `urllib.request` (stdlib). Timeout 10s, failures logged, never raised.

## 7. M6 — VPS deploy doc

New file: `docs/VPS_DEPLOY.md`. Contents:
- Install: `python3`, `node`, `ffmpeg`, Chrome (Debian), the opencli npm package, the
  bundled milo CLI, repo clone, `python -m venv`, pip install (repo deps only).
- Env vars: `POV_PROJECTS_DIR`, `POV_OPENCODE_MODEL`, `POV_MEMORY_PROJECT`, YouTube API
  key, Gemini keys, Telegram keys — all via a `.env` sourced by the daemon; config
  templates with `{{PLACEHOLDER}}`.
- YouTube auth one-time steps for the upload token.
- Flow Chrome bridge: **FLAG AS HARD RISK.** Flow is a browser-bound Google Labs product;
  whether the bridge + reCAPTCHA survive on a headless VPS is UNKNOWN. The images/thumb
  stages must fail LOUDLY (clear error + notify) if the bridge is down, not silently skip.
  Document the `--check-profiles` preflight (from AUTONOMY_PROMPT §3.6) as the gate.
- cron setup (`cron/pov-daemon.example`), log rotation, systemd unit example (bonus).
- Rollback / backup note: `milo backup`.

## 8. Acceptance criteria (reviewer will check)

- `python -m py_compile` clean on every touched `.py`; `node --check` on any touched `.cjs`.
- `discover` runs against `pov_channels.yaml`, dedupes, emits a queue, prints a summary.
  Unverifiable handles are skipped with log lines, never exceptions.
- `--dry-run-upload` prints the full payload without API calls.
- Daemon: `--once` processes one item; `--daemon` ticks, respects the window + daily cap,
  and SIGTERM exits cleanly. (Timing-dependent parts documented, not fully tested by the
  reviewer on a real video.)
- Notifications: no-op when `notify.env` absent; `make_notifier` imported and threaded
  through every stage.
- No new hardcoded absolute paths except the documented Windows default.
- No secrets committed. All config templates `{{PLACEHOLDER}}`.
- The hardened `images`/`thumb` rotation logic is untouched.
- README updated: new commands (`--discover`, `--once`, `--daemon`, `--stage upload`,
  `--dry-run-upload`), config keys, env vars, VPS notes, known risks.

## 9. Conventions & gotchas

- Python 3.10+ type hints, `Path` over strings, stage fns return bool, stdout/`eprint`
  split, resume-safe checks everywhere, never raise for recoverable failures.
- `opencli` / `opencode` / `milo` are `.CMD` shims on Windows — resolve via
  `shutil.which(...)` as `cmd[0]`, never bare. Subprocess calls use
  `encoding="utf-8", errors="replace"`.
- Do NOT break: `.jpeg` image naming contract, `IMAGE_PROMPTS_BATCH_FINAL.txt` parse
  format, manifest structure, gate thresholds, the locked §0 dispatch contract.
- Do NOT rewrite `run_pov_pipeline.py` wholesale — extend it stage-by-stage.
- Do NOT add new third-party runtime deps. `urllib.request`, sqlite3, argparse, subprocess
  are enough.
- Do NOT automate Google login / reCAPTCHA for Flow.
- Commit per milestone: `feat(pov): M2 discovery+queue`, `feat(pov): M3 upload stage`,
  `feat(pov): M4 daemon`, `feat(pov): M5 telegram notifications`,
  `docs(pov): M6 VPS deploy`. Push once at the end.
