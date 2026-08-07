#!/usr/bin/env bash
# Send a one-off message to your configured chat via the Telegram API.
# Used by the opencode Telegram MCP server (mcp-telegram-claudecode) when
# it needs to push a message from inside opencode out to Telegram, and also
# usable from the CLI for ad-hoc pings.
#
# Usage: scripts/telegram-send.sh "hello from milo"
#        scripts/telegram-send.sh --chat-id 12345 "hello"

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT="${TELEGRAM_CHAT_ID:-}"

if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set." >&2
  echo "Put them in ${ENV_FILE} or export them." >&2
  exit 2
fi

if [ "${1:-}" = "--chat-id" ]; then
  CHAT="$2"
  shift 2
fi

if [ $# -eq 0 ]; then
  echo "Usage: $0 [--chat-id ID] <message>" >&2
  exit 1
fi

MESSAGE="$*"

curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT}" \
  --data-urlencode "text=${MESSAGE}" \
  --data-urlencode "disable_web_page_preview=true" >/dev/null

echo "sent to ${CHAT}: ${MESSAGE:0:120}"
