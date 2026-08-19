# Pipeline fixes — 2026-08-19

Branch: `fix/pipelines-extraction-auth-hooks`

Read the order, not the list. Nine of the open items are **downstream of
extraction**: while no video can be downloaded, fixing the cadence cap or the
ranking ledger changes nothing observable. So do §1 first.

---

## 1. YouTube extraction — the actual blocker

**What was wrong.** One symptom ("Video unavailable" on every video and every
player client; `dQw4w9WgXcQ` returning "The page needs to be reloaded") was
hiding three independent causes:

1. **The PO Token provider was never invoked.** bgutil 1.3.1 was running on
   :4416 and pip-installed, but yt-dlp logs `[pot] PO Token Providers: none`.
   Two reasons, both real:
   - the configured clients were `android_vr,ios,web_safari`. `android*`/`ios`
     do not use GVS PO Tokens **at all**, and `android` is skipped outright when
     cookies are present. yt-dlp therefore had no reason to ask the provider for
     anything, which is exactly why manual `po_token=web.gvs+<tok>` injection
     also did nothing — it was being attached to attempts that could not use it.
   - plugin discovery is **per interpreter**. `pip install` into one environment
     and running the daemon from another gives `Plugin directories: none` with no
     error and no warning.
2. **"The page needs to be reloaded" is a stale JS-challenge solver, not a ban.**
   Upstream closed that report (yt-dlp#16212) by bumping **`yt-dlp-ejs`**, not by
   changing config. A yt-dlp pinned at 2026.7.4 beside an old `yt_dlp_ejs`
   reproduces it indefinitely.
3. **No browser-grade TLS.** A datacenter IP with a stock urllib fingerprint is
   the cheapest thing in the world to flag. yt-dlp supports curl_cffi
   impersonation natively.

**What changed.** All of it lives in `_ytdlp.py`, because every download in this
repo already goes through `from _ytdlp import NoWritebackYDL as YoutubeDL`. One
choke point fixes `src/downloader.py`, `src/sourcing.py` and every ad-hoc script
without touching their option dicts — and stops the lanes drifting onto
different player clients again.

Defaults now: `player_client=mweb,tv,web_safari`, `fetch_pot=always`,
`formats=missing_pot`, `youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416`,
explicit `plugin_dirs`, curl_cffi impersonation when available, and cookies read
from either `YTDLP_COOKIES_FILE` or `YT_COOKIES` (the ranking lane used the
second name and so had been running with no cookies at all).

**Do this, in order:**

```bash
cd artisan/youtube-shorts-pipeline

# 1. Update the two things most likely to be the whole problem.
python -m pip install -U yt-dlp yt-dlp-ejs bgutil-ytdlp-pot-provider
python -m pip install "yt-dlp[default,curl-cffi]"

# 2. Ask the box what is actually broken. Every line is actionable.
python yt_doctor.py

# 3. Find a client set that works, metadata only, nothing downloaded.
python yt_doctor.py --probe
```

`--probe` walks the client ladder one client at a time and prints which resolved
formats, including `android_vr` as a deliberate control. If it names a winner,
pin it: `YTDLP_PLAYER_CLIENTS=<winner>`.

If nothing passes and `yt_doctor.py` shows all-green on plugin, server, ejs and
curl_cffi, then — and only then — is it IP reputation, and the answer is a
residential egress for extraction. Do not reach for that first; it was not the
cause the last four times.

**Escape hatches:** `MILO_YTDLP_HARDEN=0` disables everything except the cookie
protection. `YTDLP_POT_BASE_URL=off` proves whether a failure is POT-related.

---

## 2. Per-source cadence cap — was silently unenforced

`config.upload_max_per_source` has been 3/day since the 8/09 burst.
`safe_upload.py` never read it. Today `flick_shorts` took 6 clips from
`uUAH82U_jXU` and `capital_mindset` took 6 from `yveLqk3DCNs` — both inside the
6/channel cap, both double the cadence rule.

Selection is now a pure function (`select_uploads`) that spends **both** budgets
and round-robins across sources, so a rich source cannot front-load the queue
even when it is the only source with clips. The old loop was semicolon-chained
one-liners with the budget arithmetic inlined into the print statements, which is
a large part of why a missing check was invisible; it now has tests reproducing
the exact 8/19 numbers.

```bash
cd artisan/youtube-shorts-pipeline && python -m pytest tests/test_per_source_cap.py -q
```

The summary now distinguishes `channel_cap_skips` from `source_cap_skips`, so
"nothing uploaded" tells you which cap bit.

---

## 3. Channel identity — wrong-channel uploads are now impossible

The 8/16 incident (the `wealth_mindset` token authed as **Chop UG**, 4 rogue
uploads, ids 465–468) was not a fluke, it was the design: every tool resolved
the channel, **printed** it, and proceeded regardless. There was nowhere
recording "`wealth_mindset` means channel UC…", so nothing could disagree.

Now:

- `artisan/yt_secrets/identity.py` holds the binding
  (`yt-secrets/channel_identity.json`, machine-written) and the comparison.
  `channels.yaml` gained a hand-maintained `channel_id:` per channel which wins
  over the ledger.
- `python -m yt_secrets auth` **refuses to write a token** whose resolved channel
  does not match the binding. First auth binds; `--rebind` is required to move a
  key to a different channel.
- all three publishers assert identity after building the API client and before
  any upload. They were already fetching `channels.list(mine=True)` — they just
  never compared it to anything.
- `python -m yt_secrets status` reports `BAD … WRONG CHANNEL` instead of a
  cheerful OK for a token that refreshes fine against the wrong account.

Modes via `MILO_CHANNEL_IDENTITY`: `learn` (default — binds on first sight, then
enforces), `enforce` (an unbound key is an error), `off`.

**Worth ten minutes:** run `status`, then paste each reported id into
`channels.yaml`. Start with `chop_ug` — once its id is recorded, any token
resolving there under another key is rejected outright.

---

## 4. flick_shorts OAuth client — still needs you

Its client (`929304292327-aggfh…`) is deleted in Google Cloud, so re-auth returns
`deleted_client` forever. Two changes:

- `deleted_client` now prints the recovery runbook rather than an opaque error.
- `client_from: wealth_mindset` in `channels.yaml` makes "borrow a live OAuth
  client" **configuration** instead of folklore. Already set for `flick_shorts`.

That unblocks re-auth today, but the borrowed project's 10k/day quota is then
shared. The real fix is still a manual one: Google Cloud Console → the
`yt-flick-shorts` project → Credentials → OAuth client ID → **Desktop app** →
download to `artisan/yt-secrets/draallan0/credentials.json`, publish the consent
screen (Testing mode expires refresh tokens after 7 days), then drop
`client_from`. Until then it stays a landmine: the current token works, but it
cannot be re-minted against its own client.

---

## 5. Unattended OAuth hangs — fixed

All three publishers fell through to `InstalledAppFlow.run_local_server(port=0)`
when a token was missing or unrefreshable. Inside the 9AM daemon that blocks
forever on a browser nobody will open, so a revoked token presented as "the sweep
is still running" rather than as a failure — worse than an error. It now fails
fast and names the channel to re-auth. `MILO_ALLOW_INTERACTIVE_AUTH=1` for a
human-run session.

---

## 6. Clip editing — hook → story → payoff

### The bug found on the way in

`campaign-clipper-pipeline/src/highlights.py` wrote every lexicon pattern as
`r'\\b(wait|watch|…)'`. In a raw string that is a **literal backslash followed by
`b`**, not a word boundary — so those patterns could only match text containing a
real backslash, which transcript text never does. Same for
`re.findall(r"[\\w']+")`, which made `_score_text` tokenise to an empty list and
return `(0,0,0,0)` for **every window ever scored**.

So `setup_score`, `payoff_score` and `relevance_score` have always been `0.0`,
and the composite score reduced to `0.24*motion + 0.14*audio`. **55% of the
ranking weight was dead.** Every clip this lane has ever produced was chosen by
"which 22 seconds were loudest", with no idea whether anything interesting was
being said. That is the single biggest reason the output does not hook anyone,
and it is fixed independently of the feature below.

### The feature

`src/story_edit.py` implements the structure from the clipping playbook. A clip
is hook → story → payoff, and the reliable way to manufacture one from found
footage is to **open on a question and not answer it until the end**. The
renderer can now emit a multi-span edit instead of one continuous cut:

| style | what it does |
| --- | --- |
| `question_first` | the strongest question in the window is lifted to the front, the rest plays in order behind it (the cows-on-the-plane cut) |
| `cold_open` | a ~1.8s payoff teaser up front, then the clip in full |
| `straight` | one continuous cut — previous behaviour |
| `auto` | question_first → cold_open → straight, first that fits (**default**) |

Nothing is fabricated: the words and pictures are the source's own, only their
order changes. Duration is preserved, so a reorder never pushes a clip outside a
campaign's legal band — a clip one second under the minimum is a wasted daily
submission slot.

**Captions stay in sync structurally, not arithmetically.** One function
(`remap_segments`) owns the source→output timeline mapping, and both the
filtergraph and the ASS captions are derived from the same span list and the same
offsets. If a span moves, the picture and the text move together. Same discipline
the shorts lane uses for keyframe drift, same reason.

**The on-screen title hook is now lifted from the footage** — "ARE COWS ALLOWED
ON THE PLANE?" instead of a template-pool "WAIT FOR IT". It is specific, it
creates a real curiosity gap, and the audio confirms it two seconds later instead
of contradicting it. It sits at the top of the frame (`HOOK_Y_RATIO`) clear of
the speech captions in the lower third — two blocks of large text in the same
band make both unreadable on a phone, and ~80% of Shorts viewers scroll with the
sound off, so those two layers are the only things doing any work for them.

A new `clipfarm` caption preset gives the one-group-at-a-time look:
`CAPTION_STYLE=clipfarm`.

**Single-span clips still take the old ffmpeg path byte for byte**, so nothing
that passes validation today changes shape.

```bash
cd artisan/campaign-clipper-pipeline
python -m pytest tests/test_story_edit.py -q
python -m src.main --mode build --campaign castle_clipping --count 3   # then WATCH them
```

Knobs: `EDIT_STYLE`, `HOOK_TEXT_ENABLED`, `HOOK_UPPERCASE`, `HOOK_MAX_WORDS`,
`HOOK_Y_RATIO`, `HOOK_TAIL_SECONDS`, `COLD_OPEN_SECONDS`,
`EDIT_MIN_SPAN_SECONDS`, `HOOK_PREFER_TRANSCRIPT` — all also settable from
`clipper.yaml`'s `style:` block. `EDIT_STYLE=straight` is the single switch back.

`VIDEO_FPS` deliberately stays 30. The playbook exports 60 and for fast-motion
footage that is right, but it roughly doubles encode time per clip on a 2-core
box and is invisible on talking-head sources. Set `VIDEO_FPS=60` per campaign
when the footage is actually moving.

---

## 7. State that was lying to us

**Shorts.** All 72 `generated_shorts` rows read `status='queued'` while 55 are
published, because `mark_short_uploaded` only ever wrote `youtube_short_id`. Not
cosmetic: `get_queue_health` and `get_queued_clips_for_upload` both filter on
`status='queued'`, so every published clip still counted toward the queue — the
queue looked permanently full, which suppressed discovery. `safe_upload` now
writes both, and:

```bash
cd artisan/youtube-shorts-pipeline
python repair_state.py                      # dry run, prints the diff
python repair_state.py --apply
```

It also marks rows whose `local_path` no longer resolves as `file_missing`, so
they stop being re-selected and re-failed on every sweep.

**Ranking.** 74 builds: 55 uploaded, 19 failed, every failure pointing at
`C:\Users\user\Desktop\Milo Video Factory\…` from the old PC.

```bash
cd artisan/ranking-shorts-pipeline
python repair_builds.py --db data/ranking.db
# if the renders were copied to this box under a new root:
python repair_builds.py --db data/ranking.db --remap 'C:\Users\user\Desktop\Milo Video Factory=/srv/milo' --apply
```

It requeues any build whose file is actually found and marks the rest
`failed:file_lost` so they stop looking retryable. It will **not** unwedge the
lane — ranking has produced nothing since ~8/17 because downloads are blocked
(§1). This only makes "no pending builds" mean the queue is genuinely empty.

---

## 8. Cookie writeback — protected, now tested

`YoutubeDL.save_cookies()` rewriting the shared cookiefile and dropping the 1P
auth cookies was fixed previously via `NoWritebackYDL`. It now has a regression
test (`tests/test_ytdlp_hardening.py`) rather than a comment asking nicely.

---

## Deliberately NOT changed

- **The 9AM daemon triggers.** Both daemons are boot-once + APScheduler
  self-scheduling; adding a second 9AM task trigger creates a double-daemon
  race. Nothing here touches a trigger, a schedule or a scheduler file. If a
  second run ever becomes necessary it needs a lock file, not a second trigger.
- **The 4 rogue Chop UG uploads (ids 465–468).** Still live. That is a judgement
  call about a public channel's history, so it stays yours: deleting them cleans
  the channel but also deletes whatever watch time they earned. The guard in §3
  is what stops it recurring; it does not undo it.
- **Cookie rotation.** Still an external dependency: cookies must be re-exported
  regularly via opencli CDP, and MiloRoutines does it on the VPS. If that stops,
  downloads fail as a confusing "bot-check". `yt_doctor.py` reports the cookie
  file it resolved, which turns that from hours of guessing into one line — but
  it does not monitor the rotation. A cron canary on the cookie file's mtime
  would be the next real improvement here.
