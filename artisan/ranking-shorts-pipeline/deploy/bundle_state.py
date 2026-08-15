#!/usr/bin/env python3
"""
Bundle the runtime state needed to run the ranking-shorts pipeline on a VPS.

Run ON THE OLD MACHINE (Windows) before migrating. Packs everything that is
not regenerable into a single .tar.gz you copy to the VPS:

  - config/.env                      live env (Gemini keys, OAuth path, caps)
  - data/ranking.db                  the SQLite DB: queue + dedup + upload history
  - data/plans/*.json                saved build plans (re-render without re-source)

It deliberately does NOT bundle clips/ (source downloads), output/ (rendered
MP4s), temp/, vo/ or the venv — all regenerable.

The per-channel OAuth tokens (config/youtube_token_ranking_*.json) live in git
and arrive with the clone, so they are not bundled here.

Usage:
    python deploy/bundle_state.py [--out ranking_state_bundle.tar.gz] [--data-root DIR]
"""

import argparse
import tarfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='ranking_state_bundle.tar.gz',
                        help='output filename (default: ranking_state_bundle.tar.gz)')
    parser.add_argument('--data-root', default=None,
                        help='where the live runtime lives, e.g. C:/Users/user/Desktop/'
                             'Milo Video Factory/ranking-shorts-pipeline (default: <repo>/data)')
    args = parser.parse_args()

    data_root = Path(args.data_root).expanduser() if args.data_root else ROOT / 'data'
    # The live runtime stores its files under <runtime_root>/data; accept both
    # the runtime root and the data dir itself.
    if not (data_root / 'ranking.db').exists() and (data_root / 'data' / 'ranking.db').exists():
        data_root = data_root / 'data'

    files = []
    env = ROOT / 'config' / '.env'
    db = data_root / 'ranking.db'
    plans = data_root / 'plans'

    if env.exists():
        files.append(env)
    if db.exists():
        files.append(db)
    files += sorted(plans.glob('*.json')) if plans.is_dir() else []

    files = [f for f in files if f.exists()]
    if not files:
        print("Nothing to bundle. Point --data-root at the live runtime dir.")
        return 1

    total = 0
    with tarfile.open(args.out, 'w:gz') as tar:
        for f in files:
            if f.parent == env.parent:
                arcname = 'config/.env'
            elif f == db:
                arcname = 'data/ranking.db'
            else:
                arcname = 'data/plans/' + f.name
            tar.add(f, arcname=arcname)
            total += f.stat().st_size
            print(f"  + {arcname}")

    size_mb = os.path.getsize(args.out) / (1024 * 1024)
    print(f"\nBundle written: {args.out} ({size_mb:.1f} MB, {total // 1024} KB unpacked)")
    print("Copy to the VPS, then run deploy/setup_vps.ps1 -Bundle <path>")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())