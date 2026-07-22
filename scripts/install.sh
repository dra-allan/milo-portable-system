#!/usr/bin/env bash
# Milo Portable System Installation Script
# Automatically sets up the portable Milo system

set -euo pipefail

echo "🚀 Installing Milo Portable System..."
echo "===================================="

# Determine script location and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "📁 Project root: $PROJECT_ROOT"

# Change to project root
cd "$PROJECT_ROOT"

# Step 1: Check and install Node.js dependencies
echo "📦 Installing Node.js dependencies..."
if [ -f "package.json" ]; then
  npm install
else
  # Create a basic package.json if it doesn't exist
  cat > package.json << 'EOFPKG'
{
  "name": "milo-portable",
  "version": "1.0.0",
  "description": "Portable Milo AI Assistant System",
  "main": "index.js",
  "scripts": {
    "start": "node scripts/milo-launcher.js"
  },
  "dependencies": {
    "@grinev/opencode-telegram-bot": "^1.0.0",
    "better-sqlite3": "^8.0.0",
    "chokidar": "^3.5.0",
    "uuid": "^9.0.0",
    "@modelcontextprotocol/sdk": "^0.1.0"
  }
}
EOFPKG
  npm install
fi

# Step 2: Check and set up Python dependencies for Telegram bot
echo "🐍 Setting up Python environment..."
if [ -d "milo-bot" ]; then
  cd milo-bot
  # Check if requirements exist, otherwise create basic ones
  if [ ! -f "requirements.txt" ]; then
    cat > requirements.txt << 'EOFPACK'
python-telegram-bot==20.0
httpx==0.24.0
EOFPACK
  fi
  
  # Install Python packages
  if command -v pip3 >/dev/null 2>&1; then
    pip3 install -r requirements.txt
  elif command -v pip >/dev/null 2>&1; then
    pip install -r requirements.txt
  else
    echo "⚠️  Warning: pip not found. Please install python-telegram-bot and httpx manually"
  fi
  cd ..
fi

# Step 3: Set up environment file if it doesn't exist
echo "⚙️  Setting up environment..."
TELEGRAM_ENV_DIR="telegram-data"
if [ ! -f "$TELEGRAM_ENV_DIR/.env" ]; then
  if [ -f "$TELEGRAM_ENV_DIR/.env.example" ]; then
    cp "$TELEGRAM_ENV_DIR/.env.example" "$TELEGRAM_ENV_DIR/.env"
    echo "📝 Created .env file from template. Please edit it with your Telegram credentials:"
    echo "    nano $TELEGRAM_ENV_DIR/.env"
  else
    echo "⚠️  Warning: .env.example not found"
  fi
else
  echo "✅ .env file already exists"
fi

# Step 4: Initialize Git repository if not already done
echo "🔧 Setting up Git repository..."
if [ ! -d ".git" ]; then
  git init
  echo "📝 Git repository initialized"
else
  echo "✅ Git repository already exists"
fi

# Step 5: Create data/memories.db if it doesn't exist (SQLite will create it automatically on first use)
echo "💾 Setting up storage directory..."
mkdir -p data/backups data/sync
echo "✅ Storage directories ready"

# Step 6: Make scripts executable
echo "🔧 Making scripts executable..."
chmod +x scripts/*.sh 2>/dev/null || true
chmod +x milo-bot/src/bot.py 2>/dev/null || true

# Step 7: Create startup script if it doesn't exist
if [ ! -f "scripts/run.sh" ]; then
  cat > scripts/run.sh << 'EOFS'
#!/usr/bin/env bash
# Start the Milo Telegram bot in long-polling mode.
# Usage: scripts/run.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/telegram-data/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

# Sanity-check the token
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ "${TELEGRAM_BOT_TOKEN}" = "__FILL_ME_FROM_BOTFATHER__" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN missing or still the placeholder." >&2
  echo "       Set it in ${ENV_FILE} or export it before running." >&2
  exit 2
fi

# Ensure we have a valid database path
if [ -z "${MILO_DB_PATH:-}" ]; then
  export MILO_DB_PATH="${ROOT}/data/memories.db"
fi

echo "🚀 Starting Milo Telegram bot..."
echo "💾 Using database: $MILO_DB_PATH"
exec python "${ROOT}/milo-bot/src/bot.py" "$@"
EOFS
  chmod +x scripts/run.sh
fi

# Step 8: Create the main launcher script if it doesn't exist
if [ ! -f "scripts/milo-launcher.js" ]; then
  echo "📝 Creating main launcher script..."
  # This will be created separately - for now just note it's needed
fi

echo
echo "✅ Installation complete!"
echo "===================================="
echo "📝 Next steps:"
echo "1. Edit your Telegram credentials: nano telegram-data/.env"
echo "2. Get your token from @BotFather and chat ID from @userinfobot"
echo "3. Test the setup: scripts/run.sh --help (to see bot options)"
echo "4. Start the system: scripts/run.sh"
echo
echo "🔄 For bidirectional GitHub sync, make sure to:"
echo "   - Create a repository on GitHub"
echo "   - Run: git remote add origin <your-repo-url>"
echo "   - Push initial commit: git push -u origin main"
echo
echo "🎉 Your portable Milo system is ready to use!"
