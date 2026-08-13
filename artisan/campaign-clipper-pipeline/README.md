# Campaign Clipper Pipeline

Takes a campaign's **video files**, cuts compliant vertical clips, burns your own
text, stamps the required logo, checks the result against that campaign's
requirements, uploads to your eligible account, and submits the published link
back to the board.

No video editor. FFmpeg, Python, Pillow, and OpenCV for one specific check.

This is a third lane beside `youtube-shorts-pipeline` and
`ranking-shorts-pipeline`. It reuses their conventions (runtime isolation, the
shared FFmpeg binary, Pillow-into-`movie=` text, SQLite state, the shared upload
token) and shares none of their code paths, so nothing here can break them.

---

## The one idea that makes this work

Campaign requirements are prose and every campaign words them differently. The
pipeline **never reads that prose at render time.** Each campaign is compiled
once into a validated spec (`config/campaigns/<id>.yaml`), you read the YAML, and
from then on the renderer, the caption writer and the validator only ever look at
structured fields.

Adding a campaign is data entry, not code. Adding your twentieth campaign costs
the same as your second.

---

## Install

```
cd artisan/campaign-clipper-pipeline
python -m pip install -r requirements.txt
python -m playwright install chromium
copy config\.env.template config\.env
```

Edit `config/.env`. The three that actually matter:

| Variable | Why |
|---|---|
| `VIDEO_FACTORY_ROOT` | Same external parent the other lanes use. Everything runtime lives there, nothing in the checkout. |
| `MILO_FFMPEG` / `MILO_FFPROBE` | Same binary as the other lanes. A different build gives different output for identical config. |
| `OVERLAY_FONT` | An installed bold `.ttf`. Rendering cannot start without one. |

Then:

```
python -m src.main --mode test
```

That separates hard failures (no FFmpeg, no font, no Pillow) from soft ones (no
OpenCV, no Playwright, no model key). Soft failures degrade features; they do not
stop you.

---

## First run, once per machine

```
python -m src.main --mode login
```

A browser opens. Sign in by hand. The session is saved to a persistent profile
under the runtime root and reused forever. **The pipeline never sees a
password**, which is deliberate: automating a login form is both the most
brittle and the most account-risky thing it could do.

Then authenticate the upload channel once:

```
python -c "from src.publisher import auth; print(auth('capitalmindsetshorts'))"
```

---

## Operating it: the loop

### 1. Find a campaign

```
python -m src.main --mode campaigns --platform youtube
```

### 2. Compile its requirements into a spec

From the board:

```
python -m src.main --mode pull --url <campaign-url> --id castle_clipping
```

Or, when the scrape fails or you just have the text (**this path always works**):

```
notepad castle.txt          # paste the Requirements block, save
python -m src.main --mode add --id castle_clipping --file castle.txt
```

Either way you get `config/campaigns/castle_clipping.yaml` plus a printed report:

```
[CONFLICT] min_duration: kept 10 (stricter), saw 8
[MANUAL]   Add trending audio in the platform composer at publish time.
[UNPARSED] SOME LINE THE PARSER DID NOT RECOGNISE
[BLOCK]    logo required but no logo folder configured
```

**Read the YAML before you build.** The compiler is good, not psychic. Anything
it could not classify is listed under `unparsed` rather than dropped, and that
list is the whole point of this step.

### 3. Pull the content folder

```
python -m src.main --mode sources --id castle_clipping --refresh
```

Uses `rclone` when `RCLONE_REMOTE` is set, `gdown` otherwise. If the campaign's
content lives in a Discord (Duel does), download it by hand once and point
`sources.local_folders` at the folder. That path is fully supported, not a
workaround.

### 4. Build

```
python -m src.main --mode build --id castle_clipping --count 3
```

Sources, picks scene-aligned windows it has never published before, writes copy,
renders, validates. **Publishes nothing.** Output:

```
[PASS] clip 12  castle_clipping_vert2_fix_184s.mp4
       text   : HE ACTUALLY DID THAT
       caption: wild moment from the castle stream
       warn   : keep this video live for at least 30 days
[FAIL] clip 13  castle_clipping_fla2_92s.mp4
       BLOCK  : duration 9.40s below campaign minimum 10s
```

Watch the passing files yourself. Then:

### 5. Publish and submit

```
python -m src.main --mode upload --id castle_clipping --clip 12
python -m src.main --mode submit --id castle_clipping --clip 12
```

Use `--fill-only` on submit while you still want to press the button yourself:
it fills the link field and waits.

### Or the whole thing

```
python -m src.main --mode run --id castle_clipping --count 3
```

With default config this builds, validates, and **stops**, printing the exact
commands to publish. Auto-upload and auto-submit are separate opt-ins in
`.env`, because they are separate risks: a bad upload is deletable, a bad
submission is not.

