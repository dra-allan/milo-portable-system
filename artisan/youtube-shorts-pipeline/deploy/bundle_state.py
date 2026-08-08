#!/usr/bin/env python3
"""
Bundle the runtime state needed to run the pipeline on a fresh VPS.

Run this ON THE OLD MACHINE (Windows) before migrating. It packs everything
that is not in git (or that must travel to be useful on a fresh box) into a
single .tar.gz you scp to the VPS:

  - config/youtube_token*.json     per-channel OAuth tokens (must move)
  - config/niches.yaml             niche config
  - config/.env                    live env (paths will need editing)
  - credentials.json               OAuth client secrets (must move)
  - data/processed_videos.db       the SQLite DB: dedup + stats history
  - data/library.json              downloaded-source index
  - data/transcripts/              cached transcripts (skip re-transcribing)
  - data/clip_plans/               cached clip plans (skip re-detection)

It deliberately does NOT bundle venv/, data/shorts (regenerable), data/temp
(regenerable) or the git repo (clone fresh on the VPS).

Usage:
    python deploy/bundle_state.py [--out state_bundle.tar.gz]
"""

import argparse
import tarfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='state_bundle.tar.gz',
                        help='output filename (default: state_bundle.tar.gz)')
    args = parser.parse_args()

    config_dir = ROOT / 'config'
    data_dir = ROOT / 'data'

    files = []
    files += sorted(config_dir.glob('youtube_token_*.json'))
    files += [config_dir / 'niches.yaml', config_dir / '.env']
    if (ROOT / 'credentials.json').exists():
        files.append(ROOT / 'credentials.json')
    if (data_dir / 'processed_videos.db').exists():
        files.append(data_dir / 'processed_videos.db')
    if (data_dir / 'library.json').exists():
        files.append(data_dir / 'library.json')
    files += sorted((data_dir / 'transcripts').glob('*.json')) if (data_dir / 'transcripts').exists() else []
    files += sorted((data_dir / 'clip_plans').glob('*.json')) if (data_dir / 'clip_plans').exists() else []

    files = [f for f in files if f.exists()]
    if not files:
        print("Nothing to bundle. Is this the pipeline root? "
              "Expected config/youtube_token*.json and data/processed_videos.db.")
        return 1

    total = 0
    with tarfile.open(args.out, 'w:gz') as tar:
        for f in files:
            tar.add(f, arcname=_rel(f))
            total += f.stat().st_size
            print(f"  + {_rel(f)}")

    size_mb = os.path.getsize(args.out) / (1024 * 1024)
    print(f"\nBundle written: {args.out} ({size_mb:.1f} MB, {total // 1024} KB unpacked)")
    print("Copy to the VPS with:\n"
          f"  scp {args.out} user@vps:/tmp/\n")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
