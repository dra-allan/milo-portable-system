#!/usr/bin/env python3
"""Compatibility entrypoint for the safe foreground auth flow.

Preferred usage from artisan/: ``python -m yt_secrets auth``.
Legacy usage ``python mint_token.py <channel>`` is kept so old buttons and
scripts do not silently fall back to the obsolete callback implementation.
"""
import sys
from pathlib import Path

ARTISAN = Path(__file__).resolve().parent.parent
if str(ARTISAN) not in sys.path:
    sys.path.insert(0, str(ARTISAN))

from yt_secrets.auth import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] not in {"auth", "status"}:
        args = ["auth", "--channel", args[0]] + args[1:]
    raise SystemExit(main(args))
