#!/usr/bin/env bash
# Milo Portable System Installation Validator
# Runs checks to verify the system is properly installed

set -euo pipefail

echo "🔍 Validating Milo Portable System Installation..."
echo "================================================"

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Track checks
PASSED=0
TOTAL=0

check() {
  TOTAL=$((TOTAL + 1))
  if eval "$1"; then
    echo "✅ $2"
    PASSED=$((PASSED + 1))
  else
    echo "❌ $2"
  fi
}

# Check Node.js
check "node --version | grep -E '^v([1-9][0-9]*|[1-9])\.' | head -1" "Node.js installed (v16+)"

# Check npm packages
check "ls node_modules/@grinev/opencode-telegram-bot >/dev/null 2>&1" "@grinev/opencode-telegram-bot installed"
check "ls node_modules/sqlite3 >/dev/null 2>&1" "sqlite3 installed"
check "ls node_modules/chokidar >/dev/null 2>&1" "chokidar installed"
check "ls node_modules/uuid >/dev/null 2>&1" "uuid installed"

# Check Python bot
check "test -f milo-bot/src/bot.py" "Telegram bot script exists"
check "test -d milo-bot/src" "Telegram bot source directory exists"

# Check environment file
check "test -f telegram-data/.env" "Environment file exists"
check "grep -q 'TELEGRAM_BOT_TOKEN' telegram-data/.env" "TELEGRAM_BOT_TOKEN configured in .env"
check "grep -q 'TELEGRAM_CHAT_ID\|ALLOWED_USER_IDS' telegram-data/.env" "User ID configured in .env"

# Check key scripts
check "test -f scripts/install.sh" "Installation script exists"
check "test -f scripts/run.sh" "Run script exists"
check "test -f scripts/milo-mcp-bundle.js" "MCP bundle exists"
check "test -f scripts/git-watcher.js" "Git watcher exists"

# Check directory structure
check "test -d vault" "Vault directory exists"
check "test -d data" "Data directory exists"
check "test -d data/backups" "Data backups directory exists"
check "test -d data/sync" "Data sync directory exists"
check "test -d logs" "Logs directory exists"
check "test -d agents" "Agents directory exists"
check "test -d mcp" "MCP directory exists"
check "test -d storage" "Storage directory exists"

# Check Git
check "git rev-parse --git-dir >/dev/null 2>&1" "Git repository initialized"
check "test -n \"$(git remote get-url origin 2>/dev/null || echo '')\" && [[ \"$(git remote get-url origin 2>/dev/null)\" != \"https://github.com/username/milo-portable-system.git\" ]]" "GitHub remote configured (not placeholder)"

# Check files
check "test -f README.md" "README exists"
check "test -f package.json" "Package.json exists"
check "test -f telegram-data/.env.example" "Environment template exists"

echo
echo "📊 Validation Results: $PASSED/$TOTAL checks passed"

if [ $PASSED -eq $TOTAL ]; then
  echo "🎉 All checks passed! The system is properly installed."
  echo
  echo "🚀 Next steps:"
  echo "1. Configure Telegram credentials in telegram-data/.env"
  echo "2. Set up GitHub remote for backup (optional): git remote set-origin url <your-repo-url>"
  echo "3. Start the system: bash scripts/run.sh"
  exit 0
else
  echo "⚠️  Some checks failed. Please review the output above and fix issues."
  echo "💡 Try running: bash scripts/install.sh"
  exit 1
fi