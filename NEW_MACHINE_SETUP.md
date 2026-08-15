# NEW MACHINE SETUP — the one prompt

**Purpose:** on a brand-new machine, the only manual action is installing
opencode and pasting the prompt below. opencode (as Milo) then installs
everything itself: Python, Node, opencode's own deps, the repo, milo, all
pipelines, all dependencies, state restore, and daemons.

This file is the **single source of truth** for what a machine needs. Any
time a component changes (new pipeline, new dependency, new daemon), update
this file in the same commit as the change. That is the rule Allan set.

---

## The prompt to paste into fresh opencode

```
You are Milo. Set this machine up completely from scratch. Do it in order and
verify each step before moving on. Run commands yourself with Bash; do not ask
me to type things.

STEP 1 — Prereqs: verify python, npm, and git exist. Install anything missing
via winget (Python.Python.3.12, OpenJS.NodeJS.LTS). Verify with --version.

STEP 2 — Clone the repo:
git clone https://github.com/dra-allan/milo-portable-system
cd milo-portable-system

STEP 3 — Install Milo itself:
python -m pip install -e .
milo install
(milo install prompts for secrets and the vault path. Tell me which prompts
need my input; everything else should be automated.)

STEP 4 — Make yourself Milo in opencode:
milo sync opencode
Report where AGENTS.md and agent/milo.md landed.

STEP 5 — Install every pipeline. For EACH directory under artisan/ that has a
deploy/setup_vps.ps1, run it with its state bundle if present. The standard
ones are:
  artisan/youtube-shorts-pipeline  -> deploy/setup_vps.ps1
  artisan/ranking-shorts-pipeline  -> deploy/setup_vps.ps1
For pipelines without a setup script, read their README.md and install what
it specifies (venv + requirements.txt). Skip any pipeline that is clearly
experimental or has no install instructions; list what you skipped.

STEP 6 — Daemons: install scheduled tasks from each pipeline's setup script.
Report every scheduled task registered.

STEP 7 — Restore state: for each pipeline, restore its state bundle (the
.tar.gz in the repo under artisan/<pipeline>/ or pointed to by its README).
If a bundle is missing, tell me what needs transferring — do NOT fabricate
tokens.

STEP 8 — Verify everything:
milo doctor
Then run each pipeline's --mode test (or equivalent) and report pass/fail.

STEP 9 — Report back with a table: component | version/commit | status. List
anything you could not install and exactly why.
```

---

## Component manifest (THE upgrade contract)

Every component a machine needs. **When you change one of these, update its
row in the same commit.** Rows marked `auto` are handled by the prompt.

| # | Component | Install method | Upgrade action |
|---|---|---|---|
| 1 | Python 3.12 | winget, step 1 | bump version row if base changes |
| 2 | Node LTS / npm | winget, step 1 | bump version row |
| 3 | opencode | `npm i -g opencode-ai` | version change only |
| 4 | milo (miloctl) | `pip install -e .` from repo | auto on next pull + pip |
| 5 | milo persona in opencode | `milo sync opencode` | auto on next run |
| 6 | YouTube Shorts pipeline | `artisan/youtube-shorts-pipeline/deploy/setup_vps.ps1` | update script or README, note here |
| 7 | Ranking Shorts pipeline | `artisan/ranking-shorts-pipeline/deploy/setup_vps.ps1` | update script or README, note here |
| 8 | POV pipeline (ExplaiNation) | `artisan/pov_pipeline` — README-driven, `.venv` + requirements | update README, note here |
| 9 | Money Matrix pipeline | `artisan/mm_pipeline` / `artisan/mm_agents` | note here when provisioned |
| 10 | Campaign clipper | `artisan/campaign-clipper-pipeline` — README + requirements.txt | update README, note here |
| 11 | Gemini TTS | `artisan/gemini_tts_pipeline/requirements.txt` | note here |
| 12 | Flow CLI / opencli | `artisan/flow-cli` (npm) + `npm i -g @jackwener/opencli` (provides the `opencli` bin that `milo-computer` MCP shells out to) | note here |
| 13 | opencli Chrome extension | `artisan/opencli-extension` — load unpacked in Chrome (Flow automation) | note here |
| 14 | Composio MCP | `pip install composio` (v3 SDK) + `COMPOSIO_API_KEY` in `$MILO_HOME/.env`; `milo sync opencode` emits a hosted Tool Router MCP endpoint (`miloctl/composio_mcp.py`) | add key to .env, re-sync |
| 15 | ffmpeg | winget `Gyan.FFmpeg` (setup_vps step 1) | version change only |
| 16 | YouTube cookies | `cookies.txt` at repo root (bytes exact, not via RDP drag) | re-export after auth changes; note here |
| 17 | State bundles | per-pipeline `.tar.gz` (tokens, .env, db) | bundle on change, note here |

## Upgrade accounting rule

1. Any code change to a pipeline → update its row (6-13) with the new commit
   or install notes, in the same commit as the pipeline change.
2. New pipeline added → add a row.
3. New system dependency (ffmpeg, tesseract, model weights) → add a row.
4. Daemon name / schedule changed → note it in the pipeline's row.
5. This file is in the repo, so a fresh machine gets the updated manifest on
   clone. That is the point.

## Known failure modes (learned the hard way)

- **RDP drag-and-drop corrupts files** (NUL bytes / UTF-16 mangling). Never
  transfer cookies.txt or bundles by dragging. Use base64 paste:
  `[IO.File]::WriteAllBytes("C:\...\cookies.txt", [Convert]::FromBase64String("<b64>"))`
- **`milo sync opencode` writes a GitHub PAT into opencode.json** if milo's
  state holds one. Treat it as exposed; rotate after install.
- **`milo-mcp.exe` needs the Python Scripts dir on PATH** or MCP servers fail
  to start. Verify with `where milo-mcp`.
- **state/ conflicts on pull** on the VPS: run `scripts/fix-vps-state.ps1`.
- **Vanilla opencode is NOT Milo.** The persona, memory, and MCP servers only
  exist after `milo sync opencode`. Don't skip it.