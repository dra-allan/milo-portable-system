# POV Pipeline - VPS Deployment

Target: a small Debian/Ubuntu VPS running the pipeline unattended.

> **Read the Chrome Browser Bridge section before you promise anyone this
> works headless.** Everything else in this pipeline is a well-behaved Linux
> process. Google Flow is not.

---

## 0. What actually has to run there

| Component | Needed for | Headless-safe? |
| --- | --- | --- |
| `python3` (3.10+) | orchestrator, agents, discovery, upload | yes |
| `node` | transcript scraper (`youtube-transcript.cjs`), opencli | yes |
| `ffmpeg` | the assembler | yes |
| `opencode` CLI | the 7-agent chain | yes |
| `opencli` + Chrome | Google Flow images + thumbnail | **UNKNOWN - see below** |
| bundled `milo` / `miloctl` | pipeline memory | yes |
| YouTube Data API | discovery + upload | yes |

Sizing: 2 vCPU / 4 GB is enough for everything except comfortable ffmpeg
rendering. The assembler runs with `--cpu-preset light`; give it 4 vCPU if
you want a 15-minute video in under ~20 minutes. Disk: budget ~2 GB per
project before cleanup.

---

## 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  git curl ca-certificates ffmpeg

# Node 20 (for the scraper and opencli)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Google Chrome (only needed for the Flow image stages)
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
  | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
http://dl.google.com/linux/chrome/deb/ stable main" \
  | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt-get update && sudo apt-get install -y google-chrome-stable

# Virtual display, because Chrome wants one
sudo apt-get install -y xvfb
```

Global npm CLIs:

```bash
sudo npm i -g opencli
# opencode: follow https://opencode.ai/docs (npm or the install script)
opencode --version
opencli --version
```

---

## 2. Repo, venv, Milo

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/dra-allan/milo-portable-system.git
sudo chown -R "$USER":"$USER" milo-portable-system
cd milo-portable-system

python3 -m venv .venv
. .venv/bin/activate
pip install -U pip

# The POV pipeline core is standard-library only. These are optional:
#   PyYAML                  nicer config parsing (a vendored reader covers it)
#   google-api-python-client + google-auth-oauthlib
#                           the preferred upload path and the one-time OAuth
pip install PyYAML google-api-python-client google-auth-oauthlib

# Milo itself (memory, backup, the CLI the agents write to)
pip install -e .
milo install --no-prompt
milo doctor
```

`milo doctor` should come back healthy apart from warnings about things you
have not configured yet.

---

## 3. Environment

One file, `/opt/milo-portable-system/.env`, sourced by cron and systemd.
**It is gitignored. Never commit it.**

```bash
cat > /opt/milo-portable-system/.env <<'EOF'
# --- paths -----------------------------------------------------------
POV_PROJECTS_DIR=/srv/pov/projects
POV_DATA_DIR=/opt/milo-portable-system/artisan/pov_pipeline/data
POV_STATE_DIR=/opt/milo-portable-system/artisan/pov_pipeline/state

# --- agent chain ------------------------------------------------------
POV_OPENCODE_MODEL=anthropic/claude-sonnet-4-5
POV_MEMORY_PROJECT=pov-pipeline
# POV_OPENCODE_BIN=/usr/local/bin/opencode
# POV_AGENT_TIMEOUT=2400
# POV_GATE_MAX_RETRIES=3

# --- APIs -------------------------------------------------------------
YOUTUBE_API_KEY=
GEMINI_API_KEY=
GEMINI_API_KEY_2=

# --- notifications ----------------------------------------------------
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# --- Chrome / Flow ----------------------------------------------------
DISPLAY=:99
EOF
chmod 600 /opt/milo-portable-system/.env
mkdir -p /srv/pov/projects
```

Every config template in the repo uses `{{PLACEHOLDER}}`, and a placeholder
whose environment variable is unset is treated as **absent** rather than
sent to an API as a literal string. That is why you can leave
`config/notify.env.template` alone and just set the env vars here.

Config files:

