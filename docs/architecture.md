# Milo Telegram bot — architecture

The bot is intentionally a single Python file (`milo-bot/src/bot.py`) so it boots
fast on Termux and has zero external dependencies beyond `python-telegram-bot`
and the local `opencode` CLI.

## Two layers

```
Telegram ─┐
          │                        ┌── opencode run --agent milo  ──┐
          ├── chat/ /milo ─────────►│  (Anthropic router via opencode)│ ─► Telegram reply
          │                        └─────────────────────────────┘
          │
          ├── /opencode <prompt> ─► opencode run --agent milo --auto <prompt>
          │
          ├── /mem save /mem list ─► Sqlite (~/.milo/milo-bot.sqlite)   [fallback only]
          │
          └── /vault <relative> ──► ReadOnly Read ~/vault/<relative>  [Milo memory vault]
```

## Routing decisions

- **`/opencode`** is the work surface — pass it anything you'd pass to opencode:
  feature specs, debug tasks, refactors. The bot prefixes no Milo persona wrap
  beyond what opencode's agent system already applies.
- **Direct chat + `/milo`** wrap the message with a Telegram-source nudge and route
  through opencode's `milo` agent (configured in `~/.config/opencode/agent/milo.md`).
- **`/mem`** intentionally writes to local SQLite — Engram MCP (`engram-mcp`) is
  the production hot memory and replaces it transparently once `ENGAM_API_KEY` is
  wired into opencode.json.

## Authz

`ALLOWED_USER_IDS` (comma-separated Telegram user IDs) gates every command. If
unset, the bot replies to everyone — tighten before exposing publicly.

## Process management

Daemonized via `nohup scripts/run.sh &` on Termux; on a real Linux box use
`systemd`/`NSSM` (see `dra-allan/milo` repo for the canonical bootstrap). The
blueprint repo also has `runtime/awareness.cjs` for cross-session coordination,
which this bot is compatible with.

## Future

- Replace `subprocess` `opencode run` calls with opencode's ACP server client
  (see `opencode acp --help`) for streaming replies — bigger lift, more fluid UX.
- `mem_save`/`mem_context` proxy when `ENGAM_API_KEY` is set: send the memory
  payload to engram-mcp's language server rather than SQLite.
