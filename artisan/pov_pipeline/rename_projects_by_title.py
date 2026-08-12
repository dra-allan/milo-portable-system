#!/usr/bin/env python3
"""Rename POV project folders from YouTube titles instead of opaque video IDs."""
from __future__ import annotations
import argparse, re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent

def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")[:80] or "POV"

def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--projects", default=None); ap.add_argument("--dry-run", action="store_true"); args = ap.parse_args()
    root = Path(args.projects or (HERE / "projects")).expanduser()
    if not root.exists(): return 0
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        url_file = project / "00_SOURCE_URL.txt"
        if not url_file.exists(): continue
        url = url_file.read_text(encoding="utf-8", errors="replace").strip()
        if not url: continue
        try:
            title = subprocess.check_output(["yt-dlp", "--print", "%(title)s", "--skip-download", url], text=True, stderr=subprocess.DEVNULL, timeout=60).strip()
        except Exception:
            continue
        target = root / f"{slug(title)}_{project.name.rsplit('_', 1)[-1]}"
        if target == project or target.exists(): continue
        print(f"{project.name} -> {target.name}")
        if not args.dry_run:
            project.rename(target)
            manifest = target / "state" / "manifest.json"
            if manifest.exists():
                text = manifest.read_text(encoding="utf-8", errors="replace")
                manifest.write_text(text.replace(f'"project": "{project.name}"', f'"project": "{target.name}"'), encoding="utf-8")
    return 0
if __name__ == "__main__": raise SystemExit(main())