| File | Tracked? | Purpose |
| --- | --- | --- |
| `artisan/pov_pipeline/config/pov_channels.yaml` | yes | curated sources, filters, cadence, privacy |
| `artisan/pov_pipeline/config/notify.env.template` | yes | Telegram placeholders |
| `artisan/pov_pipeline/config/notify.env` | **no** | your real values (optional) |
| `artisan/pov_pipeline/config/youtube_token_*.json` | **no** | OAuth tokens |
| `artisan/pov_pipeline/tts/.env` | **no** | Gemini keys |

---

## 4. YouTube auth (one time, on the dev machine)

The OAuth handshake needs a browser, so do it where you have one and copy
the result.

On the dev machine:

```bash
cd artisan/pov_pipeline
python -m uploader auth --channel explaination
# -> config/youtube_token_explaination.json
```

That needs `google-auth-oauthlib` and an OAuth client-secrets file. The
uploader looks for, in order: `POV_OAUTH_CLIENT_SECRETS`,
`artisan/pov_pipeline/config/credentials.json`, then the shorts pipeline's
`credentials.json`. Reuse the existing OAuth client rather than registering
a second one.

Copy the token over:

```bash
scp config/youtube_token_explaination.json \
    vps:/opt/milo-portable-system/artisan/pov_pipeline/config/
ssh vps chmod 600 /opt/milo-portable-system/artisan/pov_pipeline/config/youtube_token_explaination.json
```

With that file present the VPS uploads using the standard library alone: the
refresh token is exchanged over `urllib` and the video goes up through the
resumable upload protocol. No Google Python libraries required on the server.

Check it without touching YouTube:

```bash
python run_pov_pipeline.py --project <NAME> --stage upload --dry-run-upload
```

Discovery needs a plain **API key** (not OAuth): create one in the same
Google Cloud project with the YouTube Data API v3 enabled, and put it in
`YOUTUBE_API_KEY`.

---

## 5. Telegram

1. `@BotFather` -> `/newbot` -> copy the token.
2. Send your bot a message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and read
   `result[0].message.chat.id`.
3. Put both in the VPS `.env` (or copy `notify.env.template` to `notify.env`
   and fill it in).
4. Test:

```bash
cd artisan/pov_pipeline && python notify.py --test
```

With nothing configured the pipeline still runs; every event just goes to
`state/pipeline.log` instead. Notifications can never fail a stage.

---

## 6. THE HARD RISK: Chrome Browser Bridge / Google Flow

**Status: UNKNOWN on a headless VPS. Test this before declaring the VPS
ready.**

What is known, from the dev machine:

* Flow generates images only while its Chrome Browser Bridge profiles are
  **open**. A closed profile produces `BROWSER_CONNECT`.
* The Flow site itself does not need to be open; the login cookie persists.
* Login is a **one-time human step**. It is never automated, and neither is
  reCAPTCHA. Do not try.

What is unknown on a server:

* whether Chrome under Xvfb keeps a usable bridge session,
* whether Google's risk scoring starts throwing reCAPTCHA at a datacentre IP,
* whether the persisted login survives at all.

Procedure:

```bash
export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 &
cd artisan/pov_pipeline
./scripts/flow_profiles_up.sh flow-1 flow-2
python run_pov_pipeline.py --check-profiles --flow-profiles flow-1,flow-2
```

`--check-profiles` runs `opencli profile list` and fails loudly when a
configured profile is not connected. **Run it before every batch.** It costs
a second and saves an agent chain plus a TTS run.

If the bridge cannot run on the VPS, the pipeline is still useful: every
other stage works. `images` and `thumb` are the only blockers, and they are
built to fail loudly, not silently:

* `run_flow_images()` verifies every expected `05_IMAGES/<SEG_ID>.jpeg`
  exists and reports exactly which ones are missing,
* the daemon marks the item `failed` with the Chrome-bridge hint and fires an
  `images.failed` notification,
* the profile rotation and 30s backoff behaviour is untouched by this
  deployment; do not "simplify" it.

