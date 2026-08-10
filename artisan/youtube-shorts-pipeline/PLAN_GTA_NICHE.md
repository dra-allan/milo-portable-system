# PLAN: GTA 6 Hype Niche (two-lane)

> Written 2026-08-10. Hard dates below are verified against Rockstar/Take-Two, not vibes.
> **Revised 2026-08-10 (later)** after a full read of `discovery.py`, `downloader.py`,
> `ranking-shorts-pipeline/src/config.py` and sampled source-channel metrics.
> See [§ Verified against the code](#verified-against-the-code) for what changed and why.

## The clock (this drives everything)

| Date | Event | Why it matters |
|---|---|---|
| **2026-08-27, 3PM ET** | Rockstar "An Extended Look" (Netflix 3PM ET, YouTube 9PM ET) | Biggest search spike between now and launch. **17 days out.** The channel must be live and posting *before* this, not after. |
| Summer 2026 | Marketing campaign + pre-orders (Take-Two confirmed) | Continuous official-asset drops = continuous free source material. |
| **2026-11-19** | GTA VI launch, PS5/Xbox Series | 101 days out. Reaffirmed on the FY26 earnings call, no delay. |

The real prize is **YPP before launch**. Shorts monetisation needs 1,000 subs + 10M valid Shorts views in a rolling 90 days. Starting today, the 90-day window closes almost exactly on launch week. Start next month and you watch the biggest gaming event ever happen on an unmonetised channel.

Why Aug 27 and not Nov 19 is the real launch date: a trailer drop is a **closed-universe**
content event. For ~72 hours millions of people search the same finite set of questions
("what did you miss", "map details", "is X confirmed") and there is a hard ceiling on how
much footage exists to analyse. That is the easiest window a new channel will ever get.
November is the opposite: infinite content, every gaming channel on the platform
competing, and no reason for the algorithm to trust a two-week-old channel.

## Positioning

Faceless **GTA 6 countdown / news hub**. Narrated, not gameplay. The operator does not play GTA and does not need to.

**Do not put "GTA" or "GTA 6" in the channel name or handle.** Trademark exposure on the brand itself, and it boxes you in post-launch. Pick a neutral brand (Leonida-flavoured, Vice-flavoured, whatever) so the channel survives a Rockstar mood swing and can pivot to general gaming in December.

On "pick a lane" advice from the generic playbooks: it does not apply here. You are not on
camera and not playing, so "funny guy vs lore nerd" is a false choice. The lane an
automated pipeline can actually own is **speed and completeness** -- a clip of every
confirmed detail within hours of it existing. Personality channels cannot match that, and
it is the only edge automation actually confers.

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

**Friction with the shipped `defaults:` block -- now resolved:**

- **Topic-level overrides of `defaults` are NOT supported.** Confirmed in
  `ranking-shorts-pipeline/src/config.py`: `RankingConfig.topic()` returns the raw topic
  dict with five `setdefault` calls and never merges `self.defaults`, and `config.get()`
  reads `self.defaults` only. So `max_music_confidence`, `max_text_coverage` and
  `candidates_per_topic` are **global**. Changing any of them for GTA also changes it for
  `fishing_moments` and `animal_moments`.
- **Therefore: do not touch the defaults.** `max_music_confidence: 0.55` does reject
  wall-to-wall-score footage, which means **official Rockstar trailer b-roll is off the
  table** -- but the query set above never asks for it. Breakdown and analysis footage has
  a person talking over it and clears 0.55 fine. Design around the constraint instead of
  globally loosening a vetting rule for two unrelated topics.
- `max_text_coverage: 0.18` + `blur_detected_text: true` still bins a lot of GTA news
  footage (on-screen text, channel watermarks). Since `candidates_per_topic: 40` is also
  global, raise it only if the vet stage actually starves the render -- and accept the
  extra sourcing cost lands on all topics.
- `max_source_duration: 900` is fine; GTA breakdowns run 8-15 min.

### Lane B - fast news beats (`youtube-shorts-pipeline`)

Straight clipper on a tight, verified channel list. Talk-heavy analysis transcribes cleanly, same as the podcast niches.

**Requires the `require_keywords` gate added to `discovery.py` on 2026-08-10.** Without it
this block is unsafe -- see [§ Verified against the code](#verified-against-the-code), item 1.

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
  # Group 1 - GTA-dedicated. Safe with or without the require_keywords gate.
  channels:
    - "@MrBossFTW"        # daily GTA 6 news, desk cam, 4-10 min, real captions
    - "@LegacyKillaHD"    # GTA 6 news + analysis, 10-20 min
    - "@GTASeriesVideos"
    - "@DarkViperAU"      # news + reaction VODs, best talk density on this list
    - "@SernandoE"
  # Group 2 - broad gaming channels. These ONLY belong here because
  # require_keywords gates them. Remove them if that key is removed.
    - "@gameranxTV"       # NOT "@gameranx" -- verify before enabling
    - "@DigitalFoundry"
    - "@IGN"
    - "@GameSpot"
    - "@Nought"
    - "@TheGamer"
  # @RockstarGames is deliberately omitted: trailers run 60-180s and would be
  # rejected by min_duration on every sweep. Add it only if min_duration drops.
  #
  # THE TOPIC GATE. discovery.py filters titles against this; `keywords` below
  # does NOT filter and never has (it only scores highlights in processor.py).
  # Deleting this key while Group 2 channels are listed will publish Nintendo
  # clips to a GTA channel.
  require_keywords:
    - "gta"
    - "grand theft auto"
    - "rockstar"
    - "leonida"
    - "vice city"
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
  # Same reason "news", "breaking news" and "gameplay" are absent: after
  # Aug 27, "GTA 6 Gameplay Analysis" is the highest-value title pattern that
  # exists. Negatives are for FORMATS, never for topics.
  #
  # "reaction" and "livestream" are also absent, against the house style.
  # DarkViperAU's GTA news reaction VODs run 90k-280k views with excellent
  # talk density; blocking those two words removes the best source on the
  # list. max_duration does the bounding instead.
  negative_keywords:
    - "#shorts"
    - "shorts"
    - "clip"
    - "clips"
    - "compilation"
    - "music video"
    - "official audio"
    - "lyrics"
    - "fan made"
    - "fan edit"
    - "concept trailer"
    - "leaked footage"
    - "leaked gameplay"
    - "leaked build"
    - "mod showcase"
    - "gta 5 gameplay"
    - "roleplay"
    - "rp server"
    - "no commentary"
    - "full playthrough"
    - "giveaway"
    - "modded account"
  language: en
  # 180, not 240. Sampled Aug 2026: MrBossFTW's fast-turnaround news videos
  # run 3:53 (233s). A 240 floor rejects the densest single-topic sources on
  # the list, and he is source #1.
  min_duration: 180
  # 4h ceiling admits reaction VODs (dense talk tracks) while excluding
  # marathon streams whose transcription cost exceeds their clip yield.
  max_duration: 14400
  min_score: 0.48
  # 45, not 365. Stale GTA rumours get debunked within weeks. Posting a
  # six-month-old leak as news is how a news channel loses its audience.
  # NOTE: this key is documentation only -- discovery.py never reads it.
  # Recency is enforced structurally instead: /videos is newest-first and
  # playlistend truncates to `lookback`, so keep lookback at 3-5 and only
  # source channels that post GTA content near-daily.
  preferred_upload_days: 45
  # 8000, not 15000. min_views is a RECENCY TAX: a high gate means we only
  # ever see source videos after they have aged one to three days, which is
  # exactly backwards for a niche whose entire edge is being early. Sampled
  # Aug 2026: MrBossFTW's daily GTA 6 videos land at 13k-42k, so even 15000
  # intermittently deletes the whole daily-news tier.
  min_views: 8000
```

<a name="verified-against-the-code"></a>
## Verified against the code (2026-08-10 revision)

Five findings from reading `discovery.py`, `downloader.py` and the ranking config loader
end to end. Items 1-3 changed this plan.

### 1. `discovery.py` had no positive title gate -- **this broke Lane B as originally written**

`discover_candidates()` filtered on: already-processed, duration, `negative_keywords`,
`min_views`. That is the entire list. **`keywords` was never read by discovery** -- it is
consumed only by `processor.py`, to score highlight windows *inside a video that has
already been accepted*.

The original Lane B block listed @IGN, @GameSpot, @gameranx and @DigitalFoundry and relied
on `keywords` to keep them on-topic. Nothing did. Every non-GTA upload from those channels
was a valid candidate, and the daily cap means a single bad pick burns the entire day's
output.

**Fixed in code**, not in config. `discovery.py` now supports an opt-in `require_keywords`
list: when present, a title must match at least one entry or it lands in a new
`skipped_off_topic` bucket. Absent key means the filter never runs, so all 25 existing
niches are unchanged.

### 2. Thresholds were set from house style, not from this niche's actual supply

Sampled Aug 2026 metrics for the listed sources:

| Source | Typical views | Typical length |
|---|---|---|
| MrBossFTW (daily news) | 13k - 42k | 3:53 - 9:40 |
| LegacyKillaHD | ~100k | ~16 min |
| DarkViperAU (news + VOD) | 93k - 276k | 5:34 - 24:05 |

Against that, `min_views: 15000` and `min_duration: 240` both cut the daily-news tier --
the exact tier that supplies the freshness edge. Lowered to **8000** and **180**.

### 3. `preferred_upload_days` does nothing

Not referenced in `discovery.py` or `downloader.py`. Recency is only enforced implicitly,
because `/videos` is newest-first and `playlistend` truncates to `lookback`. Harmless for
evergreen niches; for news it means a stale rumour can be clipped as fact. Mitigate with a
small `lookback` and near-daily sources, not with the config key.

### 4. The one-source-per-24h cap is survivable, except on Aug 27

`_niche_processed_today()` hard-stops discovery if anything was processed for the niche in
24h. That still yields `max_videos` clips from one source, so 4 shorts/day -- a fine news
cadence. It is only fatal on surge days, when you want the trailer plus three breakdowns
plus two reaction VODs in one night. **`reset_caps.py` is the lever.** Run it between
sweeps on Aug 27 and Nov 19.

### 5. Source choice is a speed decision, not just a quality one

Highlight scoring reads the transcript, and `_audio_opts()` fetches YouTube's own captions
when `use_youtube_subs` is on -- which skips the Whisper pass, ~85% of total runtime per
`PIPELINE_PERFORMANCE_REPORT.md`. Talking-head channels ship real captions; gameplay
channels with music and engine noise under the commentary do not, and degrade Whisper on
top of that. Every Group 1 handle above was picked on that basis.

## Repo-specific gotchas the generic advice misses

1. **Keyword score is capped at ~6 hits** (see the `ranking_general_commentary` header note in `niches.yaml`). `PLAN_MINECRAFT_NICHE.md` and `PLAN_ROBLOX_NICHE.md` list 200-300 keywords each; junk hits saturate the cap and crowd out real signal. The list above is deliberately short and discriminative. Do not "improve" it by adding more.
2. **`_source_rank` self-prunes.** Sources recorded under 200 avg views get deprioritised automatically. Front-load 12-15 handles and let the ranker sort them instead of hand-curating.
3. **Negative keywords match title only, not description.** Anything hiding in the description gets through. Vet the first ten renders by hand.
4. **The dead-channel cache holds for 14 days.** A handle that fails listing is skipped silently for two weeks. Verify handles up front; do not discover this on Aug 27.
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

Around Nov 1, rotate `keywords` toward launch intent ("how to", "where to find", "best
early", "first mission"). Post-launch demand shifts from *news* to *guides* and the scorer
has to follow it or it will keep surfacing speculation nobody needs any more.

## Content pillars, ranked by expected Shorts performance

1. **Countdown details** - "5 things in the Extended Look nobody noticed." Lane A's native format.
2. **Comparisons** - Leonida vs real Florida, Vice City then vs now, V vs VI mechanics.
3. **Confirmed vs rumoured** - fact-check framing. Builds the authority that makes launch-week content stick.
4. **Nostalgia** - GTA V / SA / Vice City callbacks. Pulls the older audience that actually buys the game.
5. **Countdown-to-launch** - dumb, cheap, and the most reliable subscriber driver in a hype niche.

Retention mechanics worth keeping from the generic playbooks: open on the payoff inside one second, 30-45s not 60, and cut the last frame to flow into the first so the loop is seamless.

## Risk, honestly

- **The real risk is not a Take-Two DMCA strike.** Rockstar tolerates coverage; it is free
  marketing, and Content ID claims on GTA footage overwhelmingly originate from the
  licensed radio music inside the game, not from Rockstar or from the creator you clipped.
  The real risk is **YouTube's reused-content rule at YPP review**.
- **What the policy actually says.** Reused content -- clips, commentary, compilations,
  reactions -- *is* monetisable with significant original commentary, modification or
  educational/entertainment value; unchanged through the July 2025 and July 2026 updates.
  The separate **generic-or-repetitive** rule is the one that bites: templated output where
  the body barely changes upload to upload is ineligible. Same intro/outro is fine; same
  everything is not.
  **So the thing that demonetises a faceless auto-clipper is looking mass-produced, which
  is exactly the failure mode of one template over one source type at four uploads a day.**
  Lane A (original narration, assembled, overlaid) passes. Lane B alone would not.
  **Never apply for monetisation on a Lane-B-only catalogue.** Rotate caption styles, hook
  cards and crop framing so ten consecutive uploads do not read as ten runs of one macro.
- **Leaked footage is the one hard line.** Rockstar aggressively nukes leaked-build videos. Discussing leaks: fine. Hosting the footage: channel-ending. Hence `leaked footage` / `leaked gameplay` / `leaked build` in both negative lists.
- **Saturation is real.** Every automated channel on earth is doing this. The only edge automation gives is *speed and volume on the two spike dates* (Aug 27, Nov 19). If the templates are not locked before Aug 26, the edge is gone.
- **Post-launch cliff.** Peak is Nov-Dec, then the niche compresses hard. Pick a neutral brand and neutral tags now so the December pivot to general gaming costs nothing.

## Ship order

1. Verify all Lane B handles with yt-dlp (the same listing call the pipeline uses). `@gameranxTV`, not `@gameranx`. Do this first -- the dead-channel cache holds failures for 14 days.
2. Create the channel with a non-trademarked name. Authenticate both tokens:
   `python -m src.uploader auth --channel gta_hype`, plus the ranking publisher for `gta6_countdown`.
3. Add the Lane A topic to `ranking-shorts-pipeline/config/ranking.yaml`. Run `--mode source --topic gta6_countdown`, then one render with `--no-upload`. Review by hand. Do **not** edit `defaults:` -- it is global across all topics (see item above).
4. Add the Lane B block to `youtube-shorts-pipeline/config/niches.yaml`. Run
   `--mode discover --niche gta_hype` and read the skip buckets before processing anything.
   Empty candidates + full `skipped_off_topic` means `require_keywords` is too tight.
   Empty candidates + full `skipped_min_views` means the floor is still too high.
5. Keep `UPLOAD_PRIVACY=private` until three consecutive renders pass manual review.
6. Lock templates, fonts and overlays **by Aug 25**. That deadline is the plan.
