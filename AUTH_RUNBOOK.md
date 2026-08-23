# Authenticate the YouTube pipelines

Tokens are gitignored and can't be copied between machines, so each machine mints
its own. Refresh tokens also die on their own (revocations, password changes,
unused projects) -- the 8/16 and 8/18 expiries are why this is a script and not a
checklist. Time needed: ~20 min of clicking on a first run.

---

## The short version

Double-click **`reauth_all_channels.bat`** in the repo root.

It runs from `artisan/`, walks every channel in `artisan/yt-secrets/channels.yaml`
one at a time, tells you which Google account each one needs, opens the consent
page in that account's Chrome profile, refuses to write a token that resolved to
the wrong channel, and writes the verified `channel_id` back into `channels.yaml`
itself. No pasting ids, no second terminal, no guessing the account.

```
reauth_all_channels.bat                     every channel in the registry
reauth_all_channels.bat --active            only active: true channels
reauth_all_channels.bat --pipeline ranking  one lane
reauth_all_channels.bat --channel NXS       one channel
reauth_all_channels.bat --unbound           only channels with no channel_id yet
reauth_all_channels.bat --add               register a NEW channel, then auth it
reauth_all_channels.bat --status            refresh-check tokens, no browser
reauth_all_channels.bat --doctor            audit channels.yaml, no browser
reauth_all_channels.bat --sync              fill channel_id from existing tokens
reauth_all_channels.bat --help              all options
```

`auto_auth_all.bat` is the same script under its other name.

One failure does not abort the rest: you get a per-channel OK/FAIL summary and a
final refresh check. Re-run a single channel with `--channel <key>`.

**Run it in a terminal you own.** Never through an agent shell, scheduler, daemon
or background process: the flow owns its own callback server and must stay
foreground.

---

## First run on a new machine

### 1. Pull the repo and install deps

```powershell
cd C:\Users\<you>\milo-portable-system
git pull --rebase
pip install PyYAML google-api-python-client google-auth google-auth-oauthlib
```

### 2. Drop in the two OAuth client files

The `credentials.json` files are gitignored (they are secrets). You need them
before auth. Either copy them from the main PC or download fresh from Google
Cloud Console:

| File | Project | Owner | Where |
|---|---|---|---|
| `artisan/yt-secrets/draallan0/credentials.json` | yt-flick-shorts | draallan0@gmail.com | copy from main PC OR download from console |
| `artisan/yt-secrets/adrasaltsxxx/credentials.json` | milo-mcp | adrasaltsxxx@gmail.com | copy from main PC OR download from console |

On the main PC they live at:
`C:\Users\Administrator\milo-workspace\milo-portable-system\artisan\yt-secrets\<slug>\credentials.json`

Both projects already have every channel's owning email on the consent screen
(added 2026-08-15), so no console setup is needed on a new machine.
`reauth_all_channels.bat --doctor` tells you which credentials files are missing
before you waste a browser round-trip.

### 3. Authenticate

```powershell
.\reauth_all_channels.bat
```

Or the underlying CLI, if you prefer a terminal:

```powershell
cd C:\Users\<you>\milo-portable-system\artisan
python -m yt_secrets auth              # every active channel
python -m yt_secrets auth --all        # every channel in the registry
python -m yt_secrets auth --channel chop_ug
```

Each channel opens the browser, waits (15 min per channel from the .bat, 60 from
the CLI default), refresh-checks the token, compares the resolved channel against
the key's binding, and only then writes `youtube_token_<channel>.json` into the
right pipeline config dir.

Who approves what lives in `channels.yaml` (`email` + `chrome_profile`) and is
printed on screen per channel, so this table is only for reference:

| Channel | Approve as | Chrome profile |
|---|---|---|
| flick_shorts, capital_mindset | draallan0@gmail.com | Profile 3 |
| wealth_mindset, dra_allan_official | adrasaltsxxx@gmail.com | Default |
| NXS, explaination | draallan12@gmail.com | Profile 4 |
| chop_ug, rankdrop | daadaallan0@gmail.com | Profile 1 |
| money_matrix | adrasaltsx@gmail.com | Profile 2 |
| god_did_fx | draallan84@gmail.com | Profile 5 |
| the_other_guys, moviegasm | allandaada@gmail.com | Profile 6 |

### 4. Verify

```powershell
python -m yt_secrets status --all
```

Every channel should print `OK  <key>: <Channel Name> (<channel id>), refresh
works, identity matches ...`. A `BAD` line is either a broken token or, worse, a
token that works but points at the wrong channel. Both are fixed the same way:
`reauth_all_channels.bat --channel <key>`.

---

## Adding a channel to a pipeline

```powershell
.\reauth_all_channels.bat --add
```

It asks for the key, owning email, project slug, lanes (`shorts ranking pov
clipper`) and Chrome profile, derives `token_dir` from the lane, appends the
block to `channels.yaml` without disturbing its comments, then authenticates the
channel and records its `channel_id`. New channels are added `active: true`.

Same thing non-interactively:

```powershell
cd artisan
python -m yt_secrets add --channel new_key --email owner@gmail.com \
    --slug draallan0 --pipeline shorts --pipeline clipper \
    --chrome-profile "Profile 3"
```

---

## Keeping the registry honest

`reauth_all_channels.bat --doctor` is offline and safe to run any time. It flags:

- `token_dir` that does not match the channel's pipelines (authenticates fine,
  publishes nothing)
- two keys claiming the same `channel_id`
- `channels.yaml` disagreeing with `channel_identity.json`
- channels with no binding at all
- missing `credentials.json`, missing tokens, deleted OAuth clients
- `client_from:` pointing at a channel that does not exist

`--sync` is the companion: on a machine that already holds good tokens it
resolves every channel id and writes them into `channels.yaml` without opening a
browser. Run it once and the guard stops depending on the machine-written ledger.

---

## Known pitfalls

- **REFUSED / wrong channel** = you approved while signed into the wrong Google
  account. Nothing was written. Re-run that one channel; if Chrome opened the
  wrong profile, fix `chrome_profile:` for that key in `channels.yaml`.
- **`deleted_client`** = that channel's Google Cloud OAuth client is gone
  (flick_shorts). Borrow a live one with `client_from:` in `channels.yaml`, or
  recreate a Desktop client in its project. The printed runbook covers both.
- **State mismatch** = the pasted URL got clipped or a stale tab hit the callback
  server. Re-run that channel; the .bat never asks you to paste a URL, so this
  only happens when you copy it by hand.
- **`python` not found in a scheduler/system context** — use the full path
  `C:\Users\<you>\AppData\Local\Programs\Python\Python312\python.exe`. The .bat
  prefers the repo `.venv` and falls back to a known install path.
- **Testing mode expires refresh tokens after 7 days** — both projects were set
  to Published, so this is a non-issue unless a new project is added (then
  publish it).
- Tokens are per-machine. Do not copy them around; re-mint instead.
