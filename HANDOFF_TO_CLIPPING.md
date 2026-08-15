# SESSION HANDOFF — campaign clipper: run Castle clips (2026-08-15)

## The task (what "start from the clipping" means)

Run the campaign clipper pipeline against the **castle_clipping** campaign: pull
sources, plan clips, render, validate, then upload to **capital_mindset** and
submit the links back to the board.

## Where we are

The AUTH RABBIT HOLE IS DONE — do not go back into it. All 12 channel tokens are
minted and verified on this PC (2026-08-15). The blocker that started this whole
session (revoked capital_mindset / flick_shorts tokens) is cleared. capital_mindset
token is live and refresh-verified at:
`artisan/youtube-shorts-pipeline/config/youtube_token_capital_mindset.json`.

Everything about the auth overhaul is committed and pushed on `main`:
- PR #15 merged (`d493b52`): one-command foreground auth CLI
  `python -m yt_secrets auth` / `python -m yt_secrets status`.
- Runbook for other machines: `AUTH_RUNBOOK.md` (committed `f9b2518`).
- `AUTH_RUNBOOK.md` + `YT_AUTH_HANDOFF.md` both in repo root.

## The clipper state

Path: `artisan/campaign-clipper-pipeline` (branch `main`, clean tree).

- **Campaign spec** `config/campaigns/castle_clipping.yaml` — the reference
  example. Upload channel **capital_mindset** (explicit, always wins). Finance
  niche, $3000/1M, budget $5000. min_duration 10s (stricter wins over the "8
  secs" board header — recorded in `conflicts`), max 60s, shorts_only, platforms
  [youtube, tiktok, instagram]. Logo required from Drive folder, `logo_mode:
  if-absent` (only stamp if not already branded), top-right, scale 0.14.
  eligible_accounts: [capitalmindsetshorts].
- **channels.yaml** (clipper's own) maps niche→channel: finance=capital_mindset,
  gambling/gaming/sports=chop_ug, podcast/entertainment=flick_shorts,
  crypto/lifestyle=wealth_mindset, tech/news/music=NXS. STALE COMMENT in that
  file says wealth_mindset/NXS tokens are missing on this box — they are NOT
  missing anymore, all 12 are authenticated here. Consider updating that comment.
- **config/.env** (gitignored) sets `POV_SECRETS_DIR` =
  `C:\Users\Administrator\milo-workspace\milo-portable-system\artisan\youtube-shorts-pipeline\config`
  so the clipper's publisher finds the shared tokens.
- **Publisher** (`src/publisher.py`) resolves credentials with a sibling-fallback
  (`shared/credentials.json` → parent → config), so uploads work.

## Verified good

- 78 tests pass (clipper test suite; campaign-clipper-pipeline branch had the
  Whisper + YOLO upgrade where literal `\n` escapes were fixed and all 72→78
  tests pass, `--mode test` healthy).
- flick_shorts was minted via the original flow and verified; all 12 tokens
  refresh OK (final `yt_secrets status` run on 2026-08-15).

## Commands that matter (from `artisan/campaign-clipper-pipeline`)

```
python -m src.main --mode test            # environment health check
python -m src.main --mode specs           # list campaign specs + BLOCKED flags
python -m src.main --mode sources --campaign castle_clipping   # pull Drive sources + logo
python -m src.main --mode build --campaign castle_clipping [--count 3]   # source→plan→render→validate
python -m src.main --mode upload --campaign castle_clipping    # upload validated clips (opt-in)
python -m src.main --mode submit --campaign castle_clipping    # submit links to board (opt-in, beware daily slots)
```

Note the safety model in `src/main.py`: default `--mode run` builds + validates
+ prints what it WOULD publish, stopping before upload. Upload and submit are
separate opt-in stages. Do not skip stages.

## Gotchas / things to respect

- **Board daily slots are the only non-rebuildable asset** — a bad submission
  spends a slot and risks the linked account. Run `build` first, eyeball output,
  then `upload`, then `submit`.
- Campaign `enabled: true`, sources come from a Google Drive folder (gdown) +
  logo folder. `manual_only: false`. If Drive download is flaky, `refresh` flag
  exists; the manual-paste path works without Playwright.
- `--mode test` warns if `GEMINI_API_KEY` unset (copy falls back to templates)
  and if playwright missing (manual paste still works).
- Do NOT re-enter the auth rabbit hole. If a status check is needed it's
  `python -m yt_secrets status` from `artisan/`.
- Everything committed/pushed before this session ended; working tree clean.
  Any new fix → commit + push immediately (Allan's rule: one fix = one commit =
  one push).

## Next concrete steps

1. `python -m src.main --mode test` (confirm ffmpeg/font/Pillow/encoder + spec loads).
2. `python -m src.main --mode sources --campaign castle_clipping` (pull Drive folder + logo).
3. `python -m src.main --mode build --campaign castle_clipping` → review the clip list.
4. If output looks good → `--mode upload` → verify the capital_mindset uploads landed.
5. → `--mode submit` back to the board.