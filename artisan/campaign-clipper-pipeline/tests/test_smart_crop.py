"""Pure smart-crop geometry and fallback tests."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT', tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import smart_crop  # noqa: E402


class TestCropGeometry(unittest.TestCase):
    def test_widescreen_crop_is_even_and_in_bounds(self):
        x, y, w, h = smart_crop._clamp_crop(1700, 500, 1920, 1080, 1080 / 1920)
        self.assertEqual((x % 2, y % 2, w % 2, h % 2), (0, 0, 0, 0))
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 1920)
        self.assertLessEqual(y + h, 1080)

    def test_streamer_crop_tracks_right_side(self):
        crop = smart_crop._clamp_crop(1700, 380, 1920, 1080, 1080 / 1920, zoom=0.82)
        self.assertGreater(crop[0], 400)

    def test_pair_midpoint_is_valid(self):
        left = smart_crop.Person(300, 180, 240, 520, 0.9, 5)
        right = smart_crop.Person(1380, 170, 250, 530, 0.88, 5)
        cx = (left.x + right.x + right.w) / 2
        cy = min(left.y, right.y) + max(left.h, right.h) * 0.42
        crop = smart_crop._clamp_crop(cx, cy, 1920, 1080, 1080 / 1920)
        self.assertGreaterEqual(crop[0], 0)
        self.assertLessEqual(crop[0] + crop[2], 1920)


class TestFallback(unittest.TestCase):
    def test_disabled_returns_fallback_signal(self):
        old = os.environ.get('CLIPPER_SMART_CROP')
        os.environ['CLIPPER_SMART_CROP'] = 'false'
        try:
            self.assertIsNone(smart_crop.plan_crop('does-not-exist.mp4', 0, 10))
        finally:
            if old is None:
                os.environ.pop('CLIPPER_SMART_CROP', None)
            else:
                os.environ['CLIPPER_SMART_CROP'] = old


if __name__ == '__main__':
    unittest.main()
