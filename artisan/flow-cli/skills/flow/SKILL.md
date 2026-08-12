---
name: milo-flow
description: Use Milo Flow CLI with Google Labs Flow and Omni through the authenticated browser session. Covers text-to-video, reference media, uploads, job tracking, and MP4 downloads.
---

# Milo Flow CLI

Copyright © 2026 Daada Allan.

Milo Flow CLI uses the Milo Flow CLI Bridge and a Chrome session already signed in to Google Labs Flow. It does not store OAuth credentials or cookies.

## Requirements

1. Chrome has the Milo Flow CLI Bridge installed and connected.
2. The user is signed in to Google Labs Flow in Chrome.
3. A Flow project page is open, or a project has been selected with `flow project-use`.
4. Avoid concurrent use of the same Flow account because Google may trigger reCAPTCHA risk controls.

## Commands

| Command | Purpose | Writes or spends credits? |
|---|---|---|
| `flow credits` | Check balance and account tier | No |
| `flow models` | List models, durations, modes, and prices | No |
| `flow project-list/current/use` | Manage projects | `use` writes local state |
| `flow media-upload` | Upload and cache reference media | Yes |
| `flow media-list` | List cached project media | No |
| `flow gen` | Generate a video | Yes, spends credits |
| `flow job-status/wait/list` | Inspect or wait for jobs | No |
| `flow job-download` | Download a completed MP4 | No |

## Safe generation workflow

```bash
opencli flow gen --prompt "a cat walking through a sunlit meadow" --length 8 --aspect 9:16 --dryRun true
opencli flow gen --prompt "a cat walking through a sunlit meadow" --length 8 --aspect 9:16 --yes
opencli flow job-wait --mediaId <MEDIA_ID>
opencli flow job-download --mediaId <MEDIA_ID> --out out.mp4
```

Always preview cost with `--dryRun` before a new prompt. Use `--yes` only when the user intends to submit and spend credits.

## References and editing

```bash
opencli flow media-upload --file ./hero.png --name hero
opencli flow media-upload --file ./background.jpg --name background
opencli flow gen --prompt "the hero walks across the background" --refs hero,background --length 8 --aspect 9:16 --yes
opencli flow media-upload --file ./clip.mp4 --name source
opencli flow gen --prompt "turn this into a night scene with fog" --refVideo source --yes
```

Aliases, local paths, and media IDs are accepted. Local files are SHA-256 deduplicated, and the cache is isolated by project ID.

## Error handling

- `STUB_WORKFLOW`: refresh the Flow page and retry once with `--reload`.
- `RATE_LIMIT`: wait before retrying.
- `CONTENT_POLICY` or `CELEBRITY_POLICY`: change the prompt instead of retrying repeatedly.
- `AUTH`: sign in again to Flow in the bound Chrome profile.
- `INSUFFICIENT_CREDITS`: reduce duration or count, or add credits.

Never submit `flow gen --yes` without explicit user approval for the spend.
