# VPS Runbook — Daily Posting Daemons + Fast Telegram Control

**Audience:** the OpenCode agent running *on the VPS* (13.49.223.119, Windows Server 2025,
repo at `C:\milo-portable-system`).
**Goal:** shorts + ranking pipelines sweep and post every day without anyone logged in, every
run reports to Telegram, and the Telegram bot answers instantly while keeping one conversation
per chat.

Read the whole file once before typing anything. Do the steps **in order**. Every step has a
**Gate** — if the gate fails, stop and fix that step, do not continue. Report each gate result
to Telegram as you go (step 9 has the template).

**Hard rules**

- Never print, echo or commit the contents of any `.env`, token or `*.json` credential.
- Never commit `state/`, `data/`, `venv/`, `*.db`, `*.log` or tokens. Portable code only on `main`.
- Claim your work in `WORK_CLAIMS.md` before you start and release it when you finish.
- If a command needs elevation, say so and run it from an elevated PowerShell. Do not silently skip it.
- If something contradicts this runbook, trust the machine, then tell Allan what differed.

---

## 0. What changed (context)

| Thing | Before | Now |
|---|---|---|
| Pipeline daemons | `schtasks ... /sc onlogon` running `src.main --mode schedule` via an **interactive** `.bat` that ends in `pause` | `MiloShortsPipeline` / `MiloRankingPipeline`, daily, S4U (no login needed), running `scripts/daemons/pipeline_runner.py` |
| Run reporting | none, or a prompt that may or may not fire | every run writes `<state>/pipeline_runs/<key>-last.json` and pushes a Telegram report |
| Bot session | new opencode session per message | one session per chat in `state/telegram_sessions.json`, continued with `--session` |
| Bot speed | every message spawned a cold opencode | fast path (NVIDIA chat completions) for plain text; `/do` attaches to a warm `opencode serve` |
| Bot ops | none | `/status /pipelines /run /kill /logs /uploads` answered locally, no model involved |
| Recovery | manual | `MiloDaemonWatchdog` every 10 min: restarts dead daemons, alerts on missed runs, low disk |

New files (all under version control):

```
scripts/daemons/pipeline_runner.py        non-interactive pipeline run + report
scripts/daemons/install_milo_daemons.ps1  registers/removes every task (XML, S4U)
scripts/daemons/watchdog.ps1              restarts + alerts
scripts/daemons/start_telegram_bot.cmd    bot launcher for Task Scheduler
scripts/daemons/start_opencode_server.cmd warm `opencode serve` launcher
scripts/daemons/run_pipeline.cmd          scheduler -> pipeline_runner shim
scripts/daemons/patch_harness_attach.py   idempotent miloctl/harness.py fix
scripts/verify_daemons.ps1                read-only health check (rewritten)
milo-bot/src/bot.py                       rewritten bot
```

---

## 1. Preflight — get the code without losing local work

The VPS very likely has **uncommitted local edits to `milo-bot/src/bot.py`** (the fast path that
was added on 2026-08-27 and never pushed). This step exists so you do not destroy it.

```powershell
cd C:\milo-portable-system
git status --porcelain
git stash list
```

- If `git status` shows modified tracked files, back them up **before** pulling:

```powershell
$stampDir = "C:\milo-backups\pre-daemon-$(Get-Date -Format yyyyMMdd-HHmm)"
New-Item -ItemType Directory -Force -Path $stampDir | Out-Null
git diff > "$stampDir\uncommitted.patch"
Copy-Item milo-bot\src\bot.py "$stampDir\bot.py.local" -ErrorAction SilentlyContinue
git stash push -m "pre-daemon-runbook backup"
```

Then fetch and check out the fix branch:

```powershell
git fetch origin
git checkout fix/vps-daily-daemons-and-fast-bot
git pull --ff-only origin fix/vps-daily-daemons-and-fast-bot
```

**Gate 1:** `git log --oneline -1` shows the daemon commit, and
`Test-Path scripts\daemons\pipeline_runner.py` is `True`.

> The new `bot.py` supersedes the local fast-path draft: it has the same NVIDIA fast path plus
> session persistence, warm attach, and the ops commands. Diff the backup against it and port
> across anything Allan added that is missing. Do not merge blindly.

---

## 2. Environment keys

The bot and the runner both load, in order: ambient env, `%LOCALAPPDATA%\milo\.env`,
repo `.env`, `milo-bot\.env`, and for pipelines also `<pipeline>\config\.env`. First value wins.
Put shared secrets in **one** place: `%LOCALAPPDATA%\milo\.env`.

Required (check presence, never print values):

```powershell
$envPath = "$env:LOCALAPPDATA\milo\.env"
$needed = @('TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID','ALLOWED_USER_IDS','NVIDIA_API_KEY')
$have = (Get-Content $envPath | Where-Object { $_ -match '=' } |
         ForEach-Object { ($_ -split '=',2)[0].Trim() })
$needed | ForEach-Object { "{0,-22} {1}" -f $_, $(if ($have -contains $_) { 'present' } else { 'MISSING' }) }
```

Add these if absent (they are new, and the bot is faster with them):

