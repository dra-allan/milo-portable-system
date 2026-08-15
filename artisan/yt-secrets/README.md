# YouTube channel auth

Run this from `artisan/`, in a terminal owned by you. Never start OAuth through an agent shell, scheduler, daemon, or background process:

```text
python -m yt_secrets auth
```

That walks through every `active: true` channel one at a time. Each flow opens the browser, owns its localhost callback in the same foreground process, waits up to 60 minutes, refresh-checks the token, resolves the actual YouTube channel name and ID, then writes `youtube_token_<channel-key>.json` to the configured pipeline directory. Existing tokens are not touched until that channel completes successfully.

Useful commands:

```text
python -m yt_secrets auth --channel capital_mindset
python -m yt_secrets auth --channel NXS --credentials C:\path\to\milo-mcp-client.json
python -m yt_secrets status
```

## New machine setup

1. Clone the repo and install the pipeline dependencies, including `PyYAML`, `google-api-python-client`, `google-auth`, and `google-auth-oauthlib`.
2. Download the Desktop OAuth client JSON once per Google Cloud project and place it at `artisan/yt-secrets/<slug>/credentials.json`. Never commit it. Project A uses slug `draallan0`; Project B uses slug `adrasaltsxxx`.
3. Run `cd artisan` followed by `python -m yt_secrets auth`.
4. Approve each channel while signed into its owning Gmail. For NXS and explaination, use `draallan12@gmail.com`.
5. Confirm everything with `python -m yt_secrets status`.

Do not copy refresh tokens between machines as the setup strategy. Re-mint them locally. Copy only the ignored OAuth client JSON from Google Cloud Console, or download a fresh copy.

## Adding a channel or routing one to a pipeline

Add one row under `channels:` in `channels.yaml`:

```yaml
my_channel:
  email: owner@gmail.com
  slug: project-slug
  active: true
  pipelines: [shorts]
  token_dir: artisan/youtube-shorts-pipeline/config
```

Use a stable lowercase key for new channels. `pipelines` is documentation and validation context; the token filename always uses the key, so every pipeline can target the same exact channel with `--channel my_channel` or its equivalent environment setting. Set `active: false` until the Google project has the owner email as a consent-screen test user and the project is published.

## Project setup that only happens once

- Project A `yt-flick-shorts`: add `draallan0@gmail.com` as a test user, then publish the OAuth consent screen.
- Project B `milo-mcp`: add `adrasaltsxxx@gmail.com` and `draallan12@gmail.com` as test users, then publish the OAuth consent screen.
- Testing mode causes refresh tokens to expire after seven days. Publishing avoids that recurring machine setup trap.
