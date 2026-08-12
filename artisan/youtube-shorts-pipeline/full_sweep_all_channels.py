#!/usr/bin/env python3
"""Run one fresh source pass for every authenticated upload channel."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from src.config import config  # noqa: E402

def main() -> int:
    authed = list(config.authenticated_channels())
    if not authed:
        print("[sweep] no authenticated channels found", file=sys.stderr)
        return 2
    seen = set()
    niches = []
    for niche in config.niche_names():
        channels = config.get_niche_channels(niche) if hasattr(config, "get_niche_channels") else [config.get_niche_channel(niche)]
        if any(ch in authed for ch in channels if ch):
            niches.append(niche)
    if not niches:
        print("[sweep] authenticated channels are not bound to any niche", file=sys.stderr)
        return 2
    rc = 0
    for niche in niches:
        if niche in seen:
            continue
        seen.add(niche)
        print(f"[sweep] channel-bound niche: {niche}")
        result = subprocess.run([sys.executable, "-m", "src.main", "--mode", "once", "--niche", niche, "--videos", "1"], cwd=str(HERE))
        rc = rc or result.returncode
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