```
OPENCODE_SERVER_URL=http://127.0.0.1:4096
OPENCODE_WORKDIR=C:\milo-portable-system
OPENCODE_BIN=<full path to opencode.exe if it is not on PATH>
MILO_FAST_MODEL=nvidia/nvidia-nemotron-nano-9b-v2
MILO_HOME=C:\Users\Administrator\AppData\Local\milo
```

`ALLOWED_USER_IDS` must contain Allan's numeric Telegram user id (`8101147332` is the chat id;
if the DM chat id and user id are the same number, that value is fine). **An empty allowlist now
makes the bot exit with a loud error instead of silently denying every message.**

**Gate 2:** all four required keys report `present`, and `opencode --version` prints a version.

---

## 3. Bot dependencies

```powershell
cd C:\milo-portable-system\milo-bot
if (-not (Test-Path .\venv)) { python -m venv venv }
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -c "import telegram, httpx; print('ptb', telegram.__version__, 'httpx ok')"
```

**Gate 3:** that last line prints a version and `httpx ok`. Without `httpx` the fast path silently
disables itself and every message goes the slow route — that is the single most common cause of
"the bot is slow again".

---

## 4. Apply the harness fix

```powershell
cd C:\milo-portable-system
python scripts\daemons\patch_harness_attach.py
python -c "import miloctl.harness as h; print(h.OpenCodeHarness().invoke('hi'))"
```

Expected: the printed argv contains `--auto`, and `--attach` appears once `OPENCODE_SERVER_URL`
is set in the environment of that shell. If the patcher says the code diverged, apply the two
edits by hand exactly as it describes, then re-run the verify line.

**Gate 4:** `--session` / `--format` are never appended **after** the prompt anywhere in
`miloctl/harness.py` (`git diff` shows the flags built before `argv.append(prompt)`).

---

## 5. Smoke test everything in the foreground before scheduling it

Scheduled tasks hide errors. Prove each piece by hand first.

**5a. Pipeline runner, no side effects:**

```powershell
cd C:\milo-portable-system
python scripts\daemons\pipeline_runner.py shorts --dry-run --no-notify
python scripts\daemons\pipeline_runner.py ranking --dry-run --no-notify
```

Expect the resolved interpreter to be the **pipeline's own venv**
(`artisan\...\venv\Scripts\python.exe`) and the command line to end in
`-m src.main --mode once --videos 1` / `--mode auto --videos 3 --variant mixed`.

**5b. One real, small run of each (this uploads):**

```powershell
python scripts\daemons\pipeline_runner.py shorts  --videos 1
python scripts\daemons\pipeline_runner.py ranking --videos 1
```

Expect a Telegram report per run and a summary file:

```powershell
Get-Content "$env:LOCALAPPDATA\milo\pipeline_runs\shorts-last.json"
```

If a run fails on YouTube auth (`invalid_grant`), fix it before scheduling:
`.\reauth_all_channels.bat --doctor` then `.\reauth_all_channels.bat --channel <key>`.
A scheduled daemon on top of broken tokens just fails quietly every morning.

**5c. Warm opencode server:**

```powershell
Start-Process -FilePath "C:\milo-portable-system\scripts\daemons\start_opencode_server.cmd"
Start-Sleep 20
Test-NetConnection 127.0.0.1 -Port 4096 | Select-Object TcpTestSucceeded
opencode run --attach http://127.0.0.1:4096 --agent milo --auto "reply with the single word ready"
```

**5d. Bot in the foreground:**

```powershell
cd C:\milo-portable-system\milo-bot
.\venv\Scripts\python.exe src\bot.py
```

Watch stdout. Expect `Milo Telegram bot starting`, then `allowlist: <id>`. Now from your phone:

| Send | Expect |
|---|---|
| `hi` | a reply in **1–3 seconds** (fast path) |
| `/ping` | `fast path: on`, `attach: http://127.0.0.1:4096`, a session id or `(none yet)` |
| `/do what branch is the repo on and what changed in the last commit` | real answer with tool use |
| a second `/do remind me what I just asked` | it remembers — this is the session fix |
| `/status` | task table |
| `/pipelines` | the runs from 5b |

Then `Ctrl+C`.

**Gate 5:** every row above behaves as described. In particular the second `/do` must show
continuity. If it does not, check `state\telegram_sessions.json` has an entry for your chat id and
that `bot.log` logged `opencode turn (session=<id> ...)` rather than `session=new` twice.

---

## 6. Register the daemons

Elevated PowerShell:

```powershell
cd C:\milo-portable-system\scripts\daemons
powershell -ExecutionPolicy Bypass -File .\install_milo_daemons.ps1 `
    -ShortsTime 08:45 -RankingTime 09:15
