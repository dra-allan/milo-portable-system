# Campaign Clipper: deployment gates and acceptance criteria

## What this lane is

A clipping pipeline for paid campaign boards. Input is a folder of **video
files** published by the campaign. Output is a vertical short that satisfies that
campaign's stated requirements, plus its published link submitted back to the
board.

It is not a discovery pipeline. There is no search, no yt-dlp, and no candidate
ranking, because the source pool is handed to you.

## Isolation

The repository holds code and safe config only. Everything else is written under:

```
<VIDEO_FACTORY_ROOT>/campaign-clipper-pipeline/
```

Sources, assets, temp, stage renders, outputs, logs, SQLite state, the browser
profile and OAuth tokens all live there. Campaign content is other people's
copyrighted video and must never enter git history; `.gitignore` is a second line
of defence, not the plan.

The existing Shorts, POV and ranking pipelines are untouched and unimported.

## Deployment checklist

- [ ] Set `VIDEO_FACTORY_ROOT` to the same shared external parent as the other lanes.
- [ ] Copy `config/.env.template` to `config/.env`. Never commit the real file.
- [ ] Set `MILO_FFMPEG` / `MILO_FFPROBE` to the **same** binaries the other lanes use.
- [ ] Set `OVERLAY_FONT` to an installed bold `.ttf`.
- [ ] `pip install -r requirements.txt`
- [ ] `python -m playwright install chromium`
- [ ] Install `gdown` or configure `RCLONE_REMOTE`.
- [ ] `python -m src.main --mode test` passes with no `[FAIL]` lines.
- [ ] `python -m unittest discover -s tests` passes.
- [ ] `python -m src.main --mode login` and sign in by hand.
- [ ] Authenticate the upload channel: `python -c "from src.publisher import auth; print(auth('<channel>'))"`
- [ ] Compile one campaign and **read the YAML**, including `unparsed` and `conflicts`.
- [ ] `--mode build --count 1` and watch the file end to end yourself.
- [ ] Confirm the MP4 landed in the external output dir and the repo is clean.
- [ ] Keep `UPLOAD_PRIVACY=private` until you have approved a render.
- [ ] Keep `CLIPPER_AUTO_UPLOAD=false` and `CLIPPER_AUTO_SUBMIT=false` until a full campaign has passed validation twice.

## Acceptance criteria

1. A campaign requirements block compiles to a spec with zero silent drops: every
   unmatched line appears in `unparsed`.
2. A header/body numeric conflict resolves to the stricter value and records the
   looser one in `conflicts`.
3. A rendered clip is 1080x1920, h264, yuv420p, BT.709 tagged, at the exact
   target duration.
4. A clip shorter than the campaign minimum is blocked, with no rounding
   tolerance.
5. A clip whose text layer produced no visible pixels is blocked, not shipped.
6. A campaign requiring a logo produces a clip in which the logo is detectable,
   or the clip is blocked.
7. Required caption tokens are present on every submitted clip, regardless of
   what the copy model returned.
8. A window already published for a campaign is never published again.
9. Nothing uploads or submits unless the operator opted in explicitly.

## Known limits, stated deliberately

- **Trending platform audio cannot be added by FFmpeg.** Campaigns requiring it
  carry a manual step and warn on every clip. This is not a bug to be fixed; it
  is a property of publishing native audio.
- **Audience geography and engagement floors are account facts**, not render
  facts. The pipeline reports them and refuses campaigns it can prove you fail.
- **"No spam / no low quality" is a human judgement.** It is surfaced for review
  on every clip and never machine-approved.
- **Board scraping is selector-dependent.** All DOM assumptions are in one
  `SELECTORS` dict, and the paste path (`--mode add`) never depends on the DOM.
- **Source copyright is not cleared by this pipeline.** Clipping campaign-provided
  content within that campaign is the licensed use; the same footage published
  outside the campaign is not covered by anything here.
