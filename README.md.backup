# Milo Portable System

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
