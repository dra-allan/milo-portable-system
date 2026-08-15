# YT-SECRETS AUTH OVERHAUL — Handoff for Opus

## The goal

Every YouTube channel Allan runs gets a working OAuth token (`youtube_token_<channel>.json`)
so the pipelines (shorts, ranking, pov/clipper) can auto-post. Tokens must be **mintable and
refreshable on ANY machine** (main PC, AWS brain, future laptops) with the least possible
human friction.

Context in the repo:

- `artisan/yt-secrets/` — the current (broken-in-practice) layer I built.
  - `channels.yaml` — maps each channel → owning gmail → slug → pipeline token_dir.
  - `mint_token.py` — the mint tool (local HTTP callback server + OAuth).
  - `<slug>/credentials.json` — OAuth client JSONs (gitignored), one per Google Cloud project.
- Two funded Google Cloud projects (each carries its own YouTube Data API 10k-units/day quota):
  - **PROJECT A `yt-flick-shorts`** (owner `draallan0@gmail.com`, client `929304292327-...`).
  - **PROJECT B `milo-mcp`** (owner `adrasaltsxxx@gmail.com`, client `222141244525-...`).
- Channel→project split (already committed in `channels.yaml`):
  - A: flick_shorts, capital_mindset.
  - B: wealth_mindset, dra_allan_official, NXS, explaination.
  - Inactive (no funded project yet): chop_ug, rankdrop, money_matrix, god_did_fx,
    the_other_guys, moviegasm.
- What already WORKS and must stay working:
  - flick_shorts token minted via `mint_token.py` (interactive browser flow) → verified
    refresh + channel resolves to "Flick Shorts". Token at
    `artisan/youtube-shorts-pipeline/config/youtube_token_flick_shorts.json`.
  - The clipper wiring (78 tests pass): `publisher._client_secrets()` finds credentials in
    shared dir / parent fallback; `channels.yaml` keys aligned; `castle_clipping.yaml`
    upload_channel=capital_mindset.

## What I tried and what failed

The script itself works (flick_shorts proved the mechanics: build auth URL → user pastes in
Chrome → approve → local callback server gets the code → exchange → validate by refresh →
write token). The failures were ALL operational, on Windows, in a non-interactive agent
shell:

1. **Default 300s wait timeout.** `mint_token.py` waited 5 minutes for the human to paste
   the URL and click Approve. With "unverified app" warnings and back-and-forth, the human
   consistently took longer → silent `timed out waiting for OAuth callback`. I bumped it to
   `range(3600)` (1 hour) — that part is fixed in the file now.
2. **Background process gets killed.** Every launch method I tried from the agent shell died
   before/while the human approved:
   - `Start-Process` (hidden, redirected) → killed when the launch command's shell call
     ended or timed out (err file showed `^C`).
   - WMI `Win32_Process.Create` → same `^C` kill.
   - schtasks as Administrator → `0xC0000142` DLL-init failure.
   - schtasks as SYSTEM with a `.bat` wrapper → stays Running (this was the last working
     state) but the callback still did not land on the port.
   The ONLY attempt that survived end-to-end was flick_shorts on the very first try, because
   that particular launch command finished before the shell reclaimed the tree.
3. **The localhost callback is unreliable here.** Even with a live listening server, the
   consent redirect did not reach the mint process's port in repeated tries (last state:
   schtasks Running, port listening, user clicked Allow, callback never arrived). Possible
   causes: browser redirect to `http://localhost:<random-port>/` being intercepted/blocked,
   the `&`-heavy auth URL being mangled when passed through the shell, or the callback
   landing on the wrong process. Root cause never confirmed.
4. **Auth URL length/mangling.** The consent URL is ~425 chars with many `&`. Pasting it
   directly into Chrome works; passing it through shell tooling corrupted it. I built a
   `serve_redirect.py` (localhost HTTP 302 → real URL) as a band-aid; that introduced more
   moving parts.

## What the human (Allan) is demanding

Stop making him babysit this. He wants:

1. **One command, per machine, that authenticates every pipeline.** Something like
   `python -m yt_secrets auth` (or a `miloctl` subcommand) that, when run on a new machine,
   walks the human through each channel's OAuth **one at a time, safely**, and ends with all
   tokens present + verified.
2. **No fragile background process.** The long-running callback server must not be spawned by
   an agent shell that can kill it. Options to design for:
   - A single self-contained script the HUMAN runs in their own terminal window
     (agent just hands them the command; the script drives its own interactive flow,
     `webbrowser.open`, and `run_local_server`-style callback that lives inside the human's
     session — never an agent-spawned daemon).
   - Or a `--url` mode: script prints ONE URL, human approves in browser, Google redirects
     to `http://localhost` (fixed port), script validates + writes. Everything in one
     foreground process the human controls.
3. **Per-project test users + publish state** handled once per project, not per machine.
   Write it into the docs/README so setup on a new machine doesn't re-derive it:
   - Project A consent screen: test user `draallan0@gmail.com`; PUBLISH the app (refresh
     tokens expire after 7 days in Testing mode).
   - Project B consent screen: test users `adrasaltsxxx@gmail.com` AND `draallan12@gmail.com`
     (NXS + explaination are owned by draallan12 but ride project B); PUBLISH it too.
   - Note: NXS/explaination consent must be approved while signed in as draallan12.
4. **Detect and report token health.** After mint, actually refresh each token and print
   channel_name + expiry so the human sees success/failure without digging in files.
5. **Machine replication story.** credentials.json files are gitignored (good — they are
   secrets). The repo must ship a `--setup`/`docs` path that says exactly which files to copy
   from an already-authenticated machine vs. re-download from Google Cloud console.

## Acceptance criteria

- A human can authenticate ALL active channels (flick_shorts, capital_mindset,
  wealth_mindset, NXS, explaination) on a fresh machine in one sitting, in under ~10 minutes
  of human attention, without touching the agent shell.
- Each minted token is verified by refresh before being reported OK.
- The 5-minute-timeout + background-kill + mangled-URL failure modes from this session are
  structurally impossible in the new design (no agent-spawned daemons; long waits allowed;
  URL handled by the human's own browser).
- `channels.yaml` + this doc stay in sync with any design change.
- Once merged, make a PR (branch off `main`).

## Known live state to preserve

- `artisan/yt-secrets/channels.yaml` — current two-project map (committed `2cd73e0`).
- `artisan/yt-secrets/mint_token.py` — has the 3600s fix + auto-free-port; otherwise the old
  (unreliable-launch) design.
- `artisan/youtube-shorts-pipeline/config/youtube_token_flick_shorts.json` — VALID token,
  do not clobber.
- `artisan/youtube-shorts-pipeline/config/youtube_token_capital_mindset.json` — REVOKED,
  stale (11:15 AM). Needs re-mint.
- The schtasks task `mint_cm` and temp files (`mint_cm.bat`, `serve_redirect.py`) may still
  exist in the temp dir / Task Scheduler — safe to clean up.
- Original PC still needs the same auth flow run once the new design lands (tokens can't be
  copied across machines reliably; re-mint per machine is the plan).

## Ask of Opus

Implement the above so that, from the next session, authenticating the pipelines on a new
machine is a one-command, human-in-the-loop flow with no agent-spawned background processes.
Keep it vendored inside `artisan/yt-secrets/` (milo self-contained rule). Update
`channels.yaml`/docs accordingly. Commit on a branch and open a PR to `main`.