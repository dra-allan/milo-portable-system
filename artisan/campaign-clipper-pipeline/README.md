# Campaign Clipper Pipeline

Takes campaign **video files**, cuts them into real 9:16 Shorts, burns campaign
captions, stamps required logos, validates the result, uploads it, and submits
the published link back to the board. No video editor.

## Framing and captions

There is no blurred background. The render graph is:

```
source video -> scene window -> smart full crop -> 1080x1920 -> captions -> logo -> encode
```

`src/smart_crop.py` samples the actual clip window with OpenCV. A streamer gets
a tighter face-biased crop with headroom. A two-person clip uses the midpoint and\
a looser crop so both people stay visible. The crop is calculated in source
pixels, clamped to the source, rounded for `yuv420p`, and then scaled to the
Shorts frame. If no stable face is detected, it falls back to a normal centre
crop. Smart framing can be toggled with `CLIPPER_SMART_CROP=false`.

Captions are generated from the campaign spec, rendered by Pillow into a
transparent PNG, and composited **after** the crop. That means the caption is
placed on the final Shorts frame and cannot be cropped away. Required phrases
are forced into the overlay text, while required hashtags and mentions are
forced into the post caption.

## Operating loop

```
python -m src.main --mode test
python -m src.main --mode login
python -m src.main --mode add --id castle_clipping --file castle.txt
python -m src.main --mode build --id castle_clipping --count 3
python -m src.main --mode upload --id castle_clipping --clip <id>
python -m src.main --mode submit --id castle_clipping --clip <id>
python -m src.main --mode links
python -m src.main --mode record-link --id castle_clipping --clip <id> --url <youtube_url>
```

`build` publishes nothing. Watch the passing MP4s first. Use `--fill-only` on
submit if you want the browser to fill the Clipster field while you press the
button yourself.

## Titles, niches and channel routing

Each clip gets a rule-based optimized title (vendored from the Shorts lane's
`title_optimizer`): filler is stripped, non-English hooks are rejected, and a
curiosity frame keyed on the campaign niche is applied. The title is shown in
`build` output and stored with the clip.

A campaign's niche is detected from its requirements at compile time and written
into the spec; a manual `niche:` in the YAML always wins. The upload target
resolves as: spec `upload_channel:` -> `config/channels.yaml` by niche ->
`CLIPPER_UPLOAD_CHANNEL`. `--mode links` prints and writes `data/clip_links.csv`
with every published link (campaign, niche, account, title, url) for campaign
submission and tracking. `--mode record-link` attaches a URL to a clip that was
uploaded by hand, so manually posted clips stay in the same ledger.

## What the campaign spec controls

Each campaign YAML tells the pipeline where the source videos live, which logo
folder to download, whether the logo is conditional, minimum duration, required
caption tokens, phrases that must appear inside the video, platform, language,
and manual publish steps such as native trending audio.

The pipeline never guesses a campaign handle or silently ignores an unparsed
requirement. Read the YAML before building.
