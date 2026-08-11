# POV PIPELINE AUTONOMY BUILD — Implementation Prompt for Opus

You are implementing a major upgrade to the POV (point-of-view narrative documentary)
video pipeline for **Milo** (chief-of-staff agent) and **Allan** (owner). Build it
entirely, commit cleanly, and push. A separate reviewer (Milo) will audit the code
afterward — write for reviewability.

## 0. Where the code lives

- Repo: `https://github.com/dra-allan/milo-portable-system` — branch `main`.
- Local clone on the dev machine (Windows): `C:\Users\user\Desktop\milo-portable-system`
- Runtime paths below are the **dev machine** values. The target is a **Linux VPS** —
  every path in the code must come from environment/config, never hardcoded.

**HARD RULE (self-contained Milo):** every tool/script this pipeline calls must be
vendored **inside this repo**. At runtime the pipeline may depend on: `python3`,
`node`, `ffmpeg`, Chrome, the `opencli` CLI (installed as a global npm package),
the bundled `milo`/`miloctl` CLI, and the Google/YouTube APIs (keys via env/config).
Do **not** import tools from outside the repo.

## 1. What exists today (do not reinvent, extend it)

Orchestrator: `artisan/pov_pipeline/run_pov_pipeline.py`
- Stages already wired: `scrape` (YouTube transcript via `scripts/youtube-transcript.cjs`),
  `gate` (wordcount 1620–2025 + rewrite-originality overlap scan),
  `tts` (Gemini TTS, voice `Fenrir`, multi-key rotation, resume-safe),
  `images` (`opencli flow images`, Google Flow image gen via the Chrome bridge,
    **profile rotation on rate limits, 30s backoff, completion verification, aborts
    on insufficient credits** — recently hardened, don't regress it),
  `thumb`, `assemble` (ffmpeg via `scripts/pov_assembler_pro.py`),
  `video` (images + thumb + assemble).
- **`run_agents()` is a STUB.** It only prints "WAITING for agent run". This is the
  core thing you are replacing.
- Agent prompts (the LLM "thinking" layer): `artisan/pov_pipeline/agents/*.md`, in order:
  1. `POV-researcher` → `00_RESEARCH_NOTES.txt`
  2. `POV-scriptwriter` → `01_SCRIPT_RAW.txt`
  3. `POV-image-director` → `05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt`
  4. `POV-thumbnail-artist` → `04_THUMBNAIL/THUMBNAIL_PROMPT.txt`
  5. `POV-voice-engineer` → `02_SCRIPT_ELEVENLABS.txt`
  6. `POV-seo-specialist` → `07_METADATA.txt`
  7. `POV-archive-manager` → `COMPLETENESS_REPORT.txt`
- Projects live under `PROJECTS_DIR` (currently hardcoded to
  `C:\Users\user\Desktop\Milo Video Factory\pov\projects`). **Make this
  env-configurable (`POV_PROJECTS_DIR`), defaulting to that path on Windows.**
- TTS: `artisan/pov_pipeline/tts/gemini_tts.py`, `.env` holds the Gemini keys
  (never commit real keys — template with `{{PLACEHOLDER}}`).
- Flow image CLI (vendored plugin): `artisan/flow-cli/` — the `opencli flow images`
  batch command is the interface. Images land as `05_IMAGES/<SEG_ID>.jpeg` and the
  assembler consumes them by exact name. **Do not change the naming contract.**
- Flow default project id (already set in the CLI state):
  `c602178f-3e34-45dd-b745-43fe20474ef8`.
- **Pattern to copy for source curation + upload**: the shorts pipeline at
  `artisan/youtube-shorts-pipeline/` — study `niches.yaml`, `src/main.py --mode
  discover`, `data/processed_videos.db` (sqlite dedupe by `video_id`), the uploader
  module (`src/uploader.py`, `python -m src.uploader auth --channel <name>`,
  `config/youtube_token_<name>.json`). The newer `artisan/ranking-shorts-pipeline/`
  is a sibling — read both, copy the discovery/upload patterns.

## 2. The goal

From "a URL or a curated channel" to **a finished video sitting on the ExplaiNation
YouTube channel**, fully autonomous:

```
curated channels ─► discover (criteria filter) ─► scrape transcript
      ► headless agent chain (7 agents, Milo-aware, gate loop)
      ► TTS ► images ► thumb ► assemble ► upload ► notify
```

with a **scheduler** so it can run routine batches on a VPS, and **Telegram
notifications** so nobody has to watch it.

## 3. Component specs

### 3.1 Source curation & discovery (mirror the shorts pipeline)
- New config file `artisan/pov_pipeline/config/pov_channels.yaml` (template with
  placeholders): curated channel list, keywords, negative keywords, min views,
  preferred_upload_days (recency window), min/max duration (long-form ~8–15 min),
  min_score threshold, max videos per channel per run, upload cadence.
- A `discover` command that, per channel, pulls recent videos via the YouTube Data
  API (`search`/`playlistItems`), filters by the criteria, **dedupes against a
  sqlite DB** (`data/processed_videos.db` — same layout as the shorts pipeline,
  plus a POV table) so nothing is reprocessed, and emits an ordered queue of URLs.
- Both URL ingestion (`--input <youtube-url>`) and channel discovery must feed the
  same queue.

### 3.2 Headless agent-runner (THE core piece — replaces the stub)
- A runner that executes the 7 agents **headless via the opencode CLI** and checks
  each expected output file landed before advancing.
- Verify exact non-interactive syntax on the dev machine (`opencode run --help`)
  and use it (e.g. `opencode run "<brief>"`), preferring flags for agent selection
  and machine-readable output if available. Wrap it so the exact invocation is one
  function and documented.
- **Milo-awareness contract (mandatory):** opencode loads the Milo persona from
  `AGENTS.md` (it's in the user's config), but the runner must ALSO:
  1. Before each agent run, write/refresh a per-project `state/manifest.json`
     (project path, source URL, stage, last output sizes, timestamps) that the
     headless run can read.
  2. Pass each agent a structured brief that includes: the agent `.md` prompt
     contents, the project directory, the exact output file to write, the previous
     stage's output (path), and the manifest path.
  3. Instruct each headless run to record its outcome to Milo's memory via the
     bundled `milo` CLI (`milo remember ... --project pov-pipeline`), so Milo stays
     aware of every session and can answer "what's the pipeline doing".
  4. The runner itself logs every lifecycle event to `state/pipeline.log` and to
     Milo memory.
- **Gate loop:** after `POV-scriptwriter`, run the existing `script_gate`. If FAIL,
  dispatch `POV-scriptwriter` again with the gate's failure report appended to the
  brief. Cap at 3 retries, then mark the project `NEEDS_REVIEW`, notify, and move on.
- Resume-safe: if an output file for a stage already exists, skip that agent
  (mirror `run_agents`' existing skip behavior).
- Timeouts and per-stage error capture; any hard agent failure → mark project
  `NEEDS_REVIEW`, notify, do NOT crash the whole batch.

### 3.3 Upload stage (ExplaiNation channel)
- New stage `upload`: reads `07_METADATA.txt` (title, description, tags — read
  `agents/POV-seo-specialist.md` for the exact format), `04_THUMBNAIL/thumbnail.png`,
  and the assembled MP4 (`output_pro/*.mp4`, glob for the single output) and pushes
  to YouTube via the API v3 `videos.insert` (resumable chunked upload).
- Reuse/adapt the shorts pipeline uploader rather than writing a new one.
- Auth: `python -m src.uploader auth --channel explaination` one-time step →
  `config/youtube_token_explaination.json`. The upload stage must accept the
  channel/niche key as a parameter so it's testable and reusable.
- Privacy/schedule configurable in the POV config (default: unlisted for review,
  optional `publishedAt` for scheduled routine posts).

### 3.4 Scheduler / daemon
- Two modes:
  - `--once` — single pipeline run (dev).
  - `--daemon` — loop mode: every N minutes (configurable), if within the enabled
    posting window and queue is non-empty, process the next item; sleep otherwise.
    This is the VPS mode.
- Provide drop-in cron examples for the VPS (`cron/pov-daemon.example`) and a
  Windows Task Scheduler example for dev.
- The daemon must be killable cleanly (SIGTERM), log rotation, and a health line in
  `state/pipeline.log`.

### 3.5 Notifications (Telegram)
- A `notify` module: send to Telegram via the Bot API (`sendMessage`). Config via
  env/config file `config/notify.env` with `{{TELEGRAM_BOT_TOKEN}}` and
  `{{TELEGRAM_CHAT_ID}}` placeholders. Never commit real values.
- Events to notify: project started, agent chain done, gate FAIL (needs review),
  image stage failure count, video assembled, upload success (URL!), upload failure,
  daemon fatal error. One message per milestone, no spam.
- The notification module is called from every stage boundary — centralize it.

### 3.6 Chrome profile launcher (best-effort — known open problem)
- Flow image generation requires the Chrome Browser Bridge profiles to be OPEN
  (verified: closed profile → `BROWSER_CONNECT` error; the Flow site does NOT need
  to be open, the login cookie persists). There are 6 profiles aliased
  `flow-1`..`flow-6` on the dev machine.
- Build a `--check-profiles` preflight that runs `opencli profile list`, verifies
  the configured `--flow-profiles` are connected, and a launcher script
  (`scripts/flow_profiles_up.ps1` for Windows, `.sh` for VPS) that opens each
  profile's Chrome. Do NOT attempt to log in — that's human, one-time.
- **Flag this in your README as the hard dependency + risk for the VPS: Flow is a
  browser-bound Google Labs product; whether reCAPTCHA and the bridge survive on a
  headless VPS is UNKNOWN and must be tested before declaring VPS readiness.** If
  the bridge can't run on the VPS, the pipeline still runs everything else and the
  `images`/`thumb` stages are the only blockers — design for that (clear error, not
  silent failure).

### 3.7 Config & VPS portability
- `config/pov.yaml` (or a `settings.py`): projects dir, flow profiles list, upload
  channel, posting window, daemon interval, model choices, notify config path.
- Every path via `Path` + env override. Windows dev defaults + Linux VPS overrides
  documented in README.
- All secrets via env/untracked config files with `{{PLACEHOLDER}}` templates.

## 4. Sequencing (implement in this order, commit after each milestone)

- **M1 — Headless agent-runner.** Works on an existing project folder
  (replaces the `run_agents` stub; `--project X --stage agents`). Verify by running
  it against an already-scraped project and confirming all 7 output files appear.
- **M2 — Discovery + queue.** `discover` command + sqlite dedupe + `--input` URL.
- **M3 — Upload stage.** Auth flow documented + tested on a throwaway/short video.
- **M4 — Daemon + scheduler.**
- **M5 — Notifications.**
- **M6 — VPS deploy doc** (`docs/VPS_DEPLOY.md`): install steps, Chrome/bridge test,
  cron, env, secrets.

## 5. Acceptance criteria (Milo will check these)

- `node --check` / `python -m py_compile` clean on all touched files.
- `run_agents` no longer a stub — M1 demonstrably produces all 7 files headless.
- Resume-safe everywhere; a re-run of any stage never regenerates finished work.
- No new hardcoded absolute paths except the documented Windows default.
- No secrets committed; all config templates use `{{PLACEHOLDER}}`.
- `images`/`thumb` stages still call `opencli flow images` / `image-gen` with the
  profile rotation intact (don't regress the hardened code).
- The full chain runs end-to-end once on the dev machine with a real small project
  (that's the M1/M3 test evidence).
- README updated: new commands, config keys, env vars, VPS notes, known risks
  (Chrome bridge on VPS).

## 6. Conventions & gotchas

- Follow existing repo style: Python 3.10+ type hints, `Path` over strings, stage
  functions return bool, prints to stdout / `eprint` to stderr, resume-safe checks.
- Windows + PowerShell: `opencli` is a `.CMD` shim — call it via
  `shutil.which("opencli")` resolved path as `cmd[0]`, never bare `"opencli"`.
  The repo already has this pattern — copy it.
- ffmpeg: the assembler already locates it; don't depend on a global ffmpeg if you
  add any new video-side step — reuse the existing lookup.
- Don't break: the `.jpeg` image naming contract, the `IMAGE_PROMPTS_BATCH_FINAL.txt`
  parse format, the manifest structure the assembler reads, or the gate thresholds.
- Commit style: conventional commits (`feat(pov): ...`, `fix(pov): ...`), one concern
  per commit, milestone-per-PR if possible, push to `main`.

## 7. What to NOT do

- Do NOT rewrite `run_pov_pipeline.py` wholesale — extend it stage-by-stage.
- Do NOT try to automate Google login / reCAPTCHA for Flow.
- Do NOT replace the hardened `images` rotation logic.
- Do NOT add new third-party runtime dependencies beyond what's vendored in the repo.
- Do NOT commit real API keys, tokens, or the `.env`.
- Do NOT upload to YouTube as part of your testing unless you're sure it's a
  throwaway video — ask first, or leave upload as a documented step for M3.
