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

- `rankedup` -> normal ranking topics
- `other_guys` -> only topics explicitly listed in `OTHER_GUYS_TOPICS`

A topic must also set `contrast_mode: true` before it can use the `OTHERS VS
THIS GUY` copy. All other topics stay normal, so a lightning or unrelated topic
cannot inherit contrast labels. Ranking output is keyed with the channel in the
plan metadata and never uses the Shorts NXS route.
