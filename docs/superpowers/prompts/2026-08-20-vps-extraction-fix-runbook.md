# Milo — VPS YouTube Extraction Fix Runbook (2026-08-20)

You (Milo, VPS) are struggling with the same extraction block the main PC
just fixed. Both boxes share `_ytdlp.py`, but the PC fix is NOT a drop-in:
the VPS has a local bgutil POT provider on `127.0.0.1:4416` that the PC does
not, so the VPS keeps `mweb,tv,web_safari` clients + POT. Diagnose in this
order — the FIRST FAIL is the thing to fix.

## The PC fix (context, for the VPS)

On the main PC every download died with `The page needs to be reloaded` or
`HTTP Error 403: Forbidden`. Two root causes:

1. **`yt_dlp_ejs` was missing** from the pipeline Python. `_ytdlp.py`'s own
   docstring says a stale/missing ejs is what produces "The page needs to be
   reloaded" (upstream fixed it by bumping yt-dlp-ejs, yt-dlp#16212). The
   `diagnose()` helper in `_ytdlp.py` prints the ejs version as `[FAIL]` if
   missing — one command, no guessing.
2. **Client set.** The VPS-tuned defaults (`mweb,tv,web_safari` + POT on
   :4416) are wrong on a box with no provider. On the PC,
   `android,web_embedded,mweb` + `YTDLP_POT_BASE_URL=off` downloads audio
   clean with NO cookies.

## What to do on the VPS, in order

### 1. Run the diagnosis — one command
From each pipeline root (shorts then ranking):
```
python _ytdlp.py
```
(or with the interpreter the daemon actually runs:
`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe _ytdlp.py`)
Look for `[FAIL]` lines. The order they print is the order of importance.

### 2. Fix a missing/stale yt_dlp_ejs (THE most likely fix)
If the diag shows `yt_dlp_ejs not installed` or an old version, update it in
**every** interpreter the pipelines can run under (system Python + any venv):
```
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe -m pip install -U yt-dlp-ejs
```
Re-run the diag. It should now say `yt_dlp_ejs X.Y.Z` with `[INFO]`.

### 3. Verify the POT provider is actually up
The VPS depends on `http://127.0.0.1:4416` for `mweb`/`tv` formats. The diag
prints `pot server` — it must be `[INFO] ... responding`. If it's `[FAIL] ...
unreachable`, MiloRoutines is supposed to start it; start it manually:
- Server was built at `C:\Users\Administrator\bgutil-ytdlp-pot-provider\server`
  (node). Start it (port 4416), re-run diag, confirm `responding`.

### 4. Check cookies
`C:\milo-portable-system\cookies.txt`. Known-good = ~3630 bytes. ~1624 = the
broken 3P-only export (bot-blocks). If broken, re-export via the CDP recipe
or from the browser — do NOT hand-edit.

### 5. If still blocked after ejs + provider + cookies
The GVS experiment is binding media to PO tokens per-client. On the VPS,
test the client set that works on the PC (no POT needed):
```
set YTDLP_PLAYER_CLIENTS=android,web_embedded,mweb
set YTDLP_POT_BASE_URL=off
python _ytdlp.py   # re-run diag: clients should read android,web_embedded,mweb
```
Then test ONE real audio download (not just metadata) with those env vars.
If android/web_embedded/mweb download clean on the VPS too, set both vars in
`C:\milo-portable-system\artisan\youtube-shorts-pipeline\.env` and the
ranking pipeline config, and note that the VPS no longer needs the :4416
provider for downloads.

### 6. Escalate honestly
If every client still fails on a FRESH ejs + valid cookies, that is the
bot-fingerprint escalation the 08-18 note described (`Video unavailable` /
UNPLAYABLE on most videos from this IP). Then the known-working paths are
real-browser extraction or waiting out the experiment — report it, do not
silently paper over it.

## Note to self (VPS Milo)
- Metadata extract succeeding but the media download 403ing is a PO-token
  binding symptom, NOT an IP ban. Browser on the same IP plays fine.
- The `diagnose()` helper exists precisely so you don't guess. Run it first.
- After any change: `git add -A` only portable code + docs under
  `docs/superpowers/prompts/`, commit, `git pull --rebase` then push, and
  log the run in WORK_CLAIMS.md.