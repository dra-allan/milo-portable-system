#!/usr/bin/env bash
# Provision the Shorts pipeline on a fresh Ubuntu VPS.
#
# Run AFTER cloning the repo and scp'ing the state bundle:
#   git clone https://github.com/dra-allan/milo-portable-system.git
#   cd milo-portable-system/artisan/youtube-shorts-pipeline
#   scp user@old-machine:state_bundle.tar.gz /tmp/
#   bash deploy/setup_vps.sh /tmp/state_bundle.tar.gz
#
# Idempotent: safe to re-run.

set -euo pipefail

BUNDLE="${1:-/tmp/state_bundle.tar.gz}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> [1/6] System packages (ffmpeg, python3-venv)"
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg python3-venv python3-pip python3-dev

echo "==> [2/6] Python venv + requirements"
if [ ! -d "$ROOT/venv" ]; then
    python3 -m venv "$ROOT/venv"
fi
"$ROOT/venv/bin/pip" install --upgrade -q pip
"$ROOT/venv/bin/pip" install -q -r "$ROOT/requirements.txt"

echo "==> [3/6] Restore state bundle"
if [ -f "$BUNDLE" ]; then
    tar -xzf "$BUNDLE" -C "$ROOT"
    echo "   restored tokens / niches / .env / db / transcripts / clip plans"
else
    echo "   !! no bundle found at $BUNDLE -- continuing with empty state"
fi

echo "==> [4/6] Fix .env paths for this machine"
ENVFILE="$ROOT/.env"
if [ -f "$ENVFILE" ]; then
    # Point SHORTS_DIR at a Linux path if it still carries a Windows path.
    if grep -qiE '^SHORTS_DIR=.*\\\\|^SHORTS_DIR=.*[A-Za-z]:/' "$ENVFILE"; then
        sed -i "s|^SHORTS_DIR=.*|SHORTS_DIR=$ROOT/data/shorts|" "$ENVFILE"
        echo "   SHORTS_DIR -> $ROOT/data/shorts"
    fi
    # OAuth paths are relative (credentials.json, config/youtube_token.json) and
    # resolve against the repo root, so they need no rewriting on Linux.
else
    cp "$ROOT/config/.env.template" "$ENVFILE"
    echo "   no .env present; copied template (edit it: channels, paths)"
fi

echo "==> [5/6] Environment check"
cd "$ROOT"
"$ROOT/venv/bin/python" -m src.main --mode test || {
    echo "!! --mode test reported problems. Fix before scheduling."
    exit 1
}

echo "==> [6/6] Install systemd service"
SVC_DIR="$HOME/.config/systemd/user"
mkdir -p "$SVC_DIR"
sed "s|__PIPELINE_ROOT__|$ROOT|g" "$ROOT/deploy/shorts-schedule.service" > "$SVC_DIR/shorts-schedule.service"
systemctl --user daemon-reload

if [ -z "${SKIP_START:-}" ]; then
    systemctl --user enable --now shorts-schedule.service
    echo "   service enabled (check with: systemctl --user status shorts-schedule)"
else
    echo "   service installed but not started (SKIP_START set)"
fi

echo ""
echo "Done. Commands:"
echo "  run once now:          $ROOT/venv/bin/python -m src.main --mode once"
echo "  service status:        systemctl --user status shorts-schedule"
echo "  live log:              journalctl --user -u shorts-schedule -f"
