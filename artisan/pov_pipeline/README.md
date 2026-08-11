# POV Pipeline

URL (or curated channel) to a finished POV narrative documentary on the
**ExplaiNation** YouTube channel.

```
curated channels -> discover -> scrape transcript
  -> headless agent chain (7 agents, Milo-aware, gate loop)
  -> TTS -> images -> thumb -> assemble -> upload -> notify
```

Status: **M1 (headless agent-runner) is in.** M2 discovery, M3 upload,
M4 daemon, M5 notifications and M6 VPS deploy are next.

---

## Commands

```bash
# 1. Scrape a source transcript into a new project folder
python run_pov_pipeline.py "https://youtube.com/watch?v=..." --stage scrape

# 2. Run the 7 agents HEADLESS on that project  (M1)
python run_pov_pipeline.py --project <NAME> --stage agents

# 3. Everything else, unchanged
python run_pov_pipeline.py --project <NAME> --stage gate
python run_pov_pipeline.py --project <NAME> --stage tts
python run_pov_pipeline.py --project <NAME> --stage video   # images + thumb + assemble
```

No `--stage` plus a URL still runs the whole thing: scrape, agents (with
the gate loop), then TTS.

### M1 flags

| Flag | What it does |
| --- | --- |
| `--dry-run-agents` | Print the exact `opencode` invocation for every agent and run nothing. Use this first. |
| `--model provider/model` | Model for the headless runs. |
| `--gate-retries N` | Scriptwriter re-dispatches after a gate FAIL (default 3). |
| `--agent-timeout SECONDS` | Override the per-agent budget. |
| `--no-memory` | Skip the `milo remember` writes. |

---

## The headless agent-runner (`agent_runner.py`)

`run_agents()` used to be a stub that printed "WAITING for agent run". It
now delegates to `agent_runner.run_agent_chain()`, which for each of the 7
agents:

1. **Refreshes `state/manifest.json`** (project path, source URL, stage,
   per-output byte sizes, timestamps, per-agent results, gate state) so the
   headless run can read the current truth.
2. **Builds a structured brief**: the agent's `.md` contract verbatim, the
   project directory, the exact output path to write, the previous stage's
   output, and the manifest path. `%PROJECT_DIR%` is resolved for the agent.
3. **Dispatches** it through the opencode CLI.
4. **Verifies** the expected file exists and is non-empty before advancing.
5. **Logs** the outcome to `state/pipeline.log` and to Milo's memory.

### The opencode invocation

One function knows the syntax: `build_opencode_command()`. It produces

```
<opencode> run [--agent <slug>] [--model <provider/model>] "<brief>"
```

* The binary is resolved with `shutil.which("opencode")` so the Windows
  `.CMD` shim works as `cmd[0]` (same pattern the images stage uses for
  `opencli`). Override with `POV_OPENCODE_BIN`.
* Supported flags are **probed once** from `opencode run --help` and cached.
  A version that renames or drops a flag degrades to a bare
  `opencode run "<brief>"` instead of dying on an unknown option.
* `--agent` is only passed when the slug from the `.md` frontmatter (e.g.
  `pov-scriptwriter`) is actually registered with opencode. It is a
  convenience, not a dependency: the full contract is inside the brief.
* The brief is a single argv element, never shell-interpolated, so quotes,
  newlines and Windows backslashes in paths survive.

### Milo-awareness contract

* opencode loads the Milo persona from `AGENTS.md` (user config).
* On top of that, every brief instructs the run to record its outcome:
  `milo remember "..." --project pov-pipeline -c context -t pov agent`.
* The runner independently writes its own memories for chain start, every
  agent completion, gate PASS/FAIL, NEEDS_REVIEW and chain completion. So
  "what is the pipeline doing?" is answerable even if an agent forgets.
* The `milo` CLI is resolved via PATH and falls back to
  `python -m miloctl.cli` from this repo, which is always vendored. A
  missing CLI is a warning, never a pipeline failure.

### Gate loop

After `POV-scriptwriter`, the **existing** `script_gate` runs (injected as
`gate_fn`; thresholds are never re-implemented here). On FAIL:

1. The rejected `01_SCRIPT_RAW.txt` is moved to
   `state/rejected/01_SCRIPT_RAW.txt.<timestamp>.rejected` (kept as
   evidence, and so the resume-skip stays correct).
2. `POV-scriptwriter` is re-dispatched with the captured failure report
   appended to the brief and a "rewrite from scratch" instruction.
3. Capped at 3 retries. After that the project is marked `NEEDS_REVIEW`
   (`NEEDS_REVIEW.txt` + manifest status), a notification fires, and the
   batch moves on. Nothing crashes.

### Resume semantics

An output file that exists and is non-empty means the agent is **skipped**,
same as the old stub. Re-running `--stage agents` on a finished project
regenerates nothing. Delete a specific output to force one agent to re-run.

### Failure handling

Hard failure, timeout, or "exited fine but never wrote the file" all mark
the project `NEEDS_REVIEW`, notify, and return a failed `ChainResult`. If
the file landed but the process exit code grumbled, the artifact is trusted
and the noise is recorded in the manifest.

---

## Config & env vars

| Variable | Default | Purpose |
| --- | --- | --- |
| `POV_PROJECTS_DIR` | `C:\Users\user\Desktop\Milo Video Factory\pov\projects` | Where project folders live. **Set this on the VPS.** |
| `POV_OPENCODE_BIN` | resolved from PATH | opencode executable |
| `POV_OPENCODE_MODEL` | unset | `--model` for every agent run |
| `POV_OPENCODE_AGENT` | unset | Force one opencode agent for every stage |
| `POV_AGENT_TIMEOUT` | per-agent (15-40 min) | Timeout override, seconds |
| `POV_GATE_MAX_RETRIES` | `3` | Scriptwriter retries after a gate FAIL |
| `POV_MILO_BIN` | `milo` / `mylo` / vendored `miloctl` | Milo CLI |
| `POV_MEMORY_PROJECT` | `pov-pipeline` | Milo memory project key |

The Windows path above is the **only** hardcoded absolute path in the
pipeline, and it is a documented default, not a requirement.

Source curation lives in `config/pov_channels.yaml` (curated POV /
documentary / hypothetical channels, keywords, filters, cadence). Secrets in
that file are `{{PLACEHOLDER}}` templates; real values come from env or
untracked config files. **Never commit real keys or `.env`.**

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
  state/pipeline.log                    lifecycle log (rotated at 5 MB, 3 kept)
  state/rejected/                       gate-rejected drafts
```

---

## Known risks

**Chrome Browser Bridge on a headless VPS (the big one).** Google Flow image
generation needs the Chrome Browser Bridge profiles to be OPEN. A closed
profile produces `BROWSER_CONNECT`. Flow is a browser-bound Google Labs
product, and **whether reCAPTCHA and the bridge survive on a headless VPS is
UNKNOWN and must be tested before declaring VPS readiness.** If the bridge
cannot run there, everything except `images` and `thumb` still works: those
two stages must fail loudly, never silently. Login is a one-time human step
and is never automated.

**opencode CLI drift.** The non-interactive surface has changed before. Flag
support is probed at runtime rather than hardcoded, but a breaking change to
`opencode run` itself would stop the chain. `--dry-run-agents` prints the
exact invocation, which is the fastest way to confirm.

**Agent output is verified by existence, not quality.** Only the scriptwriter
is quality-gated (wordcount + rewrite-originality). The other six are checked
for "file exists and is non-empty". `COMPLETENESS_REPORT.txt` from
`POV-archive-manager` is the backstop.
