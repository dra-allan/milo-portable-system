# VPS Runbook — Daily Pipeline Daemons + Instant Telegram Control Plane

**Audience:** the agent running on the VPS (opencode, agent `milo`).
**Box:** AWS t3.small, Windows Server 2025, `C:\milo-portable-system`.
**Goal:** shorts + ranking sweep and post **every day**, unattended, and Allan
monitors and commands the whole thing from Telegram without touching RDP.

Follow this top to bottom. **Do not skip verification steps.** Every step has an
acceptance test; if the test fails, fix that step before moving on. Report the
result of each phase to Telegram as you go.

---

## 0. What was actually broken

Read this first, because it tells you what *not* to re-create.

| Symptom | Root cause | Fix (in this repo now) |
|---|---|---|
| Bot opened a new session per message | `milo-bot/src/bot.py` ran `opencode run <prompt>` with **no `--session`**. Every message = fresh conversation + cold MCP boot. | Bot now talks HTTP to a warm `opencode serve` and reuses **one session per Telegram chat**, persisted in `state/telegram_sessions.json`. |
| Replies took ~3 minutes | Process spawn + MCP cold boot + 286-line agent context, per message. | Three paths: ops commands (no LLM, sub-second), fast chat (~1-2s), agent (warm server, keeps context). |
| "The task ran but nothing posted" | **Two schedulers raced the same pipeline.** `Ranking Shorts Pipeline Daemon` (AtStartup, `--mode schedule`) *and* `MiloRankingPipeline` (daily) were both registered. Same workspace, same daily upload cap. | One canonical task per pipeline, legacy names unregistered by the installer, plus a PID lock in `Run-Pipeline.ps1`. |
| Health check always looked fine | `scripts/verify_daemons.ps1` checked task names that no longer existed. | `scripts/vps/Health-Check.ps1` uses the canonical names and exits non-zero on trouble. |
| Silence meant nothing | Reports only existed if an agent felt like generating one. | Every sweep reports to Telegram, pass **or** fail. A watchdog alerts when a sweep goes stale. Silence now means "the task never fired", which the watchdog also catches. |
| Tokens "expired" for no reason | Tasks registered as `SYSTEM`. SYSTEM has a different profile, so OAuth tokens and `opencode auth` were looked up in the wrong place. | Daemons run as the **Administrator** account (S4U), never SYSTEM. |

---

## 1. Preconditions (5 min)

```powershell
cd C:\milo-portable-system
git fetch --all
git status                      # must be clean; stash local junk, do not commit state
```

Check the claim file first — never work over another machine's claim:

```powershell
Get-Content .\WORK_CLAIMS.md
```

Then confirm the toolchain, **as the account the daemons will run as** (the
interactive `Administrator`, not SYSTEM):

```powershell
whoami
python --version                # 3.11+ expected
(Get-Command opencode).Source   # must resolve
ffmpeg -version | Select-Object -First 1
opencode auth list              # NVIDIA (or whatever provider) must be listed
```

**Acceptance:** all five commands succeed. If `opencode` or `ffmpeg` is missing
from PATH, fix PATH **machine-wide** (`setx /M PATH ...`) and open a new shell —
scheduled tasks do not inherit your session's PATH edits.

---

## 2. Pull this branch and install deps

```powershell
cd C:\milo-portable-system
git checkout fix/vps-daemons-and-instant-bot   # or main once merged
git pull

cd milo-bot
if (-not (Test-Path venv)) { python -m venv venv }
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Acceptance:** `.\venv\Scripts\python.exe -c "import httpx, telegram; print('ok')"`
prints `ok`.

---

## 3. Configure `milo-bot\.env`

Copy `.env.example` to `.env` and fill it. Five keys are load-bearing:

| Key | Why it matters |
|---|---|
| `TELEGRAM_BOT_TOKEN` | obvious |
| `TELEGRAM_CHAT_ID` | where unsolicited reports land (`8101147332`) |
| `ALLOWED_USER_IDS` | **empty = bot refuses to start.** Fail closed, on purpose. |
| `NVIDIA_API_KEY` | the fast path. Missing key means every plain message falls through to the slow agent, which is the old behaviour. |
| `OPENCODE_SERVER_URL` | `http://127.0.0.1:4096` |

