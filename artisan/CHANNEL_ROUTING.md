# Channel routing contract

`artisan/yt-secrets/channels.yaml` is the source of truth. The token filename is
always `youtube_token_<channel-key>.json`; use the exact registry key, never a
display name. `RankDrop` is not a key, `rankdrop` is.

## What a channel declares

| Field | Meaning |
|---|---|
| `pipelines` | The only lanes allowed to publish with this token: `shorts`, `clipper`, `ranking`, `pov`. Any other lane is refused. |
| `token_dir` | Where the token is written. Must match the lane, or the token lands where the publisher never looks. |
| `niches` | Allow-list of `niches.yaml` keys that may publish here. A niche outside the list is refused. |
| `variant` | Ranking lane only: `normal` (ranked countdowns) or `contrast` (OTHERS VS THIS GUY). Two channels cannot own the same variant. |
| `content` | Plain English. It shows up in the error when something is mis-routed. |
| `channel_id` | The YouTube channel this key means. Written automatically by auth/sync. |
| `chrome_profile` | The Chrome profile signed into `email`, so consent opens on the right account. |

Two separate guards enforce this at publish time:

- **Identity** (`MILO_CHANNEL_IDENTITY`, default `learn`) - is this the right
  channel? Compares the live token's channel against `channel_id`.
- **Content** (`MILO_CHANNEL_CONTENT`, default `enforce`) - is this the right
  content for that channel? Compares lane, variant and niche against the
  declarations above. Only declared facts are checked, so an incompletely
  described channel is never blocked; use `warn` for a deliberate cross-post.

## Current routing

| Channel | Lane | Posts | Fed by |
|---|---|---|---|
| flick_shorts | shorts + clipper | Life lessons / hard truths from podcasts | niche `flick_shorts` |
| capital_mindset | shorts + clipper | Business, sales, founders | niche `capital_mindset` |
| wealth_mindset | shorts + clipper | Wealth building, money mindset | niche `wealth_mindset` |
| NXS | shorts + clipper | GTA 6 news only (topic-gated) | niche `gta_hype` |
| chop_ug | shorts | Luganda Ugandan gossip, captions off | niche `chop_ug` |
| god_did_fx | shorts + clipper | Forex education | niche `forex_god_fx` |
| rankdrop | ranking | Top-N countdowns (`normal`) | ranking lane |
| the_other_guys | ranking | OTHERS VS THIS GUY (`contrast`) | ranking lane |
| explaination | pov | POV explainers | POV lane |
| dra_allan_official | pov | Personal POV | POV lane |
| money_matrix | pov | Money POV | POV lane |

## Authenticate or inspect

```text
reauth_all_channels.bat                     every channel, one at a time
reauth_all_channels.bat --channel NXS       one channel
reauth_all_channels.bat --pipeline ranking  one lane
reauth_all_channels.bat --doctor            audit routing, offline
reauth_all_channels.bat --sync              fill channel_id from existing tokens
reauth_all_channels.bat --add               register a NEW channel on a lane
```

The underlying CLI is `cd artisan && python -m yt_secrets ...` (`auth`, `status`,
`list`, `doctor`, `sync`, `add`, `bind`). See `AUTH_RUNBOOK.md`.

## Pipeline targets

- **Shorts / clipper**: the niche's `upload_channels` in
  `youtube-shorts-pipeline/config/niches.yaml` names the registry key. The
  uploader no longer falls back to the shared default token when a per-channel
  token is missing, and refuses to upload at all with no channel key - both used
  to publish to whatever channel the default token happened to own.
- **Ranking**: routing comes from the `variant` declarations, not from a
  hardcoded table. `RANKING_UPLOAD_CHANNEL` / `RANKING_CHANNEL_PROFILES` still
  work and are canonicalised to registry keys.
- **Clipper**: `upload_channel` in `config/castle_clipping.yaml`.
  `capital_mindset` is the current destination.

Every lane logs the requested key and the resolved YouTube channel id on every
upload, so a wrong-channel incident is findable in the log rather than only on
YouTube.

## Adding a channel

```text
reauth_all_channels.bat --add
```

Asks for the key, owning email, project slug, lanes and Chrome profile, derives
`token_dir`, appends the block, authenticates it and records its `channel_id`.
Then fill in `content:` and `niches:` (and `variant:` for ranking) by hand, and
add the channel to a niche's `upload_channels` so it actually receives work.
`--doctor` will keep flagging it until both ends are connected.

## Removed 2026-08-23 (Allan's call)

The **`ranking_general_commentary`** niche was deleted from `niches.yaml`: its
`upload_channels` named a channel key that never existed in `channels.yaml`, so
it could build clips and then publish nowhere. The countdown-aware scoring it
motivated (`ranking_mode`) is generic processor logic and stays, covered by
`tests/test_ranking_niche.py`.

Same day, **moviegasm** went `active: false` on the shorts/clipper lanes: it
had no niche feeding it. It remains a campaign target through the campaign
clipper's own config, which this registry does not govern.

Still true: about twenty research niches (`future_tech_daily`,
`peak_human_lab`, `psychology_behavior`, ...) declare no `upload_channels`.
They can discover and build, and a publish attempt is refused instead of
falling through to the default token.
