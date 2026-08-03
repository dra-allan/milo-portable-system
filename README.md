# Milo Portable System

Transfer Milo to any machine in 3 commands.

## Installation Guide

### Prerequisites
- **Python 3.8+** (https://www.python.org/downloads/)
- **Git** (https://git-scm.com/downloads/)

### Step-by-step Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/dra-allan/milo-portable-system
   cd milo-portable-system
   ```

2. **Run the installer**
   ```bash
   python milo.py install
   ```
   The installer will:
   - Prompt you for any required secrets (Telegram bot token/chat ID, GitHub token, Supabase credentials, etc.)
   - Clone all Milo repositories into `~/.milo/`
   - Install required Python dependencies (none are required by default; optional dependencies are installed as needed)
   - Set up services to run Milo in the background:
     - Windows: Installs a service via NSSM (downloaded automatically)
     - Linux/macOS/WSL: Installs a systemd user service
     - Android/Termux: Sets up a `screen` session (ensure `screen` is installed via `pkg install screen`)

3. **Verify installation**
   ```bash
   python milo.py doctor
   ```
   This will report any issues with the setup.

## Usage Guide

### Core Commands

| Command | Description |
|---------|-------------|
| `milo install` | Full setup on a fresh machine (same as step 2 above) |
| `milo start` | Start all Milo services (background agents, Telegram bot, etc.) |
| `milo stop` | Stop all running Milo services |
| `milo status` | Show health status: repositories, services, logs |
| `milo backup` | Push all Milo repositories to GitHub (requires `GITHUB_PAT`) |
| `milo restore` | Pull latest changes from GitHub for all Milo repositories |
| `milo doctor` | Run diagnostic checks (same as during installation) |
| `milo remember "..."` | Save a durable memory |
| `milo recall "query"` | Search your memories |
| `milo send "message"` | Send a message through all configured channels (Telegram, ntfy, etc.) |
| `milo channels` | List communication channels and their configuration status |
| `milo channels setup [NAME...]` | Interactively configure one or more channels (e.g., `milo channels setup telegram`) |
| `milo channels test [--to CHANNELS] [message]` | Send a test message through configured channels |

### Communication Channels

Milo can send messages via multiple platforms. Configure them with `milo channels setup`:

- **Telegram**: Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
- **Discord**: Requires `DISCORD_WEBHOOK_URL`
- **Slack**: Requires `SLACK_WEBHOOK_URL`
- **ntfy**: Requires `NTFY_TOPIC` (optional `NTFY_SERVER`, `NTFY_TOKEN`)
- **Webhook**: Requires `MILO_WEBHOOK_URL` (any URL accepting JSON POST)
- **Log**: Always available; writes to `~/milo/logs/channels.log`

Example:
```bash
# Configure Telegram
milo channels setup telegram
# (enter bot token and chat ID when prompted)

# Test all configured channels
milo channels test

# Test only ntfy with a custom message
milo channels test --to ntfy "Hello from Milo!"

# Send a message via all configured channels (same as `milo send`)
milo send "Task completed!"
```

### Advanced Usage

- **Running Milo interactively**:  
  ```bash
  milo
  ```
  Starts an interactive REPL where you can talk to Milo directly.

- **Managing skills**:  
  ```bash
  milo skills list          # List available skills
  milo learn "how to backup my code"   # Turn a recent interaction into a reusable skill
  ```

- **Memory management**:  
  ```bash
  milo memory stats        # Show memory database statistics
  memomemo export          # Export memories to a file
  memomemo import <file>   # Import memories from a file
  ```

### Moving to a New Machine

1. Clone the repository: `git clone https://github.com/dra-allan/milo-portable-system`
2. Run `milo install` and enter your secrets when prompted.
3. Restore your data from GitHub: `milo restore`
4. Start Milo: `milo start`

### Troubleshooting

- Run `milo doctor` to check for common issues.
- Check logs: `~/.milo/logs/` (contains service logs and channel logs).
- Ensure required environment variables are set in `~/.milo/.env`.
- For service-related issues, reinstall the service: `milo install --force-service`.

## Requirements

- Python 3.8+
- Git
- That's it

Enjoy using Milo!