Also set `MILO_HOME=C:\Users\Administrator\AppData\Local\milo` and
`OPENCODE_WORKDIR=C:\milo-portable-system`.

**Never** print the file, echo a token into a log, or commit `.env`.

**Acceptance:**
```powershell
.\venv\Scripts\python.exe .\src\bot.py --once
```
Prints `[ok] bot online as @Milo_drabot`. It is fine for the opencode line to
say `!!` at this point — the server is not installed yet.

---

## 4. Install the daemons (one command)

```powershell
cd C:\milo-portable-system\scripts\vps
powershell -ExecutionPolicy Bypass -File .\Install-MiloDaemons.ps1 -Verbose
```

This is **idempotent** — safe to re-run. It unregisters the legacy duplicates
and registers exactly these:

| Task | Trigger | Runs |
|---|---|---|
| `Milo-OpencodeServer` | AtStartup + AtLogOn, restart 999x | `opencode serve --port 4096 --hostname 127.0.0.1` |
| `Milo-TelegramBot` | AtStartup + AtLogOn, restart 999x | `milo-bot\src\bot.py` |
| `Milo-ShortsPipeline` | daily 08:45 | `Run-Pipeline.ps1 -Pipeline shorts` |
| `Milo-RankingPipeline` | daily 09:15 | `Run-Pipeline.ps1 -Pipeline ranking` |
| `Milo-PipelineDriver` | daily 09:40 | `Send-Report.ps1` (daily digest) |
| `Milo-Routines` | every 5 min | `python -m miloctl.cli --quiet routines tick` |
| `Milo-Watchdog` | every 10 min | `Watchdog.ps1` |

Times are staggered on purpose: a t3.small cannot render two pipelines at once.
Shorts gets a 30-minute head start, and the digest fires after both.

If S4U tasks fail to start on this box (Task Scheduler history shows
`2147943785` / "logon failure"), re-run with `-UseStoredPassword` and enter the
Administrator password.

**Acceptance:**
```powershell
powershell -File .\Health-Check.ps1
```
`Milo-OpencodeServer` and `Milo-TelegramBot` show `Running`, the opencode server
reports a version, the bot holds the lock on `127.0.0.1:47431`. Exit code 0.

---

## 5. Prove the pipelines actually sweep and post

Do **not** wait for tomorrow's trigger. Run each one manually, once, right now.

```powershell
cd C:\milo-portable-system\scripts\vps
.\Run-Pipeline.ps1 -Pipeline shorts
.\Run-Pipeline.ps1 -Pipeline ranking
```

While it runs, watch from Telegram: `/logs shorts 60`.

Each run must produce, in `%LOCALAPPDATA%\milo`:

* `logs\pipelines\shorts-<date>.log` and `shorts-latest.log`
* `pipeline-status\shorts.json` with `exit_code: 0`
* a Telegram message: `[OK] YouTube Shorts sweep - ... uploads detected: N`

**If `exit_code` is not 0**, do not "fix" it by rescheduling. Diagnose in this
order, because these are the failures this box actually has:

1. **`invalid_grant`** in the log means a YouTube token died.
   `.\reauth_all_channels.bat --doctor` (offline audit) then reauth the named
   channel only: `.\reauth_all_channels.bat --channel <key>`.
2. **`quotaExceeded` / `dailyLimitExceeded`** is an API quota wall, not a bug.
   Note which channel and move on; the cap resets.
3. **`ffmpeg` not found** means PATH is wrong for the task principal (step 1).
4. **`no authenticated channels found`** means a token dir mismatch. Tokens live
   per lane: shorts/clipper in `youtube-shorts-pipeline/config/`, ranking in
   `ranking-shorts-pipeline/config/`. `channels.yaml` in `artisan/yt-secrets/`
   is the single source of truth; reconcile with
   `.\reauth_all_channels.bat --sync`.
5. **Channel guard rejection** means the token resolves to a different
   `channel_id` than `channels.yaml` declares. Fix the declaration, never the
   guard.

**Acceptance:** both status JSONs show `exit_code: 0` and two Telegram reports
landed. Uploads may legitimately be 0 if the daily caps were already drained —
in that case the log must say so (`caps reached` / `upload_failures=0`).

---

## 6. Prove the daily schedule fires

