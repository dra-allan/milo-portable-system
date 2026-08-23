# VPS: what changed 2026-08-23 and how to re-authenticate

Companion to `AUTH_RUNBOOK.md`. Read this first if you are sitting at the VPS
(`C:\milo-portable-system`, Windows Server 2025) wondering why tokens are dead
or pointing at the wrong project. Everything below was done and verified on the
main PC the same day; the VPS still needs its own token mint because **tokens
are per-machine and cannot be copied**.

---

## 1. What changed

### The OAuth split: two Google Cloud projects, not one

Every channel used to share ONE OAuth client (old milo-mcp,
client `612279340654-…`), so all uploads drew from a single 10k/day YouTube
quota bucket — that was the 400/429 upload wall. As of 2026-08-23 the channels
are split across TWO projects, each with its own `credentials.json` and its own
quota bucket:

| | PROJECT 1 | PROJECT 2 |
|---|---|---|
| Console owner | draallan0@gmail.com | adrasaltsxxx@gmail.com |
| Client id (short) | `929304292327-e00rk…` | `222141244525-gq84b…` |
| credentials.json at | `artisan/yt-secrets/draallan0/credentials.json` | `artisan/yt-secrets/adrasaltsxxx/credentials.json` |
| Test users added | draallan0, daadaallan0, allandaada | adrasaltsxxx, draallan12, adrasaltsx, draallan84 |
| Channels | flick_shorts, capital_mindset, chop_ug, rankdrop, moviegasm, the_other_guys | wealth_mindset, dra_allan_official, NXS, explaination, money_matrix, god_did_fx |

Which project mints a token is decided by the channel's `slug:` in
`artisan/yt-secrets/channels.yaml`. The slugs already match the split — no
edits needed on the VPS.

Notes:

- Project 1's original OAuth client (`…-aggfh…`) had been DELETED, which is
  what killed the flick_shorts token. It was recreated inside the same
  yt-flick-shorts project, so `flick_shorts` no longer borrows another
  channel's client (`client_from:` was removed from channels.yaml).
- BOTH projects are set to **In production** in Google Console. In Testing
  mode refresh tokens die after 7 days — do not flip them back.
- Old milo-mcp client `612279340654` is retired. Any token still minted
  through it draws the OLD shared quota bucket and should be re-minted.

### Registry state (channels.yaml)

- All 12 channels were authenticated and verified on the main PC. Every
  `channel_id:` is now filled in, which arms the identity gate EVERYWHERE,
  including here: a consent approved while signed into the wrong Google
  account writes nothing and reports REFUSED. This is the guard born from the
  8/16 Chop UG incident — do not bypass it.
- `chrome_profile:` values were remapped to the MAIN PC's real Chrome profile
  directories (Profile 4 = draallan0, Profile 5 = draallan12, Profile 12 =
  daadaallan0, Profile 6 = draallan84, Profile 7 = allandaada, Default =
  adrasaltsxxx). **These names mean nothing on the VPS.** They point at
  profiles that do not exist here, and Chrome would silently spawn fresh
  unsigned profiles. On the VPS, authenticate with `--no-chrome-profile`
  (section 3) unless you deliberately sign the accounts into Chrome here.
- `ranking_general_commentary` niche was DELETED from niches.yaml (its upload
  target never existed as a channel). `moviegasm` is `active: false` on the
  shorts/clipper lanes (campaigns still reach it via the campaign clipper's
  own config). Neither affects authentication.

---

## 2. Before you start (VPS checklist)

Run everything in a **foreground RDP session**, your own terminal. Never an
agent shell, scheduler or daemon: the flow owns a localhost callback server.

1. Pull:

   ```powershell
   cd C:\milo-portable-system
   git pull --rebase
   ```

2. Copy the two OAuth client files from the main PC (gitignored, so pull does
   NOT bring them):

   ```text
   PC   : C:\Users\user\Desktop\milo-portable-system\artisan\yt-secrets\<slug>\credentials.json
   VPS  : C:\milo-portable-system\artisan\yt-secrets\<slug>\credentials.json
   ```

   for `<slug>` = `draallan0` AND `adrasaltsxxx`. RDP clipboard or scp both
   work. Verify with:

   ```powershell
   dir C:\milo-portable-system\artisan\yt-secrets\*\credentials.json
   ```

