# Authenticate the YouTube pipelines on a new machine

Verified working process (main PC, 2026-08-15). Tokens are gitignored and can't
be copied between machines, so each machine mints its own. Time needed: ~20 min
of clicking on a first run.

---

## 1. Pull the repo and install deps

```powershell
cd C:\Users\<you>\milo-portable-system
git pull --rebase
pip install PyYAML google-api-python-client google-auth google-auth-oauthlib
```

## 2. Drop in the two OAuth client files

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

## 3. Run auth in a terminal YOU own

Never launch this through an agent shell, scheduler, daemon or background
process — it owns its own callback server and must stay foreground. Open a
normal PowerShell window:

```powershell
cd C:\Users\<you>\milo-portable-system\artisan
python -m yt_secrets auth
```

It walks every `active: true` channel one at a time: opens the browser, waits up
to 60 minutes, then refresh-checks the token and writes
`youtube_token_<channel>.json` to the right pipeline config dir.

You must approve each consent page while signed into that channel's OWNING
Gmail. Active channels and which Google account approves them:

| Channel | Approve as | Chrome profile |
|---|---|---|
| flick_shorts | draallan0@gmail.com | Profile 3 |
| capital_mindset | draallan0@gmail.com | Profile 3 |
| wealth_mindset | adrasaltsxxx@gmail.com | Default |
| NXS | draallan12@gmail.com | Profile 4 |
| explaination | draallan12@gmail.com | Profile 4 |

If a URL is too long to paste reliably (it can get clipped, breaking the state
token → "state mismatch"), serve it through a short redirect. Start a second
terminal and run:

```powershell
python C:\path\to\serve_redirect.py <THE_LONG_URL> 8800
```

Then only paste `http://localhost:8800/` in the browser. Use a FRESH port
(8800, 8801, ...) per channel so no stale redirect is reused.

## 4. Verify

```powershell
python -m yt_secrets status
```

Every channel should print `OK  <key>: <Channel Name> (<channel id>), refresh works`.
If any print `BAD`, the token is broken — re-run `python -m yt_secrets auth --channel <key>`.

## 5. Inactive channels (only if you want them live)

chop_ug, rankdrop, money_matrix, god_did_fx, the_other_guys, moviegasm and
dra_allan_official are authenticated on the main PC but still `active: false`
in `artisan/yt-secrets/channels.yaml`, so the default `auth` run skips them.
To authenticate one explicitly:

```powershell
python -m yt_secrets auth --channel chop_ug
```

Approve as that channel's owner:
chop_ug/rankdrop → daadaallan0@gmail.com (Profile 1) · money_matrix → adrasaltsx@gmail.com (Profile 2) ·
god_did_fx → draallan84@gmail.com (Profile 5) · the_other_guys/moviegasm → allandaada@gmail.com (Profile 6) ·
dra_allan_official → adrasaltsxxx@gmail.com (Default).

## Known pitfalls

- **State mismatch** = the pasted URL got clipped or a stale tab/redirect hit the
  server. Re-run that channel with a fresh `auth --channel` and (if long) a fresh
  redirect port.
- **`python` not found in a scheduler/system context** — use the full path
  `C:\Users\<you>\AppData\Local\Programs\Python\Python312\python.exe`.
- **Testing mode expires refresh tokens after 7 days** — both projects were set
  to Published, so this is a non-issue unless a new project is added (then
  publish it).
- Tokens are per-machine. Do not copy them around; re-mint instead.