#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# flow_profiles_up.sh - open the Chrome Browser Bridge profiles (Linux VPS)
#
#   ./scripts/flow_profiles_up.sh                 # flow-1..flow-6
#   ./scripts/flow_profiles_up.sh flow-1 flow-2
#
# Google Flow only generates images while its Chrome profiles are OPEN. This
# script opens them; it does NOT log in. Google login and reCAPTCHA stay a
# one-time human step and are never automated.
#
# HEADLESS VPS WARNING
# --------------------
# Flow is a browser-bound Google Labs product. Whether the bridge survives on
# a headless server is UNKNOWN and must be tested before you call the VPS
# ready. If there is no display, run Chrome under Xvfb first:
#
#   sudo apt-get install -y xvfb
#   Xvfb :99 -screen 0 1920x1080x24 &
#   export DISPLAY=:99
#   ./scripts/flow_profiles_up.sh
#
# Verify afterwards:
#   python run_pov_pipeline.py --check-profiles --flow-profiles flow-1,flow-2
# ---------------------------------------------------------------------------
set -uo pipefail

PROFILES=("$@")
if [ ${#PROFILES[@]} -eq 0 ]; then
  PROFILES=(flow-1 flow-2 flow-3 flow-4 flow-5 flow-6)
fi

OPENCLI="$(command -v opencli || true)"
if [ -z "$OPENCLI" ]; then
  echo "opencli is not on PATH. Install it: npm i -g opencli" >&2
  exit 1
fi

if [ -z "${DISPLAY:-}" ]; then
  echo "WARNING: DISPLAY is unset. Chrome needs a display (see Xvfb above)." >&2
fi

echo "Opening ${#PROFILES[@]} Flow profile(s) via $OPENCLI"
for p in "${PROFILES[@]}"; do
  echo "  -> $p"
  "$OPENCLI" profile open "$p" || echo "  WARN: $p did not open cleanly" >&2
  sleep 3
done

echo
echo "Connected profiles:"
"$OPENCLI" profile list
echo
echo "If a profile shows as logged out, sign in to it by hand once."
