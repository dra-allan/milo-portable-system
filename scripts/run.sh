#!/usr/bin/env bash
# Start the Milo Telegram bot in long-polling mode.
# Usage: scripts/run.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ROOT}/.env"

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

exec python "${ROOT}/milo-bot/src/bot.py" "$@"
