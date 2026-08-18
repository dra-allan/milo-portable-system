"""Autopilot intake rules load from clipper.yaml as data, not code."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import config  # noqa: E402


class TestIntakeConfig(unittest.TestCase):
    def test_follower_gate_keywords_present(self):
        self.assertTrue(any('min followers' in k
                            for k in config.intake_reject_keywords))

    def test_view_and_engagement_gate_keywords_present(self):
        blob = ' | '.join(config.intake_reject_keywords)
        self.assertIn('min views', blob)
        self.assertIn('min engagement', blob)

    def test_keywords_are_lowercased(self):
        """Matching is done against a lowercased blob, so the list must be too."""
        for keyword in config.intake_reject_keywords:
            self.assertEqual(keyword, keyword.lower())

    def test_max_progress_default(self):
        self.assertEqual(config.intake_max_progress, 20.0)

    def test_campaign_channels_exclude_ranking_channels(self):
        self.assertIn('capital_mindset', config.campaign_channels)
        self.assertIn('NXS', config.campaign_channels)
        for reserved in ('chop_ug', 'rankdrop', 'the_other_guys',
                         'explaination'):
            self.assertNotIn(reserved, config.campaign_channels)


class TestOpencliConfig(unittest.TestCase):
    def test_session_default(self):
        self.assertEqual(config.opencli_session, 'clipster')

    def test_profile_is_not_hardcoded_to_one_machine(self):
        """A machine-specific bridge id must never be a committed default."""
        self.assertNotEqual(config.opencli_profile, 'g5f9qrts')


if __name__ == '__main__':
    unittest.main()