```powershell
Get-ScheduledTask Milo-ShortsPipeline, Milo-RankingPipeline, Milo-PipelineDriver |
  Get-ScheduledTaskInfo | Select-Object TaskName, LastRunTime, LastTaskResult, NextRunTime
```

`NextRunTime` must be tomorrow at the configured time. `LastTaskResult` of
`267011` means "never ran" — expected before the first trigger, **not** after.

Then force the digest once so you know the daily report path works end to end:

```powershell
.\Send-Report.ps1
```

**Acceptance:** a `MILO DAILY REPORT` message arrives with a line per pipeline
and a daemon roll-call. If the opencode server is up it also carries a short
`--- milo ---` commentary. The numbers are computed from the status files, so
they arrive even when the agent is down. That is deliberate: never make the
daily report depend on the AI being healthy.

---

## 7. Prove the bot is fast and remembers

From Telegram, in this order:

| Send | Expect |
|---|---|
| `/ping` | opencode server `ok` with a latency in ms, fast path `ok` |
| `/status` | daemon table + disk + last sweeps, **under a second** |
| `hey milo, you up?` | reply in 1-2s (fast path, no tools) |
| `/logs ranking 30` | last 30 lines of the ranking sweep |
| `/ask what did the shorts sweep upload today?` | agent reply using tools |
| `remember that in the next message` then `/ask what did I just ask you?` | it knows — **this is the session-continuity test** |
| `/run shorts` | `Milo-ShortsPipeline: started` |
| `/new` | thread reset confirmed |

**Acceptance:** the continuity test passes and `/status` is instant. If the
agent forgets between messages, check `%LOCALAPPDATA%\milo\telegram_sessions.json`
— it must contain a `chat_id -> session_id` entry, and
`GET http://127.0.0.1:4096/session/<id>` must return 200.

---

## 8. Reboot test (the one everybody skips)

```powershell
Restart-Computer -Force
```

Wait 3 minutes, then from Telegram send `/ping` and `/status`.

**Acceptance:** both answer without anyone logging in over RDP. If they do not,
`Milo-OpencodeServer` / `Milo-TelegramBot` did not start at boot: check
`Get-ScheduledTaskInfo` `LastTaskResult` and the Task Scheduler history for that
task, and re-register with `-UseStoredPassword`.

---

## 9. Report back

Post to Telegram a single message with:

1. Health-Check output (final block only).
2. Per pipeline: exit code, duration, uploads detected, and the channel keys
   that actually posted.
3. Anything you could **not** fix, with the exact error line and what you tried.

Then update `WORK_CLAIMS.md` (release your claim) and push state to the VPS
backup branch:

```powershell
python -m miloctl.cli backup
powershell -File .\scripts\git-sync.ps1
```

Portable code only on `main`; state goes to `backup/brain`. Never commit `.env`,
tokens, `state/`, `venv/`, or anything under `artisan/*/data/`.

---

## Command cheat sheet (for Allan, from the phone)

```
/status                    daemons, disk, last sweeps      instant
/logs shorts 60            tail any pipeline log           instant
/run shorts|ranking        kick a sweep now                instant
/report                    force the daily digest          instant
/ping                      is everything alive             instant
hey milo ...               casual chat                     1-2s
/ask <task>                full agent, tools, remembers    as long as it takes
!<task>                    same as /ask, fewer keystrokes
/new                       reset this chat's thread
```

## Failure modes, one line each

| Symptom | Do this |
|---|---|
| Bot silent | `Get-ScheduledTask Milo-TelegramBot`; `Get-Content milo-bot\bot.log -Tail 40` |
| `409 Conflict` in bot.log | two pollers. The lock on `:47431` should prevent it; kill the stray python and restart the task |
| `/ask` says server not answering | `Start-ScheduledTask Milo-OpencodeServer`, then `/ping` |
| Replies slow again | fast path key missing or wrong: `/ping` shows `NOT CONFIGURED` |
| Agent forgets context | `telegram_sessions.json` unwritable, or the server was reinstalled and dropped sessions |
| No daily report | check `Milo-PipelineDriver` `LastTaskResult`; run `Send-Report.ps1` by hand |
| Reports arrive, nothing uploads | caps or auth. `/logs shorts 80` and look for `invalid_grant` / `quotaExceeded` |
| Everything green, no videos | suppression: median views near 0 triggers channel health throttling. Check the `suppression.py` cache |
