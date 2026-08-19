# Campaign Clipper - Daily Autopilot Routine (Milo)

Trigger: VPS Task Scheduler (`MiloRoutines`), once per day, 09:00 local.
Working dir: `C:\Users\user\Desktop\Milo Video Factory\campaign-clipper-pipeline`

Milo is the driver. The pipeline is a dumb worker: it clips, validates, uploads
and tracks state. It never opens a browser bridge and never sends a Telegram
message. Milo does both.

---

## Prerequisites (check these before the first unattended run)

These are not optional and the loop is silently useless without them.

1. **Two browser sessions, not one.** Intake scrapes the board with
   **Playwright** (`clipster.list_campaigns` / `read_campaign`) using the
   persistent profile at `CLIPSTER_PROFILE_DIR`. Submission uses **opencli**
   against your everyday Chrome. They are different browsers with different
   cookie jars. Being logged into Clipster in Chrome does **not** log in the
   Playwright profile. Run `python -m src.main --mode login` once per machine or
   every intake pass returns zero campaigns.
2. **`OPENCLI_PROFILE` must be set.** There is no default. The bridge refuses to
   submit without it rather than picking whichever Chrome answered first.
3. **Niche map vs. channel guard.** `config/channels.yaml` still routes
   `gambling`, `gaming` and `sports` to `chop_ug`, which the ranking pipeline
   owns. The guard therefore **skips** those campaigns, and they are the bulk of
   the board. Until the map is repointed at a campaign channel, expect intake to
   reject most candidates for "non-campaign channel".
4. **Upload quota is the real cap.** A YouTube upload costs ~1600 quota units
   against a default 10,000/day project, so roughly **6 uploads per day across
   all channels**, not 5 per channel. `CLIPPER_MAX_PER_DAY=5` is inside that
   budget only because it is a global cap. Do not raise it without raising quota.
5. Clips must be **public** before submitting. Views on a private or unlisted
   video do not count, and the board reads the link immediately.

---

## Stage 1 - Intake

```
python -m src.main --mode intake --platform youtube
```

Telegram: `intake: added=N rejected=N skipped=N waiting=N`
Per added campaign: `+ <name> (<id>) - <describe>`
Per rejection, include the reason verbatim; "min followers" appearing every day
is the signal that the follower wall is still the binding constraint.

No fresh campaigns is a **clean no-op**. Notify and exit 0.

## Stage 2 - Build + validate, per campaign

For each enabled campaign in `config/campaigns/*.yaml` that has content folders:

```
python -m src.main --mode build --id <id> --count 3
```

Telegram: `<id>: built K, validated V, rejected R`

A campaign with no content is reported as **waiting for content** and skipped.
Files-only rule: never scrape YouTube for source footage.

## Stage 3 - Upload, per validated clip

While uploads in the last 24h < `CLIPPER_MAX_PER_DAY`:

```
python -m src.main --mode upload --id <id> --clip <clip_id> --privacy public
```

Telegram: `<id> clip <clip_id> -> <video_url>`

A clip whose campaign resolves to no eligible channel is refused by
`upload_clip` and reported. That is the guard working, not a bug.

## Stage 4 - Submit via the opencli bridge

For each `uploaded` clip that has not been submitted:

```python
from src.opencli_bridge import submit
submit(campaign_url, video_url)
```

- `status == 'submitted'` -> mark the clip submitted.
- `status == 'rejected'` -> record the rejection, and **do not retry that
  campaign** until its requirements change.
- `status in ('error', 'unknown')` -> park in the manual queue
  (`clipster.queue_manual`) and notify. Never mark the clip submitted.

Telegram: `submitted <campaign> -> <url>` or `queued <campaign> -> <url>`

## Stage 5 - Status report

```
python -m src.main --mode status
```

One consolidated Telegram report: clips by status, uploads in last 24h, manual
queue length, disk report.

---

## Failure rules

- Per-campaign failures: log, notify, continue the cycle.
- **Every submit erroring in one cycle: stop submitting immediately**, notify,
  and do not retry until a human looks. That pattern means the bridge is down or
  the board was restyled, and continuing spams the board with broken attempts.
- Never mark a clip submitted on anything other than `status == 'submitted'`.
- Discard opencli stderr unless the exit code is non-zero; the extension-update
  notice pollutes it on every call.

## Task Scheduler entry (manual VPS step)

Register a daily 09:00 trigger under `MiloRoutines` that runs this routine.
This is done on the VPS, not from a checkout on another machine.

## What is still unverified

- **The opencli command surface.** `build_submit_steps` assumes
  `opencli browser <session> open|click|type --css ... --profile <p>`. The
  selectors were verified live on 2026-08-18; the *argument shape* of opencli
  1.8.6 was not. If the flags differ, fix them in `build_submit_steps` only:
  every command is constructed there.
- **One real submission.** No link has been put on the board through this code
  path yet. Until that happens the mechanism is untrusted, and the daily loop
  should run with submission parked to the manual queue.
