#!/usr/bin/env python3
"""Build a ranking-style 'Others vs This Guy' contrast video.

Thin wrapper over ``python -m src.main --variant contrast``. The contrast copy
now lives in the pipeline itself, so the labels are burned into the render
instead of being patched into the plan after the video was already stitched
with ordinary ranking titles.

Topic comes from RANKING_CONTRAST_TOPIC, else the least-recently-run topic in
ranking.yaml (it used to always take the first entry, which is why every
contrast build was fishing). Set CONTRAST_SUBJECT=DOG/PRO/whatever for the
punchline, and RANKING_VIDEOS_PER_RUN for more than one.
"""
from __future__ import annotations
import os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from src.main import main as pipeline_main  # noqa: E402


def main() -> int:
    argv = ['--variant', 'contrast', '--no-upload']
    topic = (os.getenv('RANKING_CONTRAST_TOPIC') or '').strip()
    videos = (os.getenv('RANKING_VIDEOS_PER_RUN') or '').strip()
    if topic and topic.lower() != 'auto':
        argv += ['--mode', 'once', '--topic', topic]
    else:
        argv += ['--mode', 'auto', '--videos', videos if videos.isdigit() else '1']
    return pipeline_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