Or just use the menu: `run_campaign_clipper.bat`.

---

## What is checked, and what is not

The validator measures the finished file. It does not trust the renderer.

**Blocks the submission**

- duration below the campaign minimum, with **no tolerance** (9.97s fails a 10s
  campaign, because it does)
- output not portrait
- required own-text missing, or a text layer that rendered zero visible pixels
- a phrase the campaign requires *in the video* missing from the burned text
- required logo not detectable in the output, including "the logo stage ran but
  composited off-frame"
- a required caption keyword, hashtag or mention missing
- forbidden topic words in your copy
- a campaign that does not run on YouTube

**Reported, never silently passed**

- audience geography (a fact about your account)
- engagement rate and view thresholds (post-publish metrics)
- the spoken language of the source audio
- "no spam / no low quality", which is a human judgement

**Cannot be done by this pipeline at all**

- trending audio added inside the platform composer at publish time. FFmpeg
  cannot do it. Campaigns that require it carry a `manual_steps` entry and the
  validator warns on every clip.

---

## The five example campaigns

| Campaign | Automatable? | The catch |
|---|---|---|
| Castle | Yes, end to end | Header says 8s, text says 10s. Spec keeps 10s and records the conflict. Logo is `if-absent`. |
| Roobet | Yes, end to end | Cleanest of the five. Real content folder, real logo, one required hashtag. |
| Duel | Build yes, sourcing no | Content is behind a Discord invite. Drop the files, everything else runs. |
| Kingdom Clash | Partly | Needs trending audio at publish (manual) and a 40% US audience (your account, not the file). |
| GamePoint Bingo | Partly | Assets live in a Notion brief, and the official account handle is written nowhere. The spec says so instead of guessing a handle. |

Guessing that handle would be worse than admitting it is unknown: tagging the
wrong account is its own violation.

---

## Design decisions you will want the reasoning for

**Text goes through Pillow, never `drawtext`.** Campaign copy contains
apostrophes, colons, commas and percent signs. `drawtext` has no escaping recipe
that survives all of them together; a percent sign logs "Stray %", draws nothing,
and still exits zero. You would ship a text-less clip to a campaign that pays for
the text. A PNG has no syntax to break out of.

**Clips cut on scene boundaries.** A fixed grid lands mid-sentence and
mid-action, which is exactly the low-effort output these campaigns reject by
name. The scene timestamps already exist, so snapping to them is free.

**Fit into 9:16 over a blurred bed, never centre-crop.** These are gameplay and
stream clips where the subject is often near an edge or in a HUD corner. Cropping
throws away what the campaign is paying for, and several campaigns require
gameplay to stay clearly visible.

**Published windows are remembered per campaign.** The content folders are tiny
and shared with every other clipper on the campaign. Posting the same twenty
seconds twice is the fastest route to a spam rejection.

**The encoder is confirmed with a one-frame encode.** A machine can list
`h264_nvenc` and `h264_qsv` while neither runs. Trusting `-encoders` fails every
clip in the run with "Unknown encoder".

**Local files survive upload.** A clip is finished when the *link* is accepted,
not when the upload completes. Deleting on upload leaves nothing to retry with.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No overlay font found` | Set `OVERLAY_FONT` to a real `.ttf`. |
| `ffmpeg not found` | Set `MILO_FFMPEG` to the shared binary. |
| `REQUIREMENT_ROWS_EMPTY` | Board was restyled. Fix `SELECTORS` in `src/clipster.py`, or use `--mode add`. |
| `logo presence (OpenCV not available)` | `pip install opencv-python-headless`. Until then logo checks are unverifiable. |
| `FETCH_NO_BACKEND` | Install `gdown` or set `RCLONE_REMOTE`. |
| Every clip fails on duration | `TARGET_DURATION` is below the campaign minimum. It gets clamped up, but check the spec's `min_duration` is right. |
| Nothing built, `NO_FRESH_WINDOWS` | You have already published every window of every source. Add sources or lower `TARGET_DURATION`. |
| Copy is always the same fallback hook | No `GEMINI_API_KEY`. Compliance is unaffected; only the creative half is. |

Run the tests any time:

```
python -m unittest discover -s tests -v
```

---

## Layout

```
config/
  clipper.yaml            style + machine defaults ONLY
  .env.template
  campaigns/<id>.yaml     one validated spec per campaign
src/
  spec.py                 the schema everything compiles into
  compiler.py             prose -> spec (rules first, model second)
  sources.py              content folder -> local files
  segmenter.py            which seconds to ship
  overlay.py              text sheets, logo stamping, logo detection
  renderer.py             the FFmpeg graph
  captions.py             copy, with requirements enforced in code
  validator.py            the pre-submit gate
  publisher.py            YouTube upload
  clipster.py             board browse / read / submit
  main.py                 CLI
tests/
run_campaign_clipper.bat
```
