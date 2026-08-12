# Milo Flow CLI

**Copyright © 2026 Daada Allan. All rights reserved to the extent permitted by the applicable license.**

Milo Flow CLI turns Google Labs Flow and Omni generation into a practical command-line workflow. It uses the Flow session already authenticated in your Chrome browser through the bridge. It does not store OAuth credentials or cookies.

## What it does

- Generate videos from text, images, and multiple references.
- Preview credit cost before submission with `--dryRun`.
- Deduplicate uploaded media with SHA-256 caching.
- Keep media caches isolated by Flow project.
- Track generation jobs, wait for completion, and download MP4 files.
- Detect common Flow, authentication, rate-limit, and policy failures.
- Return compact tables by default, or JSON, YAML, and CSV for automation.

## Requirements

1. Node.js and `@jackwener/opencli` 1.7.22 or newer.
2. `esbuild` available globally or through your environment.
3. The Milo Flow browser extension installed and connected.
4. Google Labs Flow open in Chrome, with your own account signed in.

## Install

Install the plugin from this repository, then verify the bridge:

```bash
npm install
opencli doctor
opencli flow credits
opencli flow --help
```

The command namespace remains `opencli flow` because Milo Flow CLI runs on the OpenCLI host. The product, package, documentation, and browser extension are branded Milo Flow CLI.

## Quick start

```bash
# List projects
opencli flow project-list

# Select the active project
opencli flow project-use --projectId <PROJECT_UUID>

# Preview cost without submitting
opencli flow gen --prompt "a cat walking through a sunlit meadow" --length 8 --dryRun true

# Submit a generation
opencli flow gen --prompt "a cat walking through a sunlit meadow" --length 8 --yes

# Wait for completion, then download
opencli flow job-wait --mediaId <MEDIA_ID>
opencli flow job-download --mediaId <MEDIA_ID> --out out.mp4
```

## Video editing and references

```bash
# Upload a reference video with a cache alias
opencli flow media-upload --file ./clip.mp4 --name source

# Use the reference video for editing
opencli flow gen --prompt "turn this into a night scene with fog" --refVideo source --yes

# Upload reference images
opencli flow media-upload --file ./hero.png --name hero
opencli flow media-upload --file ./background.jpg --name background

# Generate with multiple references
opencli flow gen --prompt "the hero walks across the background" --refs hero,background --length 8 --aspect 9:16 --yes
```

Reference tokens can be cache aliases, local paths, or Flow media IDs. Local files are SHA-256 deduplicated before upload.

## Commands

| Command | Purpose |
|---|---|
| `flow credits` | Show credit balance and account tier |
| `flow models` | Show supported models, durations, and pricing |
| `flow project-list/current/use` | Inspect and select Flow projects |
| `flow media-upload` | Upload and cache image or video media |
| `flow media-list` | List cached project media |
| `flow gen` | Submit a video generation |
| `flow job-status/wait/list` | Inspect or wait for jobs |
| `flow job-download` | Download a completed MP4 |

## Pricing and limits

Omni currently exposes 4, 6, 8, and 10 second options. Video editing has its own fixed cost. Always use `--dryRun` when testing a new prompt, and use `--yes` only when you intend to spend credits.

## Error handling

- `STUB_WORKFLOW`: refresh the Flow page and retry once with `--reload`.
- `RATE_LIMIT`: wait and retry later.
- `CONTENT_POLICY` or `CELEBRITY_POLICY`: change the prompt; do not repeatedly retry.
- `AUTH`: sign in again to Flow in the bound Chrome profile.
- `INSUFFICIENT_CREDITS`: reduce count or duration, or add credits.

## Data locations

- State: `~/.opencli/clis/flow/state.json`
- Media cache: `~/.opencli/clis/flow/media-cache.json`
- Locks: `~/.opencli/clis/flow/locks/`

The CLI does not store OAuth tokens or cookies. It reads the active browser session when a command runs.

## License and attribution

Milo Flow CLI is created and maintained by Daada Allan. This repository preserves the MIT license for the underlying OpenCLI host integration and retains upstream attribution where required. Milo Flow CLI branding, documentation, workflows, and modifications are Copyright © 2026 Daada Allan.
