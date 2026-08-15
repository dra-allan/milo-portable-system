---
name: moving-machines
description: Move Milo to a new laptop or phone without losing anything.
version: 1.0.0
author: Milo
tags: [migration, backup, setup]
pinned: true
---

# Moving Milo to another machine

The rule that makes this work: **if it is not in the snapshot, it does not
survive.** Everything below exists to keep that true.

## Leaving the old machine

```bash
milo backup            # snapshot + leak scan + commit + push
```

That is the whole ritual. It writes the snapshot inside the checkout and pushes
it, so the brain leaves the machine. Do it before you close the lid for the last
time — and ideally nightly, which `milo routines init` already sets up.

If `git push` is not an option (no network, borrowed machine):

```bash
milo backup --archive          # one .tar.gz
milo path backups              # where it landed
```

## Arriving on the new machine

The whole install is designed to be **one command from inside the agent**, not a
human walking through steps. Once opencode (or any harness) is installed and
`milo` is importable, Milo self-installs:

```bash
git clone https://github.com/dra-allan/milo-portable-system
cd milo-portable-system
pip install -e .
milo install          # creds, vault, snapshot restore, persona
```

Then the critical opencode step — **do not skip this**:

```bash
milo sync opencode
```

`milo sync opencode` writes `~/.config/opencode/AGENTS.md`, `agent/milo.md`,
`agent/mylo.md`, the command files (remember, recall, learn, handoff, milo,
mylo), and an `opencode.json` with the MCP servers + `agent.milo.mode=primary`.
That is what makes the agent **be Milo** instead of a blank assistant.

After sync, restart opencode and verify:

```bash
milo doctor            # confirm every piece landed
milo routines install  # re-arm the scheduled work
```

From an archive instead: `milo restore --archive <path>`.

**The one-command rule (learned the hard way, 2026-08-15):** when a human
reports "I installed opencode on a new box, now what", the answer is
`milo sync opencode` — never hand-wire config files step by step. The night of
the VPS install was wasted exactly this way; the sync had existed all along.
If you do not have the tools to run it yourself, tell the user to run it. Do
not re-derive what the sync already does.

## What actually moves

| Thing | File in the snapshot |
|---|---|
| Durable memory | `memory.jsonl` |
| The always-loaded notes | `MEMORY.md`, `USER.md` |
| Skills | `skills/` |
| User model | `profile.json` |
| Session history | `sessions.jsonl` |
| Routines | `routines.json` |
| Which secrets exist | `env.template` (**names only**) |

Secret *values* never enter the snapshot. `milo install` re-prompts for them.
That is deliberate, and it is why the repo can be public.

## Things that bite

**Backup refuses to push.** The leak scanner found something credential-shaped
and stopped *before* anything left the machine. Read what it flagged — usually a
token pasted into a memory or a note. Remove it and re-run. Do not bypass it.

**Vault not found.** The vault path differs per platform (Desktop on Windows,
`~/vault` on Termux). Say where it is and everything else follows:

```bash
milo config set MILO_VAULT_DIR "/path/to/dra-brains"
```

**Restore says 0 new.** Correct — it already ran. Restore merges and is
idempotent, so running it twice is safe and nothing was lost.

**An old Engram database is on the machine.** `milo migrate` pulls it into the
unified brain. It reads a copy, so a running Engram is never at risk.

**Installed from a wheel, not a clone.** There is no checkout to commit into, so
the snapshot goes to the fallback directory. Check with `milo path repo` before
assuming a push happened.

**The synced `opencode.json` may contain a GitHub PAT.** `milo sync opencode`
writes the MCP block from stored credentials, and if a GitHub token lives in
milo's state it lands in that config in plaintext. It is a real credential, not
a placeholder — treat it as exposed and rotate it after install. Do not commit
the synced config to any repo.

**`milo-mcp` missing from PATH after sync.** `milo sync opencode` assumes the
Scripts dir is on PATH. If the MCP server won't start, add
`<python>\Scripts` to PATH and restart opencode. `milo-mcp.exe` ships next to
`milo.exe`; verify with `where milo-mcp`.