3. Inventory what survives (offline, no browser):

   ```powershell
   .\reauth_all_channels.bat --status
   ```

   Every `OK` line is a working token — but check the fine print: tokens
   minted before the split draw the OLD quota bucket. For clean buckets,
   re-mint everything that matters; at minimum re-mint every `BAD` line.

---

## 3. Authenticate (the VPS way)

The `.bat` opens Chrome pinned profiles, which do not exist here. Use the
underlying CLI with `--no-chrome-profile`; it opens the DEFAULT browser
(Edge is fine for Google consent) and prints the required Google account for
each channel:

```powershell
cd C:\milo-portable-system\artisan

# everything, one consent at a time
python -m yt_secrets auth --all --no-chrome-profile

# or just the dead ones
python -m yt_secrets auth --channel flick_shorts --channel chop_ug --no-chrome-profile
```

For each channel, on the consent screen:

1. If the browser is already signed into the WRONG Google account, sign out or
   use "Choose an account" → pick the email printed in the terminal. The
   account → channel map lives in `channels.yaml` (`email:` per key); the
   short version:

   | Approve as | Channels |
   |---|---|
   | draallan0@gmail.com | flick_shorts, capital_mindset |
   | adrasaltsxxx@gmail.com | wealth_mindset, dra_allan_official |
   | draallan12@gmail.com | NXS, explaination |
   | daadaallan0@gmail.com | chop_ug, rankdrop |
   | draallan84@gmail.com | god_did_fx |
   | allandaada@gmail.com | moviegasm, the_other_guys |
   | adrasaltsx@gmail.com | money_matrix |

2. Brand-account chooser (if shown): pick the CHANNEL named by the key, not
   just the person. This step is where the 8/16 mix-up happened.
3. Unverified-app warning: Continue (both projects have all owners as test
   users).
4. Consent scope list: tick **Select all**, then Continue.

The tool refresh-checks the token, resolves the live channel, compares against
the binding, and only then writes `youtube_token_<key>.json` into the right
pipeline config dir. One failure does not abort the rest.

Optional quality-of-life: install Chrome on the VPS and sign in each Gmail
once, then put real `chrome_profile:` values back for this machine — after
that the plain `.bat` works here too. Not required; the CLI path above is
enough.

---

## 4. Verify

```powershell
cd C:\milo-portable-system\artisan
python -m yt_secrets status --all
```

Want exactly 12 lines like:

```text
OK  flick_shorts: Flick Shorts (UCFWY9jrOMuauvho3XpIzACw), refresh works, identity matches channels.yaml
...
```

Then nothing else to do: the pipeline daemons read tokens from their config
dirs at upload time and pick up fresh mints on their next scheduled run
(shorts 08:45, ranking 08:49 daily, plus AtStartup triggers). No daemon
restart needed.

If a lane still fails to publish after status is green, check `--doctor`:

```powershell
.\reauth_all_channels.bat --doctor
```

Zero ERROR lines is healthy (WARNs about research niches with no upload
target are known and deliberate).

---

## 5. Quick reference

```text
reauth_all_channels.bat --status          offline: refresh-check every token
reauth_all_channels.bat --doctor          offline: audit the registry
reauth_all_channels.bat --sync            offline: fill channel_id from good tokens
python -m yt_secrets auth --all --no-chrome-profile      VPS re-auth (default browser)
python -m yt_secrets auth --channel KEY --no-chrome-profile
python -m yt_secrets list                 show registry selection
```

Gotchas:

- `deleted_client` = authenticating against a dead OAuth client. With the
  recreated clients this should be gone; if you see it, the slug's
  credentials.json on THIS machine is stale — re-copy from the main PC.
- `REFUSED … wrong channel` = consent approved on the wrong Google account or
  brand account. Nothing was written. Re-run that one channel.
- `invalid_grant` = refresh token expired/revoked. Normal death; re-auth.
- Never copy `youtube_token_*.json` between machines — they are per-machine.
  Credentials.json MAY be copied (it is the project client, not a grant).