The hybrid that actually works today: run discovery, agents, TTS and
assembly on the VPS, and keep the image stages on the Windows box where the
bridge is known good. Both machines share the same `POV_PROJECTS_DIR` layout,
so a project folder can move between them.

---

## 7. Running it

```bash
cd /opt/milo-portable-system/artisan/pov_pipeline
set -a; . /opt/milo-portable-system/.env; set +a

python run_pov_pipeline.py --discover          # fill the queue
python run_pov_pipeline.py --queue             # look at it
python run_pov_pipeline.py --once              # one video, end to end
python run_pov_pipeline.py --daemon            # stay up (VPS mode)
```

The daemon respects `cadence.posting_window`, `cadence.timezone`,
`cadence.videos_per_day` and `cadence.daemon_interval_minutes` from
`config/pov_channels.yaml`. It processes one project at a time, writes a
heartbeat every tick, and survives individual project failures.

### cron

See `cron/pov-daemon.example`. Cron gets almost no environment, so every
line sources `.env` itself.

### systemd (better)

```ini
# /etc/systemd/system/pov-daemon.service
[Unit]
Description=POV pipeline daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=milo
WorkingDirectory=/opt/milo-portable-system/artisan/pov_pipeline
EnvironmentFile=/opt/milo-portable-system/.env
ExecStart=/opt/milo-portable-system/.venv/bin/python run_pov_pipeline.py --daemon
Restart=always
RestartSec=30
KillSignal=SIGTERM
TimeoutStopSec=900
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pov-daemon
journalctl -u pov-daemon -f
```

`TimeoutStopSec=900` matters: on SIGTERM the daemon finishes the current
stage before exiting, and killing a half-written ffmpeg render helps nobody.

If you use Flow on this box, add a `pov-xvfb.service` for
`Xvfb :99 -screen 0 1920x1080x24` and make the daemon unit want it.

---

## 8. Logs

| Path | What |
| --- | --- |
| `<POV_STATE_DIR>/pipeline.log` | discovery + daemon lifecycle, heartbeats |
| `<project>/state/pipeline.log` | per-project stage events |
| `<project>/state/runs/<agent>.attempt<N>.log` | raw opencode output |
| `<project>/state/briefs/` | the exact brief each agent received |
| `<project>/state/rejected/` | gate-rejected script drafts |

Both `pipeline.log` files rotate at 5 MB, keeping 3 generations. Cron
redirect files (`state/*.out`) do not rotate; the example crontab includes a
weekly cleanup line.

---

## 9. Backup and rollback

```bash
milo backup            # snapshot the brain (memory + vault) and push it
milo backup --no-push  # local commit only
```

The pipeline's own durable state is small and worth keeping:

```bash
cp artisan/pov_pipeline/data/processed_videos.db ~/pov-ledger-$(date +%F).db
```

Losing that file does not lose finished work: discovery also dedupes against
the `<video_id>_*` project folders on disk.

Rolling back code:

```bash
cd /opt/milo-portable-system
git log --oneline -10
git checkout <good-sha> -- artisan/pov_pipeline
sudo systemctl restart pov-daemon
```

A half-finished project is safe to leave alone. Every stage is resume-safe:
re-running skips work that already exists, and only a gate FAIL ever discards
an artifact (into `state/rejected/`, never deleted).

---

## 10. First-run smoke test, in order

```bash
milo doctor                                             # 1. Milo healthy
python notify.py --test                                 # 2. Telegram
python run_pov_pipeline.py --discover --max-channels 2  # 3. API key + queue
python run_pov_pipeline.py --queue                      # 4. queue populated
python run_pov_pipeline.py --check-profiles             # 5. THE RISK
python run_pov_pipeline.py --once --skip-upload         # 6. full chain, no post
python run_pov_pipeline.py --project <NAME> --stage upload --dry-run-upload
python run_pov_pipeline.py --project <NAME> --stage upload --privacy unlisted
```

Only switch `defaults.privacy` to `public` in `pov_channels.yaml` after you
have watched an unlisted upload end to end.
