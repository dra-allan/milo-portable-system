# POV PIPELINE — FINISHING PROMPT FOR OPUS/CLAUDE

You are finishing the POV pipeline automation in
`C:\Users\user\Desktop\milo-portable-system`. The goal: Allan pastes ONE
YouTube URL and gets a finished `FINAL_<VIDEO_ID>.mp4`. Zero manual steps.

Target command (MUST work end to end):

```powershell
python artisan\pov_pipeline\run_pov_pipeline.py `
  --url "https://www.youtube.com/watch?v=XXXX" `
  --stage full `
  --flow-profiles "flow-account-1,flow-account-2"
```

Output: `C:\Users\user\Desktop\Milo Video Factory\pov\projects\<PROJECT>\output_pro\<VIDEO_ID>\FINAL_<VIDEO_ID>.mp4`

---

## ⚠️ VERIFIED DISPATCH FACTS (tested 2026-08-11 on this machine — do not redo)

These were proven with live subprocess tests on this exact machine. Design around
them or the pipeline fails silently.

1. `opencode run --help` via resolved shim path works (return 0, help on stderr).
2. Nested `opencode run <message>` works non-interactively in a subprocess
   (~38s, return 0, no stdin hang). Capture stdout AND stderr — logs go to stderr.
3. `--agent <name>` ONLY works if the agent's frontmatter has **`mode: all`**.
   With `mode: subagent`, opencode silently prints
   `agent "X" is a subagent, not a primary agent. Falling back to default agent`
   and runs the generic `build` agent instead — which will NOT follow your
   300-line POV prompt correctly even though it may still write a file.
4. `tools:` is NOT a valid agent frontmatter field. opencode hard-fails config
   validation: `Expected object | undefined, got "Write" tools`. Use
   `permission:` instead (e.g. `permission: { edit: allow }`).
5. **Agent discovery roots at `--dir`, not the process cwd.** Agents registered in
   `artisan/pov_pipeline/.opencode/agent/` are NEVER found when you dispatch with
   `--dir <project>` where the project lives under `Milo Video Factory\pov\projects\`
   (outside the repo). opencode prints `agent "X" not found. Falling back to default
   agent`. So DO NOT register agents in the repo. Instead `run_agents()` must WRITE
   the 7 agent files into `<project_dir>/.opencode/agent/` before dispatching
   (that dir is the `--dir` target, so discovery works — verified). This is also
   self-contained per project and keeps `state/` clean.
6. A full dispatch (register agent + run + write file) takes ~50-100s per agent on
   this machine. Set per-agent timeout ≥ 600s, and expect 7 agents ≈ 6-12 min.

The correct dispatch recipe (all pieces individually verified):

```python
import shutil, subprocess
opencode = shutil.which("opencode")           # -> C:\...\npm\opencode.CMD
agent_dir = project_dir / ".opencode" / "agent"
agent_dir.mkdir(parents=True, exist_ok=True)
# write <agent_dir>/pov-<agent>.md with frontmatter mode: all + body
r = subprocess.run(
    [opencode, "run", "--agent", f"pov-{agent}",
     "--dir", str(project_dir), "--pure", message],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
)
# GATE: outfile must exist and be non-empty. returncode alone is NOT sufficient
# (the subagent-mode fallback also returns 0).
```

---

## VERIFIED CURRENT STATE — do not re-research, do not duplicate

Read these files before changing anything. They are ground truth.

1. **Orchestrator:** `artisan/pov_pipeline/run_pov_pipeline.py`
   - Stages already exist: `scrape`, `gate`, `tts`, `images`, `thumb`, `assemble`, `video`.
   - `run_agents()` is a **STUB** — it only prints "WAITING for agent run". It does
     NOT dispatch anything. This is the main thing you are implementing.
   - Scraping ALREADY WORKS via `scrape_transcript()` → `scripts/youtube-transcript.cjs`.
   - `PIPELINE_AGENTS` list already maps all 7 agents → their output files. Use it as-is:
     researcher→00_RESEARCH_NOTES.txt, scriptwriter→01_SCRIPT_RAW.txt,
     image-director→05_IMAGES/IMAGE_PROMPTS_BATCH_FINAL.txt, thumbnail-artist→
     04_THUMBNAIL/THUMBNAIL_PROMPT.txt, voice-engineer→02_SCRIPT_ELEVENLABS.txt,
     seo-specialist→07_METADATA.txt, archive-manager→COMPLETENESS_REPORT.txt.

2. **The 7 agent prompts** live in `artisan/pov_pipeline/agents/POV-*.md` (7 files).
   They use `%PROJECT_DIR%` placeholders. Do NOT rewrite their content. The
   researcher already supports SOURCE MODE (reads 00_SOURCE_SCRIPT.txt and applies a
   heavy twist).

3. **Transcript scraper:** `artisan/pov_pipeline/scripts/youtube-transcript.cjs`
   ALREADY EXISTS and works (`node youtube-transcript.cjs <url> en`, prints transcript
   to stdout). **DO NOT create a new Python scraper.** Reuse the existing one via
   `scrape_transcript()`.

4. **TTS:** `artisan/pov_pipeline/tts/gemini_tts.py`, run with its own venv
   (`tts/.venv/Scripts/python.exe`). It writes segments to
   `06_AUDIO/<VIDEO_ID>/<SEG>.wav` — **nested under the video ID**. The assembler must
   receive that nested dir, not `06_AUDIO/` itself.

5. **Flow CLI (images):** `artisan/flow-cli/images.ts`, exposed as
   `opencli flow images --file <batch> [--profiles a,b]`. Thumbnail via
   `opencli flow image-gen --prompt <p> --aspect 16:9 --out <f> --yes`.
   OpenCLI is a Chrome-browser bridge: images render inside a logged-in Chrome
   profile. Human one-time setup (see bottom) — not something code can automate.

6. **Assembler:** `artisan/pov_pipeline/scripts/pov_assembler_pro.py`. Reads the
   `=== SEGMENT MANIFEST ===` block from `01_SCRIPT_RAW.txt` (VIDEO_ID, row table).
   Args: `--script --audio <dir> --images <dir> --output <dir> --cpu-preset light`.
   Writes `output_pro/<VIDEO_ID>/FINAL_<VIDEO_ID>.mp4`.

7. **Word budget gate:** 1,620–2,025 narration words (12–15 min). Already in
   `script_gate()`.

8. **Existing partial branch:** `origin/fix/phase1-pipeline-automation` (commit
   07723f8) contains two useful pieces you may cherry-pick the *ideas* from:
   - `images.ts` strict `EXPECTED FILES:` validation + `execFileSync` (needs fixing —
     see GAP 4).
   - a `render` stage and `_video_id_from_script()` in the orchestrator.
   It is NOT merged to main. Do not copy its `state/` changes — those are Milo's own
   brain files and must never be touched by this pipeline.

---

## GAPS TO CLOSE (your actual work)

### GAP 1 — Implement `run_agents()`: real agent dispatch via OpenCode CLI

Replace the stub so it runs the 7 agents **sequentially**, each as its own
`opencode run` subprocess, and aborts if any agent fails.

1. `run_agents()` generates the 7 registered agents **per project at dispatch
   time** (do NOT commit them to the repo — see verified fact 5). For each agent in
   `PIPELINE_AGENTS`, write a file to `<project_dir>/.opencode/agent/pov-<agent>.md`
   with THIS exact frontmatter shape (mode: all is mandatory — see verified fact 3):
   ```
   ---
   name: pov-<agent>
   description: <from original>
   mode: all
   permission:
     edit: allow
     bash: allow
   ---
   ```
   followed by the original `POV-<agent>.md` body verbatim (keep `%PROJECT_DIR%`
   placeholders — your dispatch layer substitutes the real absolute path).
   Creating `<project_dir>/.opencode/agent/` also creates a `.gitignore` there;
   that's harmless. Do NOT register agents in `artisan/pov_pipeline/.opencode/`
   or in the global `~/.config/opencode/agent/`.

2. In `run_agents(project_dir)`:
   - For each `(agent, outfile)` in `PIPELINE_AGENTS`:
     - Skip if `outfile` already exists (resume-safe) — but ONLY skip after
       verifying it is non-empty.
     - Build the task message: the agent .md body with `%PROJECT_DIR%` replaced by
       the absolute project dir, plus an explicit line:
       `Write your complete output to <abs project dir>/<outfile>. Do not truncate.`
     - Dispatch via subprocess. **Windows shim rule (critical):** `opencode` on this
       machine is a `.cmd`/`.ps1` shim under `C:\Users\user\AppData\Roaming\npm\`,
       not a real exe. Resolve it with `shutil.which("opencode")` and use that
       resolved path as `cmd[0]` — do not invoke bare `"opencode"`. Same rule
       already applies to `opencli` in this codebase.
     - Command shape (fully verified — see the recipe in the top section):
       `[resolved_opencode, "run", "--agent", f"pov-{agent}", "--dir", str(project_dir), "--pure", message]`
       Use `encoding="utf-8", errors="replace"` and a per-agent timeout ≥ 600s.
       Do not pass `--model`; let it use the default.
     - **Gate:** after the subprocess returns, assert `outfile` exists AND is
       non-empty. On failure, print which agent failed, its tail stderr, and
       `sys.exit(1)`. Do NOT continue to the next agent. Returncode alone is NOT a
       valid gate (the subagent-mode fallback returns 0) — always check the file.
   - Print a clear progress line per agent (`[2/7] POV-scriptwriter -> 01_SCRIPT_RAW.txt`).

3. The dispatch message must tell each agent exactly which project files to READ as
   input (e.g. scriptwriter reads 00_RESEARCH_NOTES.txt; image-director reads
   01_SCRIPT_RAW.txt; archive reads everything). List them per agent in the message.

### GAP 2 — Add `--url` and `--stage full`

1. Add a `--url` argument as an alternative to the positional `input`.
2. Add `full` to the `--stage` choices. `--stage full` with a URL runs, in order:
   `scrape → run_agents → gate → tts → images → thumb → assemble`, printing the
   final mp4 path. Each stage must call the existing functions, not re-implement.
3. Make `run_assembler` pass `--audio <project>/06_AUDIO/<VIDEO_ID>/` (the nested
   dir TTS actually writes to), deriving VIDEO_ID from the script manifest
   (regex `VIDEO_ID:\s*(\S+)` in the `=== SEGMENT MANIFEST ===` block). Reuse the
   branch's `_video_id_from_script()` idea.
4. Keep every stage independently runnable (`--project X --stage images` etc.) and
   resume-safe (skip existing audio/images/agents).

### GAP 3 — Make agent-dispatch work when called from inside an OpenCode session AND from a bare PowerShell

The parent may itself be OpenCode. Nested `opencode run` calls still work (each is a
separate process/session) but are heavy. Verified: nested dispatch completes in
~50-100s per agent and returns cleanly without hanging on stdin (use `--pure` to
skip external plugins and avoid stray prompts). No retry logic is required beyond
one retry on timeout.

### GAP 4 — Resolve the `EXPECTED FILES:` mismatch (Opus introduced this)

Opus's `images.ts` now throws and refuses to run unless the batch file contains a
line `EXPECTED FILES: NAR-001, NAR-002, ...`. But **no agent currently emits that
line**, so image generation would fail closed on every real project. Fix BOTH sides:

1. `artisan/pov_pipeline/agents/POV-image-director.md`: add a mandate to end
   `IMAGE_PROMPTS_BATCH_FINAL.txt` with an `EXPECTED FILES:` line listing every
   `[SEG_ID]` it wrote (primaries AND sub-images like NAR-042-B), comma-separated.
2. `artisan/flow-cli/images.ts`: make the check **tolerant** — if the
   `EXPECTED FILES:` line is missing, derive the expected IDs from the parsed
   `[SEG_ID]` blocks and continue (warn, don't abort). Only hard-fail when a listed
   expected file has no generated `.jpeg` after the run.

### GAP 5 — Never touch `state/`

`state/manifest.json`, `state/memory.jsonl`, `state/profile.json`,
`state/routines.json` are Milo's (the agent's) own memory. This pipeline MUST NOT
read, write, or commit changes to `state/`. Opus's branch modified them — do not
bring those changes over. Only touch `artisan/pov_pipeline/**` and
`artisan/flow-cli/**`.

---

## WINDOWS GOTCHAS (already discovered, do not rediscover)

- `opencli` and `opencode` are `.cmd`/`.ps1` shims. Always resolve via
  `shutil.which(...)` and use the resolved path as `cmd[0]`.
- `subprocess.run(..., text=True)` on Windows defaults to cp1252 → mojibake on UTF-8
  output. Pass `encoding="utf-8", errors="replace"`.
- Use `execFileSync`/argv arrays, never string-shell concatenation with prompts that
  contain quotes.

## VERIFY BEFORE YOU FINISH

1. `python -m py_compile artisan\pov_pipeline\run_pov_pipeline.py` passes.
2. `node --check artisan\flow-cli\images.ts` — no wait, it's TS. Compile/typecheck it
   the same way this repo builds flow-cli (check for a build script / tsconfig; if
   none, at least ensure the TS is syntactically consistent with the rest of the
   plugin and `opencli plugin update flow` re-installs cleanly).
3. Dry-run agent dispatch on a THROWAWAY project: create a temp project dir, put a
   fake `00_SOURCE_SCRIPT.txt` in it, run `run_agents()` and confirm agent 1
   (researcher) actually writes `00_RESEARCH_NOTES.txt`. The dispatch recipe in the
   top section is already proven on this machine — a single-agent smoke test should
   pass within ~2 min. The known-good recipe is:
   `[resolved_opencode, "run", "--agent", "pov-<x>", "--dir", <project>, "--pure", message]`
   with the agent file written to `<project>/.opencode/agent/` and `mode: all`.
4. Confirm `--stage full` with a real URL at least reaches TTS and reports the exact
   point where human setup (Flow browser login) is needed.

## ONE-TIME HUMAN SETUP (document this, don't try to automate it)

OpenCLI Flow profiles are **Chrome browser profiles** with the OpenCLI Browser
Bridge extension:
1. `opencli profile list` / `opencli profile use <name>` to pick a profile.
2. Open that Chrome profile, sign into the Google account, install the OpenCLI
   Browser Bridge extension.
3. `--flow-profiles "flow-account-1,flow-account-2"` rotates profiles on rate limits.

## DELIVERABLES

- `run_pov_pipeline.py`: `--url`, `--stage full`, real `run_agents()` (generates
  the 7 per-project agents in `<project>/.opencode/agent/`, dispatches via the
  verified recipe, gates on non-empty output files), nested audio-dir fix.
- `POV-image-director.md`: `EXPECTED FILES:` mandate.
- `artisan/flow-cli/images.ts`: tolerant expected-file validation.
- Short verification log proving GAP checks above.
- Commit on a feature branch (e.g. `feat/pov-pipeline-full`), never straight to main.
