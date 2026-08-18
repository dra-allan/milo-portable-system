# Campaign Clipper Autopilot — Design

Date: 2026-08-18
Status: LOCKED by Allan (2026-08-18, after Opus review). Autonomy design stands.
Owner: Milo

> **DECISION (Allan, 2026-08-18):** Campaign lane is deferred, not dead. All 21
> posted campaign clips (duel_yt_shorts_4, posted 08-15, capital_mindset 11 /
> wealth_mindset 5 / flick_shorts 5) measured **0 views, 0 likes, 0 comments**
> three days after upload — zero algorithmic push, plus the Roobet 1000-sub
> follower gate makes every board submission Ineligible. Opus's review
> recommended freezing the lane. **Allan overrides: keep this autopilot design
> and its autonomy exactly as envisioned — do NOT build the robot to run it
> now. Embark on campaigns only once the shorts channels have enough traffic.**
> Until then the shorts/ranking pipelines are the active lane and the goal is
> growing numbers (subs + views). This doc stays as the ready-to-run blueprint
> for when traffic unlocks the gate.

## Problem

The campaign-clipper pipeline produces clips but has never made money. 81 clips
built, 0 uploaded, 0 submitted. Root causes verified live on 2026-08-18:

1. **Eligibility wall** — all 47 prior submissions under Roobet [CLIPPING] 3 are
   marked **Ineligible** on `/activity/submissions`. Roobet requires "Min
   Followers per Social Profile: 1000"; the channels have 0-22 subs. Boards pay
   per view; a submission under a gated campaign earns nothing regardless of
   views.
2. **No automation** — `.env` has `CLIPPER_UPLOAD_CHANNEL` empty,
   `CLIPPER_AUTO_UPLOAD=false`, `CLIPPER_AUTO_SUBMIT=false`. Nothing posts or
   submits itself.
3. **Auth broken** — `wealth_mindset` token was Chop UG's (wrong channel), NXS
   was `invalid_grant`. Both fixed and verified during this session
   (2026-08-18): wealth_mindset now authenticates as UCHBboDmff-Ns2z0vDp2aYEg,
   NXS as UCK88b8L-4ggp0u9GfQk2Ung.

## Goal

A **daily autopilot** that finds fresh campaigns (no min-follower gate, <20%
used), clips campaign content, posts to the right channel, submits the short
URL to the board, and reports everything to Telegram. Run by Milo, driven by
Task Scheduler on the VPS.

## Non-goals (decided with Allan)

- **No YouTube source scraping.** Raw footage comes from campaign content
  folders only (Drive shares / Discord drops / local folders). Auto-clip means
  running `build` against downloaded files, nothing more.
- **No gates on campaign intake.** Fresh campaigns are auto-added when they pass
  the eligibility filter. Telegram notifications inform; they never block.
- **No Playwright.** Submit rides the opencli browser bridge against Allan's
  existing Chrome + Clipster session. No re-login, no separate profile.

## Roles (locked)

| Actor | Job |
|---|---|
| **Allan** | Owns Chrome + Clipster session. Logs into Clipster once per machine. Never re-authenticates. |
| **Milo** | The driver. Runs the pipeline stages, calls opencli browser for submissions and activity reads, sends Telegram reports. The only thing that touches both pipeline and browser. |
| **Pipeline** (`campaign-clipper-pipeline`) | Pure worker. Clips, validates, uploads, tracks state in `clipper.db`. No browser, no opencli, no Telegram. |
| **Chrome / Clipster session** | The authenticated submit surface, used only through opencli. |

## Architecture

Three layers, never crossed:

```
Task Scheduler (VPS, MiloRoutines, daily)
        │
        ▼
Milo (driver) ─────────────────────────────┐
   │                                        │
   ├─ pipeline scan board → intake fresh    │
   ├─ pipeline build → validate → upload    │
   ├─ opencli browser: submit short URL     │  → Allan's Chrome
   ├─ opencli browser: read submissions     │    (Clipster session)
   └─ Telegram: status report to Allan ─────┴─ Telegram
```

## Daily loop

Order of stages, each reported to Telegram as it runs:

```
scan board → intake fresh campaigns → pull content → build clips
→ validate → upload → submit → status report
```

Failures are **per-campaign** and reported; a stuck campaign never blocks the
rest of the cycle. The run stops at the first stage that has nothing to do
(e.g. no fresh campaigns → notify + exit cleanly).

## Section 1 — The daily loop (driver)

- Milo routine, not a standalone script. Scheduled via the VPS Task Scheduler
  (MiloRoutines), one cycle per day.
- Milo calls the pipeline's existing `--mode` commands as subprocesses.
- Milo drives opencli browser for every submit and every activity-page read.
- Telegram notifications stream per stage; one consolidated status report at
  the end.
- Pipeline safety flags flip on for the daily run
  (`CLIPPER_AUTO_UPLOAD=true`, `CLIPPER_AUTO_SUBMIT=true` are **not** required
  since Milo drives each stage explicitly — but the DB status gates stay):
  uploads only from `validated` clips, submits only from `uploaded` clips.
  Build-only preview (`--mode run`) remains available for Allan.

## Section 2 — Campaign auto-intake

