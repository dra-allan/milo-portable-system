"""Campaign clips may only ever land on the campaign-posting channels.

The guard matters because the niche map still points the biggest campaign
categories (gambling, gaming, sports) at chop_ug, which the ranking pipeline
owns. Without the guard those campaigns would post there; with it they are
skipped and reported.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config  # noqa: E402


class TestChannelResolution(unittest.TestCase):
    def test_niche_map_wins_when_no_explicit_channel(self):
        self.assertEqual(config.resolve_channel(niche='finance'),
                         'capital_mindset')

    def test_explicit_channel_beats_niche_map(self):
        self.assertEqual(config.resolve_channel(upload_channel='NXS',
                                                niche='finance'), 'NXS')

    def test_ranking_channel_niche_is_skipped(self):
        """gambling -> chop_ug, which is reserved for the ranking pipeline."""
        self.assertEqual(config.resolve_channel(niche='gambling'), '')

    def test_explicit_ranking_channel_is_also_skipped(self):
        self.assertEqual(config.resolve_channel(upload_channel='chop_ug'), '')

    def test_unknown_niche_falls_back_to_catch_all(self):
        self.assertEqual(config.resolve_channel(niche='luganda'),
                         'flick_shorts')

    def test_every_resolved_channel_is_a_campaign_channel(self):
        for niche in list(config.channel_map) + ['', 'nonsense']:
            resolved = config.resolve_channel(niche=niche)
            if resolved:
                self.assertIn(resolved, config.campaign_channels)


if __name__ == '__main__':
    unittest.main()
