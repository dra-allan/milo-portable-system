"""End-to-end caption burn verification (not part of the unit suite).

Creates a 6s 1080x1920 test source with audio, seeds a Whisper-style
transcript cache with word-level timestamps, and renders a 5s clip. Fails if
the output lacks the captions burn or the ASS is malformed.

Run: python -m tests.e2e_caption_burn
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['VIDEO_FACTORY_ROOT'] = tempfile.mkdtemp(prefix='clipper-e2e-')

from src import renderer  # noqa: E402
from src.config import config  # noqa: E402
from src.spec import CampaignSpec  # noqa: E402
from src.utils import probe_media  # noqa: E402


def _make_source(path: Path) -> None:
    import subprocess
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
           '-f', 'lavfi', '-i',
           'color=c=0x223344:s=1080x1920:d=6:r=30',
           '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
           '-shortest', '-c:v', 'libx264', '-preset', 'ultrafast',
           '-c:a', 'aac', str(path)]
    subprocess.run(cmd, check=True, timeout=120)


def _seed_transcript(source_path: Path) -> None:
    import hashlib
    with source_path.open('rb') as fh:
        digest = hashlib.sha256(str(source_path.stat().st_size).encode())
        digest.update(fh.read(1024 * 1024))
    identity = digest.hexdigest()[:32]
    words = []
    cursor = 0.0
    for token in ['wait', 'for', 'it', 'this', 'is', 'the', 'one']:
        words.append({'word': token, 'start': cursor, 'end': cursor + 0.35})
        cursor += 0.45
    payload = {'source': identity, 'v': 2,
               'segments': [{'text': 'wait for it this is the one',
                             'start': 0.0, 'end': cursor,
                             'confidence': -0.3, 'words': words}]}
    dest = config.data_dir / 'transcripts' / f'{identity}.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload), encoding='utf-8')
    print(f'seeded transcript {dest.name}')
    return identity


class TestE2ECaptionBurn(unittest.TestCase):
    def test_render_burns_captions(self):
        src_dir = config.campaign_source_dir('e2e')
        source = src_dir / 'test_source.mp4'
        _make_source(source)
        _seed_transcript(source)

        spec = CampaignSpec.from_dict({
            'campaign': {'id': 'e2e', 'name': 'E2E'},
            'sources': {'local_folders': [str(src_dir)]},
            'render': {'min_duration': 4, 'max_duration': 60,
                       'own_text_required': True, 'platforms': ['youtube']},
        })
        plan = {'fingerprint': _fingerprint(source),
                'source_name': source.name, 'source_path': str(source),
                'start': 0.0, 'duration': 5.0, 'has_audio': True}
        copy = {'overlay_text': 'WAIT FOR IT', 'highlight': '',
                'caption': 'e2e', 'caption_added': [], 'banned': []}
        report = renderer.render_clip(spec, plan, copy, logo_path=None,
                                      stamp_logo=False)
        self.assertIsNotNone(report)
        self.assertTrue(report['captions'],
                        'captions should be burned into the render')
        out = Path(report['path'])
        self.assertTrue(out.exists(), 'render output missing')
        media = probe_media(str(out))
        self.assertGreater(media['duration'], 0)
        print(f'RENDER_OK {out.name} captions=True '
              f'{media["width"]}x{media["height"]} {media["duration"]:.2f}s')


def _fingerprint(path: Path) -> str:
    import hashlib
    with path.open('rb') as fh:
        h = hashlib.sha256(str(path.stat().st_size).encode())
        h.update(fh.read(1024 * 1024))
        if path.stat().st_size > 2 * 1024 * 1024:
            fh.seek(-1024 * 1024, os.SEEK_END)
            h.update(fh.read(1024 * 1024))
    return h.hexdigest()[:32]


if __name__ == '__main__':
    unittest.main(verbosity=2)