New module `src/intake.py` wrapping existing pieces:

1. **Scan** — `list_campaigns('youtube')` scrapes the board; keep cards with
   `progress < 20%`.
2. **Open each candidate page** — `read_campaign(url)` already separates
   obligations (green) from prohibitions (red). Reject a campaign if its
   requirements contain:
   - a min-followers / min-subscribers gate,
   - a min-views or engagement-percentage gate,
   - a platform mismatch (not YouTube).
3. **Compile + auto-add** — `compiler.compile_to_file(...)` writes the spec to
   `config/campaigns/`; DB records it; a `seen_campaigns` set prevents
   re-adding the same URL on later days.
4. **Content check** — a spec with no `content_folders`/`local_folders` is
   marked **waiting for content** and notified on Telegram. It is not built
   (files-only rule) but stays in config so content can be dropped in anytime.

The eligibility reject-list is data, not code: a rules block in `clipper.yaml`
(e.g. `intake_reject_keywords`) so Allan can relax/tighten it without code
changes.

## Section 3 — Channel assignment, upload, submit

- **Channel assignment** — `_channel_for()` already resolves per campaign:
  explicit spec `upload_channel` wins, then the niche→channel map
  (`channels.yaml`, updated 2026-08-18 for campaign channels). Campaign clips
  only go to: **capital_mindset, wealth_mindset, flick_shorts, moviegasm, NXS**
  (all verified tokens). A campaign whose niche maps to a non-campaign channel
  (chop_ug, rankdrop, the_other_guys, explaination) is **skipped with a
  Telegram notice**.
- **Upload** — `upload_clip()`: validated-only, daily caps
  (`CLIPPER_MAX_PER_DAY`, per-campaign caps) respected.
- **Submit** — via **opencli browser** (see Section 4). After each submit, the
  status report carries the result (submitted OK / rejected / queued manual).
  A failed submit parks in the existing manual queue (`manual_submissions.json`)
  with a Telegram heads-up.
- Unknown-niche campaigns map to `flick_shorts` (the "post anything, go with
  what viral" channel) per Allan's strategy.

## Section 4 — Submission mechanism (opencli browser)

- `opencli` 1.8.6 browser bridge (modified, vendored inside milo) drives
  **Allan's Chrome**, bound via a session. Works on the VPS.
- The pipeline **never** calls opencli; **Milo** issues the opencli commands.
- Verified selectors (live-tested 2026-08-18):
  - campaign card: `button[id^=discover-campaign-card-…]`
  - open dialog: `#submit-content-button`
  - fill field: `input#content-url` (name=`content_url`) with the short URL
  - send: `#submit-content-send-button`
  - result read: `https://www.clipster.gg/activity/submissions` (status badges)
- Multiple Browser Bridge profiles connected → ambiguity error. Milo must set
  the profile (`OPENCLI_PROFILE=<profile>` or `opencli profile use <name>`)
  before browser commands. Extension-update notice pollutes stderr; discard
  stderr or filter it in the routine.

## Section 5 — Telegram reporting

- Notifications per stage: campaign picked up, clips built (count), upload
  result (URL + privacy), submit result, anything blocked/rejected.
- One consolidated **status report** at end of cycle: clips by status,
  submissions today, manual queue length, disk report.
- No gates: notifications inform, never block.

## Error handling

- Per-campaign try/except: a failed campaign is logged + reported, the rest of
  the cycle continues.
- No fresh campaigns → notify + exit 0 (clean no-op).
- Submit failure → manual queue + Telegram heads-up (existing fallback,
  `queue_manual` / `clear_manual`).
- Eligibility wall on a live submission (e.g. "not eligible" on the page after
  submit) → record as rejected, do not retry that campaign until its
  requirements change.
- Board restyle breaks selectors → opencli returns errors; Milo reports and
  pauses submissions (no silent spam of broken submits).

## Testing

1. **Intake filter unit tests** — parse fake requirement rows; assert
   min-followers/min-views gates are rejected, clean campaigns pass.
2. **Live submission test (one)** — during implementation, Milo runs one real
   submission through opencli against a known campaign and confirms it lands on
   `/activity/submissions`. This is the trust-earning gate for the mechanism.
3. **Channel assignment tests** — niche→channel resolution, skip for
   non-campaign channels.
4. **Dry-run daily loop** — Milo runs the loop with upload/submit disabled,
   verifies build+validation+intake, then enables publish.

## Open questions

- None blocking. (Auth fixed, roles locked, mechanism chosen.)

## Files touched

- `artisan/campaign-clipper-pipeline/src/intake.py` — new (auto-intake).
- `artisan/campaign-clipper-pipeline/src/config.py` — intake rules block,
  opencli session/profile settings.
- `artisan/campaign-clipper-pipeline/src/clipster.py` — submit result parsing
  for opencli mode (or a small opencli wrapper module).
- `artisan/campaign-clipper-pipeline/config/clipper.yaml` — intake reject
  keywords, opencli session name/profile.
- Milo routine definition (VPS Task Scheduler + routine prompt) — new.
- `reauth_channel.py` — already fixed this session (`flow.credentials`),
  committed as 06e8b62.