```

What it creates (all `LogonType=S4U`, `RunLevel=HighestAvailable`, so they run with **nobody
logged in** — this is the reason the old `onlogon` tasks stopped when RDP closed):

| Task | Trigger | Time limit |
|---|---|---|
| `MiloOpencodeServer` | at boot +1m, restart 999x/1m | unlimited |
| `MiloTelegramBot` | at boot +1m and at logon, restart 999x/1m | unlimited |
| `MiloShortsPipeline` | daily 08:45 | 6h |
| `MiloRankingPipeline` | daily 09:15 | 6h |
| `MiloDaemonWatchdog` | every 10 min | 10m |

Also make sure the 5-minute routines heartbeat exists (separate system, do not duplicate it):

```powershell
milo routines install
milo routines status
```

**Gate 6:** `powershell -File .\install_milo_daemons.ps1 -Status` shows `MiloTelegramBot` and
`MiloOpencodeServer` as **Running**, the two pipelines as **Ready** with a `next` time tomorrow
morning, and the watchdog **Ready**.

---

## 7. Verify like you did not write it

```powershell
cd C:\milo-portable-system
powershell -ExecutionPolicy Bypass -File .\scripts\verify_daemons.ps1
```

Then the honest test — kill the bot and watch it come back:

```powershell
schtasks /End /TN MiloTelegramBot
schtasks /Run  /TN MiloDaemonWatchdog
Start-Sleep 20
(Get-ScheduledTask MiloTelegramBot).State     # expect Running
```

And prove the schedule fires without a session: from the console, sign out of RDP entirely, wait
for the next daily window, and confirm a Telegram report lands. Until you have seen a report
arrive with no one logged in, the deployment is **not** done.

**Gate 7:** health check prints `HEALTHY`, the bot self-restarted, and you can name the exact
timestamp of a pipeline report that arrived while logged out.

---

## 8. Daily rhythm, once it is live

- 08:45 shorts sweep -> report to Telegram
- 09:15 ranking sweep -> report to Telegram
- 09:40 `pipeline-driver` routine (existing) -> verifier summary
- every 10 min watchdog -> restarts, missed-run alerts, disk alerts
- any time, from the phone: `/run shorts`, `/run ranking 3`, `/kill ranking`, `/logs shorts 40`

To change the times: re-run `install_milo_daemons.ps1 -ShortsTime HH:MM -RankingTime HH:MM`.
To change how much each run makes: `--videos` in `run_pipeline.cmd`, or the `env` block for that
pipeline in `pipeline_runner.py` (it mirrors the values the interactive `.bat` panel sets).

---

## 9. Report back to Allan

Send this to Telegram, filled in, no edits to the shape:

```
DEPLOY REPORT — daemons + bot
gates: 1 ok  2 ok  3 ok  4 ok  5 ok  6 ok  7 ok      <- mark any failure and stop there
tasks: bot=Running server=Running shorts=Ready(next 08:45) ranking=Ready(next 09:15) watchdog=Ready
smoke: shorts run <status> <n> uploads | ranking run <status> <n> uploads
bot:   plain text <n>s | /do session continuity ok | attach warm
left:  <anything you could not do, with the exact blocker>
```

---

## 10. Failure modes and the exact fix

| Symptom | Cause | Fix |
|---|---|---|
| Bot exits instantly, `bot.stdout.log` says ALLOWED_USER_IDS | allowlist empty | add it to `%LOCALAPPDATA%\milo\.env`, `schtasks /End` then `/Run MiloTelegramBot` |
| `Conflict: terminated by other getUpdates` | two pollers on one token | stop the stray python process, then run only the task. Never run `src\bot.py` by hand while the task is Running |
| Replies take 20s+ | fast path off | `/ping`: if `fast path: OFF` add `NVIDIA_API_KEY`; if on, `pip install httpx` in the bot venv |
| `/do` slow but works | no warm server | check `attach:` in `/ping`, then `MiloOpencodeServer` state and port 4096 |
| Every `/do` forgets the last one | session not persisting | check `state\telegram_sessions.json` is writable and that `patch_harness_attach.py` ran |
| Task `Ready` but nothing posted | `--mode schedule` style long-runner, or the interactive `.bat` | the task must call `run_pipeline.cmd`, never `run_pipeline.bat` |
| Task result `0x41301` | already running (normal for the two daemons) | nothing to do |
| Task result `0x1` on a pipeline | pipeline exited non-zero | `/logs shorts 60`, then the dated log in `<pipeline>\data\logs\` |
| Pipeline runs but 0 uploads | caps hit, suppression, or tokens | `reset_caps.py`, `channel_health.py`, `reauth_all_channels.bat --doctor` |
| `invalid_grant` in a report | OAuth token expired | `reauth_all_channels.bat --channel <key>` |
| Two runs of one pipeline overlap | lock removed by hand | the lock is `<pipeline>\data\<key>.lock`; only delete it if the pid in it is gone |
| Disk alerts | renders piling up | `cleanup_runtime.py`, `cleanup_uploaded.py` |
| Nothing arrives on Telegram at all | wrong token or chat id | `milo send "test" --to telegram` and read the error it prints |

---

## 11. Rollback

```powershell
cd C:\milo-portable-system\scripts\daemons
powershell -ExecutionPolicy Bypass -File .\install_milo_daemons.ps1 -Uninstall
cd C:\milo-portable-system
git checkout main
git stash pop            # if you stashed in step 1
```

Then re-register whatever was there before. Nothing in this change touches pipeline internals,
tokens or state, so a rollback is only about tasks and two files.
