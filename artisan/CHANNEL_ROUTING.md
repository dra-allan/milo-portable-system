# Channel routing contract

`artisan/yt-secrets/channels.yaml` is the source of truth. The token filename is always `youtube_token_<channel-key>.json`; use the exact registry key, not a display name.

## Authenticate or inspect one channel

```text
cd artisan
python -m yt_secrets auth --channel capital_mindset
python -m yt_secrets status --channel capital_mindset
```

## Pipeline targets

- Shorts: pass the registry key through the pipeline's channel option or `UPLOAD_CHANNEL` setting. The shared uploader looks for `youtube_token_<key>.json`.
- Ranking: set `RANKING_UPLOAD_CHANNEL=rankdrop` (or another registry key) before the run. The old `RankDrop` default was a bug because it generated a token filename that did not match the registry.
- Clipper: set `upload_channel` in `config/castle_clipping.yaml` or pass the campaign channel. `capital_mindset` is the current configured destination.

Every lane should log both the requested key and the resolved YouTube channel ID. If a key is missing or inactive, fix `channels.yaml` and authenticate that key before posting. Do not create one token copy per pipeline: the registry key is the shared identity.

## Adding a channel

Add a row to `yt-secrets/channels.yaml` with `email`, `slug`, `active`, `pipelines`, and `token_dir`. Add the owner to the relevant Google Cloud consent-screen test users, publish the project, then run the one-channel auth command. This is the only channel onboarding path.

Current active channels are `flick_shorts`, `capital_mindset`, `wealth_mindset`, `NXS`, and `explaination`. `dra_allan_official` and the other rows stay inactive until their project and owner approval are ready.
