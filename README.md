# Milo Portable System

<<<<<<< HEAD
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
=======
A completely portable, cross-platform Milo AI assistant system that works on Windows, Linux, and Termux/Android.

## 🚀 Quick Start for AI Assistants (Claude Code / OpenCode)

If you're an AI assistant tasked with installing this system, follow these exact steps:

### 1. Clone the Repository
```bash
git clone <this-repo-url> ~/.milo-portable
cd ~/.milo-portable
```

### 2. Run the Installation Script
```bash
# This will set up everything automatically
scripts/install.sh
```

### 3. Configure Your Bot
Edit the environment file:
```bash
# Edit this file with your Telegram bot credentials
nano telegram-data/.env
```

Get your credentials from:
- **TELEGRAM_BOT_TOKEN**: @BotFather on Telegram
- **TELEGRAM_CHAT_ID**: @userinfobot on Telegram (your numeric user ID)

### 4. Start the System
```bash
scripts/run.sh
```

## 📁 System Structure

```
.milo-portable/
├── vault/                    # Your Obsidian knowledge base (Markdown)
├── telegram-data/            # Telegram bot configuration
│   ├── .env                  # Environment variables (create from .env.example)
│   └── .env.example          # Template for environment variables
├── data/                     # Persistent data storage
│   ├── backups/              # GitHub backup files
│   ├── sync/                 # Sync state files
│   └── memories.db           # SQLite memory database
├── logs/                     # Log files
├── scripts/                  # Installation and runtime scripts
│   ├── install.sh            # Auto-installation script
│   ├── run.sh                # Main execution script
│   ├── telegram-send.sh      # Utility to send Telegram messages
│   └── sync-watcher.sh       # Bidirectional GitHub sync watcher
├── agents/                   # Specialized agent definitions
├── mcp/                      # MCP server configurations
├── playwright-bridge/        # Browser automation tools
├── storage/                  # Agent memory storage
└── docs/                     # Documentation
```

## 🔧 Manual Installation Steps

If you prefer to install manually:

1. **Ensure Dependencies Are Installed**:
   - **Node.js** (v16+) with npm
   - **Python 3.x** (for Telegram bot)
   - **Git** 
   - **Opencode CLI** (installed globally)

2. **Install Node.js Dependencies**:
   ```bash
   npm install
   ```

3. **Install Python Dependencies**:
   ```bash
   pip install python-telegram-bot httpx
   ```

4. **Configure Environment**:
   ```bash
   cp telegram-data/.env.example telegram-data/.env
   # Edit .env with your Telegram credentials
   ```

5. **Set Up GitHub Repository** (for bidirectional sync):
   ```bash
   git init
   git remote add origin <your-github-repo-url>
   git add .
   git commit -m "Initial commit"
   git push -u origin main
   ```

6. **Start the System**:
   ```bash
   scripts/run.sh
   ```

## 🔄 Bidirectional GitHub Sync

This system features intelligent two-way synchronization with GitHub:
- **Pushes local changes** to GitHub
- **Pulls remote changes** from GitHub  
- **Detects and prevents conflicts**
- **Maintains sync history** via Git notes
- **Works across all platforms** (Windows/Linux/Termux)

## 📱 Platform Support

- **Windows 10/11** (native)
- **Linux** (Ubuntu, Debian, etc.)
- **Termux/Android** (via Ubuntu chroot or native)
- **macOS** (should work, tested on Linux/Windows)

## 🧠 Features

- **Persistent Memory**: SQLite-based memory system that survives restarts
- **Cross-Device Awareness**: Knows which device you're using
- **Telegram Bot**: Full-featured AI assistant via Telegram
- **Opencode Integration**: Seamless connection to Opencode CLI
- **Vault Integration**: Read-only access to your Obsidian knowledge base
- **Automatic Backups**: GitHub-based version control
- **Zero Configuration**: Works out of the box after initial setup

## ⚙️ Configuration

### Environment Variables (`telegram-data/.env`)
```
# Required - Get from @BotFather
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Required - Get from @userinfobot  
TELEGRAM_CHAT_ID=your_telegram_user_id_here

# Optional - For Opencode bridge
OPENCODE_BIN=opencode
OPENCODE_WORKDIR=/path/to/your/workspace
OPENCODE_TIMEOUT_SEC=600
MILO_AGENT=milo

# Logging
LOG_LEVEL=INFO
```

### Customization
- Modify `scripts/run.sh` to change how the bot starts
- Adjust sync frequency in `scripts/sync-watcher.sh`
- Customize agent behavior in `agents/` directory

## 🛠️ Troubleshooting

### Common Issues

1. **Bot not responding**:
   - Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
   - Verify the bot is running: `ps aux | grep bot.py`
   - Check logs: `tail -f logs/launcher.log`

2. **Git sync not working**:
   - Verify GitHub remote is set correctly: `git remote -v`
   - Check SSH key or PAT authentication
   - Ensure you have push/pull permissions

3. **Memory not persisting**:
   - Check that `data/memories.db` exists and is writable
   - Verify SQLite3 is available on your system

### Logs
- Main log: `logs/launcher.log`
- Telegram bot log: Console output (visible when running)
- Sync log: `data/backups/git-sync.log`

## 📜 License

MIT License - feel free to modify and distribute!

## 💡 Tips for AI Assistants

When helping users install this system:
1. Always run `scripts/install.sh` first for automated setup
2. Verify the GitHub remote is configured before first run
3. Ensure Telegram credentials are correct
4. Remind users to keep their .env file secure (never share it)
5. The system is designed to be copy-portable - just copy the entire .milo-portable folder to move between devices
>>>>>>> 6a77522 (Initial commit: Milo Portable System with bidirectional GitHub sync, Telegram bot, MCP server, and portable memory system)
