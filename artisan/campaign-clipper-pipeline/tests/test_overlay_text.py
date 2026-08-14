"""Text sheet rendering and filtergraph shape.

The hostile-string case is the whole reason text goes through Pillow instead of
drawtext. With drawtext that string either aborts the filtergraph or draws
nothing while still exiting zero, so it has to produce countable pixels here.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import overlay as ov  # noqa: E402
from src.config import config  # noqa: E402
from src.utils import quote_filter_path  # noqa: E402

try:
    from PIL import Image as PILImage
    HAVE_PIL = True
except ImportError:
    PILImage = None
    HAVE_PIL = False

try:
    config.resolve_font()
    HAVE_FONT = True
except Exception:
    HAVE_FONT = False

HOSTILE = "THAT'S 100% WILD, BUDDY: PART [2] 50%OFF"


@unittest.skipUnless(HAVE_PIL and HAVE_FONT, 'needs Pillow and a font')
class TestTextSheet(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='sheet-'))

    def test_hostile_string_renders_pixels(self):
        path = ov.text_sheet(HOSTILE, self.dir / 'hostile.png')
        self.assertIsNotNone(path)
        self.assertGreater(ov.sheet_ink(path), 500)

    def test_empty_text_returns_none(self):
        self.assertIsNone(ov.text_sheet('   ', self.dir / 'empty.png'))

    def test_long_text_still_renders(self):
        long_text = 'THIS IS A VERY LONG HOOK ' * 6
        path = ov.text_sheet(long_text, self.dir / 'long.png')
        self.assertGreater(ov.sheet_ink(path), 500)

    def test_sheet_is_frame_sized(self):
        path = ov.text_sheet('HELLO', self.dir / 'hello.png')
        with PILImage.open(path) as image:
            self.assertEqual(image.size, (config.width, config.height))


class TestChains(unittest.TestCase):
    def test_fill_chain_ends_on_out_label(self):
        chains = ov.fill_chain('0:v', 'out')
        self.assertTrue(chains[-1].endswith('[out]'))

    def test_sheet_chain_with_no_sheets_is_a_passthrough(self):
        self.assertEqual(ov.sheet_chain('a', 'b', []), ['[a]null[b]'])

    def test_logo_chain_positions(self):
        chains = ov.logo_chain('a', 'b', '/tmp/logo.png',
                              position='bottom-left')
        joined = ' '.join(chains)
        self.assertIn('overlay=', joined)
        self.assertIn('H-h-', joined)

    def test_logo_chain_forces_rgba_before_scale(self):
        # Without format=rgba a palettised or opaque source carries no alpha for
        # the opacity mixer to act on.
        joined = ' '.join(ov.logo_chain('a', 'b', '/tmp/logo.png'))
        self.assertIn('format=rgba', joined)


class TestPathQuoting(unittest.TestCase):
    def test_windows_drive_colon_is_escaped(self):
        # A bare C:/ breaks the filter option parser. The verified form is single
        # quotes plus a backslash-escaped colon.
        self.assertEqual(quote_filter_path('C:/fonts/impact.ttf'),
                         "'C\\:/fonts/impact.ttf'")

    def test_backslashes_are_normalised(self):
        self.assertEqual(quote_filter_path('C:\\fonts\\impact.ttf'),
                         "'C\\:/fonts/impact.ttf'")


if __name__ == '__main__':
    unittest.main()
