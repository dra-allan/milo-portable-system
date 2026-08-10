# PLAN: GTA 6 Hype Niche (two-lane)

> Written 2026-08-10. Hard dates below are verified against Rockstar/Take-Two, not vibes.

## The clock (this drives everything)

| Date | Event | Why it matters |
|---|---|---|
| **2026-08-27, 3PM ET** | Rockstar "An Extended Look" | Biggest search spike between now and launch. **17 days out.** The channel must be live and posting *before* this, not after. |
| Summer 2026 | Marketing campaign + pre-orders (Take-Two confirmed) | Continuous official-asset drops = continuous free source material. |
| **2026-11-19** | GTA VI launch, PS5/Xbox Series | 101 days out. Reaffirmed on the FY26 earnings call, no delay. |

The real prize is **YPP before launch**. Shorts monetisation needs 1,000 subs + 10M valid Shorts views in a rolling 90 days. Starting today, the 90-day window closes almost exactly on launch week. Start next month and you watch the biggest gaming event ever happen on an unmonetised channel.

## Positioning

Faceless **GTA 6 countdown / news hub**. Narrated, not gameplay. The operator does not play GTA and does not need to.

**Do not put "GTA" or "GTA 6" in the channel name or handle.** Trademark exposure on the brand itself, and it boxes you in post-launch. Pick a neutral brand (Leonida-flavoured, Vice-flavoured, whatever) so the channel survives a Rockstar mood swing and can pivot to general gaming in December.

## Architecture: use both pipelines, change no code

The instinct to bolt search-query discovery onto `youtube-shorts-pipeline/src/discovery.py` is wrong. That capability already exists in `artisan/ranking-shorts-pipeline` (`queries:` in `config/ranking.yaml`, yt-dlp search, clip vetting, phash dedupe, forked TTS voice-over, FFmpeg overlays, stitch, upload). Rewriting `discovery.py` duplicates working code and breaks the isolation rule in the ranking `PROJECT_BRIEF.md`.

### Lane A - primary volume (`ranking-shorts-pipeline`)

Original TTS narration over sourced b-roll, assembled as a countdown. This is both the **top-performing GTA Shorts format** ("5 details you missed") and the only lane that produces genuinely transformed output, which is what survives YPP review.

```yaml
  gta6_countdown:
    title: "TOP {n} GTA 6 DETAILS"
    queries:
      - "gta 6 trailer breakdown details"
      - "gta 6 extended look analysis"
      - "gta 6 map leonida breakdown"
      - "gta 6 details you missed"
      - "gta 6 confirmed features"
      - "gta 6 vs gta 5 comparison"
    channels: []
    extra_sources: []
    negative_keywords: ["leaked footage", "leaked gameplay", "leaked build", "fan made", "concept", "mod showcase", "reaction", "livestream", "podcast", "ai generated"]
    channel: gta6_countdown
    tags: ["gta6", "gta6trailer", "leonida", "gtavi", "shorts"]
```

**Known friction with the shipped `defaults:` block - check before first run:**

- `max_music_confidence: 0.55` will reject official trailer footage outright; Rockstar trailers are wall-to-wall score. If topic-level overrides of `defaults` are not supported, trailer b-roll is off the table and analysis/gameplay b-roll carries the lane.
- `max_text_coverage: 0.18` + `blur_detected_text: true` will bin a lot of GTA news footage, which is plastered in on-screen text and channel watermarks. Raise `candidates_per_topic` from 40 to ~80 for this topic or the vet stage will starve the render.
- `max_source_duration: 900` is fine; GTA breakdowns run 8-15 min.

### Lane B - fast news beats (`youtube-shorts-pipeline`)

Straight clipper on a tight, verified channel list. Talk-heavy analysis transcribes cleanly, same as the podcast niches.

```yaml
gta_hype:
  # Requires: python -m src.uploader auth --channel gta_hype
  upload_channels:
    - gta_hype
  channel: gta_hype
  max_videos: 4
  # Every handle below MUST be verified with yt-dlp before committing. The
  # ranking niche shipped with five dead handles (@Factnomenal, @TopTenz,
  # @Alltime10s et al) and each one costs a wasted round-trip per sweep.
  channels:
    - "@MrBossFTW"
    - "@LegacyKillaHD"
    - "@GTASeriesVideos"
    - "@SernandoE"
    - "@gameranx"
    - "@DigitalFoundry"
    - "@DarkViperAU"
    - "@IGN"
    - "@GameSpot"
    - "@Nought"
    - "@TheGamer"
    - "@RockstarGames"
  keywords:
    - "gta 6"
    - "gta vi"
    - "grand theft auto 6"
    - "grand theft auto vi"
    - "leonida"
    - "vice city"
    - "jason and lucia"
    - "trailer breakdown"
    - "extended look"
    - "frame by frame"
    - "details you missed"
    - "confirmed feature"
    - "release date"
    - "pre order"
    - "map size"
    - "open world"
    - "rockstar confirmed"
    - "everything we know"
    - "what nobody noticed"
    - "hidden detail"
  # NOTE: "trailer" and "teaser" are deliberately ABSENT from the negatives.
  # Every other niche in this file blocks them. discovery.py word-boundary
  # matches on the TITLE, so inheriting that habit here would reject
  # "GTA 6 Trailer 3 Breakdown" - the single best source video type we have.
  # Same reason "news" and "breaking news" are absent.
  negative_keywords:
    - "#shorts"
    - "shorts"
    - "clip"
    - "clips"
    - "compilation"
    - "reaction"
    - "livestream"
    - "live stream"
    - "music video"
    - "fan made"
    - "concept trailer"
    - "leaked footage"
    - "leaked gameplay"
    - "leaked build"
    - "mod showcase"
    - "gta 5 gameplay"
    - "roleplay"
    - "rp server"
    - "giveaway"
    - "modded account"
  language: en
  min_duration: 240
  max_duration: 5400
  min_score: 0.48
  # 45, not 365. Stale GTA rumours get debunked within weeks. Posting a
  # six-month-old leak as news is how a news channel loses its audience.
  preferred_upload_days: 45
  # 15000, not 50000. min_views is a RECENCY TAX: a 50k gate means we only
  # ever see source videos after they have aged one to three days, which is
  # exactly backwards for a niche whose entire edge is being early.
  min_views: 15000
```

