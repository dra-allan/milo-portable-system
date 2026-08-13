# Channel routing contract

## Shorts line

- `capital_mindset` -> Capital Mindset
- `flick_shorts` -> Flickshots
- `chop_ug` -> ChopUG
- `nxs` -> NXS, with the legacy `gta_hype` niche treated as an alias only
- `wealth_mindset` -> Wealth Mindset

The full sweep chops one fresh source per authenticated lane first. Posting is
run afterward and is the only step that enforces daily and per-run caps. When a
cap is exhausted, rendered clips remain in the lane's backlog.

## Ranking line

- `RankDrop` -> normal ranking topics (TOP-N countdowns)
- `the other guys` -> contrast `OTHERS VS THIS GUY` clips

Routing is variant-driven: a normal build publishes to RankDrop, a contrast
build publishes to The Other Guys. A topic must also set `contrast_mode: true`
before it can use the `OTHERS VS THIS GUY` copy in the mixed sweep. Ranking
output is keyed with the channel in the plan metadata and never uses the
Shorts NXS route. Configure lanes with `RANKING_CHANNEL_PROFILES`, for example
`RankDrop:normal,the other guys:contrast`.
