# Milo Portable System

A completely portable, cross-platform Milo AI assistant system that works on Windows, Linux, and Termux/Android.

![GitHub last commit](https://img.shields.io/github/last-commit/your-username/milo-portable-system)
![GitHub repo size](https://img.shields.io/github/repo-size/your-username/milo-portable-system)
![GitHub](https://img.shields.io/github/license/your-username/milo-portable-system)
![Node.js Version](https://img.shields.io/node/v/milo-portable)
![Python Version](https://img.shields.io/python/required-version)

## 🚀 Quick Start for AI Assistants (Claude Code / OpenCode)

If you're an AI assistant tasked with installing this system, follow these exact steps:

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/milo-portable-system.git ~/.milo-portable
cd ~/.milo-portable
```

### 2. Run the Installation Script
```bash
# This will set up everything automatically
bash scripts/install.sh
```

### 3. Configure GitHub Backup (Optional but Recommended)
```bash
# If you have created a GitHub repository for backup, set the remote URL:
# Replace <your-username> and <repo-name> with your actual values
git remote add origin https://github.com/<your-username>/<repo-name>.git
# Verify the remote was set correctly
git remote -v
```

### 4. Configure Your Bot
```bash
# Edit this file with your Telegram bot credentials
nano telegram-data/.env
```

Get your credentials from:
- **TELEGRAM_BOT_TOKEN**: @BotFather on Telegram
- **TELEGRAM_CHAT_ID**: @userinfobot on Telegram (your numeric user ID)

### 4. Verify Installation
```bash
# Check that Node.js dependencies are installed
ls -la node_modules/
# Check that Python dependencies are available
ls -la milo-bot/
```

### 5. Start the System
```bash
bash scripts/run.sh
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
   - **Opencode CLI** (installed globally: `npm install -g opencode`)

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
   # Edit .env with your Telegram credentials:
   # - TELEGRAM_BOT_TOKEN: Get from @BotFather
   # - TELEGRAM_CHAT_ID: Get from @userinfobot
   nano telegram-data/.env
   ```

5. **Set Up GitHub Repository** (for bidirectional sync):
   ```bash
   # Initialize git if not already done
   git init
   
   # Set your actual GitHub repository URL
   git remote add origin https://github.com/your-username/your-repo-name.git
   
   # Add files and commit
   git add .
   git commit -m "Initial commit: Milo Portable System"
   
   # Push to GitHub
   git push -u origin main
   ```

6. **Verify GitHub Connection**:
   ```bash
   git remote -v
   # Should show your GitHub URLs for fetch and push
   ```

7. **Start the System**:
   ```bash
   bash scripts/run.sh
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
   - Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env: `cat telegram-data/.env`
   - Verify the bot is running: `ps aux | grep bot.py` (Linux/macOS) or Task Manager (Windows)
   - Check logs: `tail -20 logs/launcher.log`

2. **Git sync not working**:
   - Verify GitHub remote is set correctly: `git remote -v`
   - Check SSH key or PAT authentication: `git ls-remote origin`
   - Ensure you have push/pull permissions on the repository
   - Test connection: `ssh -T git@github.com` (if using SSH)

3. **Memory not persisting**:
   - Check that `data/memories.db` exists and is readable: `ls -la data/memories.db`
   - Verify SQLite3 is available: `sqlite3 --version`
   - Check database permissions: `ls -la data/`

4. **Installation issues**:
   - Node.js version: `node --version` (should be v16+)
   - NPM packages: `ls node_modules/` should show @grinev/opencode-telegram-bot, sqlite3, etc.
   - Python packages: `pip list | grep -E "(telegram|httpx)"`

### Verification Commands for AI Assistants
After installation, AI assistants can verify the system with:

```bash
# Check Node.js dependencies
ls node_modules/@grinev/opencode-telegram-bot node_modules/sqlite3 node_modules/chokidar node_modules/uuid

# Check Python dependencies  
ls milo-bot/src/bot.py

# Check key files exist
ls -la scripts/gw-watcher.js scripts/milo-mcp-bundle.js scripts/run.sh telegram-data/.env

# Check directory structure
ls -la vault/ data/ logs/ agents/ mcp/ playwright-bridge/ storage/ docs/

# Check GitHub remote
git remote -v

# Test MCP server (should start without errors)
node scripts/milo-mcp-bundle.js &
sleep 2
curl -s http://localhost:3000/ 2>/dev/null || echo "MCP server started (expect connection refused if no HTTP endpoint)"
```

### Logs
- Main log: `logs/launcher.log`
- Telegram bot log: Console output (visible when running)
- Sync log: `data/backups/git-sync.log`

## 📜 License

MIT License - feel free to modify and distribute!

## 💡 Tips for AI Assistants

When helping users install this system, follow these exact steps:

### For Claude Code Users:
1. **Read this README completely** before starting
2. **Execute in order**:
   - `git clone <repository-url> ~/.milo-portable`
   - `cd ~/.milo-portable`
   - `bash scripts/install.sh`
   - Edit `telegram-data/.env` with user's credentials
   - `bash scripts/run.sh`

### For OpenCode Users:
1. **Use the terminal tool** to execute commands sequentially
2. **Verify each step** before proceeding to the next
3. **Pay special attention** to:
   - Environment variable configuration
   - GitHub remote setup (if backup is desired)
   - Dependency installation success

### Validation Checklist:
After guiding the user through installation, verify:
- [ ] Node.js modules installed: `ls node_modules/`
- [ ] Python dependencies: `pip list | grep telegram`
- [ ] Environment file configured: `grep -v "^#" telegram-data/.env | grep -v "^$"`
- [ ] Git initialized: `git status`
- [ ] Key scripts present: `ls scripts/*.sh scripts/*.js`
- [ ] Directory structure correct: `ls -la vault/ data/ logs/`

### Common Mistakes to Avoid:
- **Skipping the installation script**: Always run `scripts/install.sh` first
- **Incorrect environment variables**: Double-check TELEGRAM_BOT_TOKEN format (should be like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
- **Wrong chat ID**: Must be numeric user ID from @userinfobot, not username
- **GitHub setup confusion**: The system works without GitHub, but for backup, a real repository URL is needed
- **Path issues**: The system uses relative paths from the cloned directory

### Quick Validation Command:
Run this to check if the system is properly installed:
```bash
echo "=== Validation Check ===" && \
echo "Node.js: $(node --version)" && \
echo "NPM packages: $(ls node_modules/ | wc -l) packages" && \
echo "Python bot: $(test -f milo-bot/src/bot.py && echo 'FOUND' || echo 'MISSING')" && \
echo "Env file: $(test -f telegram-data/.env && echo 'FOUND' || echo 'MISSING')" && \
echo "Key scripts: $(test -f scripts/milo-mcp-bundle.js && echo 'FOUND' || echo 'MISSING')" && \
echo "Git repo: $(git rev-parse --git-dir 2>/dev/null && echo 'INITIALIZED' || echo 'NOT INIT')"
```