## Repo-specific gotchas the generic advice misses

1. **Keyword score is capped at ~6 hits** (see the `ranking_general_commentary` header note in `niches.yaml`). `PLAN_MINECRAFT_NICHE.md` and `PLAN_ROBLOX_NICHE.md` list 200-300 keywords each; junk hits saturate the cap and crowd out real signal. The list above is deliberately short and discriminative. Do not "improve" it by adding more.
2. **`discovery.py` enforces one source video per niche per 24h** (`_niche_processed_today`, DB-backed, applies to manual runs too). Lane B is therefore hard-capped at one source/day. Do not fight it: one 15-minute breakdown yields 3-5 clips, which is a day of posting. Lane A carries the volume.
3. **`_source_rank` self-prunes.** Sources recorded under 200 avg views get deprioritised automatically. Front-load 12-15 handles and let the ranker sort them instead of hand-curating.
4. **Negative keywords match title only, not description.** Anything hiding in the description gets through. Vet the first ten renders by hand.
5. `TITLE_OPTIMIZER=true` handles hooks. Feed it raw, specific hooks ("Rockstar Confirmed This And Nobody Noticed"), not generic hype.

## Cadence

| Window | Posts/day | Focus |
|---|---|---|
| Aug 10-26 | 2-3 | Establish topical authority. Map, Leonida, Jason/Lucia, confirmed-vs-rumour. |
| **Aug 26-31** | **6-8** | Extended Look week. Highest-leverage seven days before launch. Prep templates now. |
| Sep - Oct | 3 | Steady. Feature deep-dives, GTA V nostalgia callbacks, countdown series. |
| Nov 1-18 | 4-5 | Pre-order, reviews embargo, hype peak. |
| **Nov 19-26** | **8-10** | Launch. Speed beats polish. Easter eggs, map comparisons, "where to find X". |

A running day-counter in the overlay is a free series hook and costs nothing to generate.

## Content pillars, ranked by expected Shorts performance

1. **Countdown details** - "5 things in the Extended Look nobody noticed." Lane A's native format.
2. **Comparisons** - Leonida vs real Florida, Vice City then vs now, V vs VI mechanics.
3. **Confirmed vs rumoured** - fact-check framing. Builds the authority that makes launch-week content stick.
4. **Nostalgia** - GTA V / SA / Vice City callbacks. Pulls the older audience that actually buys the game.
5. **Countdown-to-launch** - dumb, cheap, and the most reliable subscriber driver in a hype niche.

Retention mechanics worth keeping from the generic playbooks: open on the payoff inside one second, 30-45s not 60, and cut the last frame to flow into the first so the loop is seamless.

## Risk, honestly

- **The real risk is not a Take-Two DMCA strike.** Rockstar tolerates coverage; it is free marketing. The real risk is **YouTube's reused-content rule at YPP review**. Lane A (original narration, assembled, overlaid) passes. Lane B alone would not. **Never apply for monetisation on a Lane-B-only catalogue.**
- **Leaked footage is the one hard line.** Rockstar aggressively nukes leaked-build videos. Discussing leaks: fine. Hosting the footage: channel-ending. Hence `leaked footage` / `leaked gameplay` / `leaked build` in both negative lists.
- **Saturation is real.** Every automated channel on earth is doing this. The only edge automation gives is *speed and volume on the two spike dates* (Aug 27, Nov 19). If the templates are not locked before Aug 26, the edge is gone.
- **Post-launch cliff.** Peak is Nov-Dec, then the niche compresses hard. Pick a neutral brand and neutral tags now so the December pivot to general gaming costs nothing.

## Ship order

1. Verify all Lane B handles with yt-dlp (the same listing call the pipeline uses).
2. Create the channel with a non-trademarked name. Authenticate both tokens:
   `python -m src.uploader auth --channel gta_hype`, plus the ranking publisher for `gta6_countdown`.
3. Add the Lane A topic to `ranking-shorts-pipeline/config/ranking.yaml`. Run `--mode source --topic gta6_countdown`, then one render with `--no-upload`. Review by hand.
4. Confirm whether topic-level overrides beat `defaults.max_music_confidence`. If not, drop trailer b-roll from the query set.
5. Add the Lane B block to `youtube-shorts-pipeline/config/niches.yaml`. Run `--mode discover --niche gta_hype` and check the skip buckets before processing anything.
6. Keep `UPLOAD_PRIVACY=private` until three consecutive renders pass manual review.
7. Lock templates, fonts and overlays **by Aug 25**. That deadline is the plan.
