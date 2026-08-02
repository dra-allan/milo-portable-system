# Milo Portable System

Transfer Milo to any machine in 3 commands.

## Quickstart

```bash
git clone https://github.com/dra-allan/milo-portable-system
cd milo-portable-system
python milo.py install
```

That's it. Works on Windows, Linux, and Android (Termux).

## What it does

| Step | Action |
|------|--------|
| 1 | Prompts for secrets (Telegram, GitHub, Supabase) |
| 2 | Clones all Milo repos into `~/.milo/` |
| 3 | Installs Python deps |
| 4 | Registers services (NSSM on Windows, systemd on Linux, screen on Termux) |

## Commands

```bash
python milo.py install    # Full setup on a fresh machine
python milo.py start      # Start all services
python milo.py stop       # Stop all services
python milo.py status     # Health check — repos, services, logs
python milo.py backup     # git push all repos to GitHub
python milo.py restore    # git pull all repos
```

## Repos managed

| Repo | Purpose |
|------|---------|
| `milo` | Runtime scripts, awareness, memory persistence |
| `agentmemory-milo` | Telegram bot + OpenCode bridge |
| `dra-brains` | Obsidian vault (your memory) |
| `milo-portable-system` | This repo |

## Platform notes

- **Windows**: Uses NSSM (auto-downloaded). Run once in any terminal — no admin needed for setup, only service registration.
- **Linux**: Uses systemd user units. No sudo required.
- **Android/Termux**: Uses `screen` sessions. Install screen first: `pkg install screen`

## Moving to a new machine

```bash
# On new machine
git clone https://github.com/dra-allan/milo-portable-system
cd milo-portable-system
python milo.py install      # → enter secrets when prompted
python milo.py restore      # → pull latest state from GitHub
python milo.py start        # → Milo is alive
```

## Requirements

- Python 3.8+
- Git
- That's it
