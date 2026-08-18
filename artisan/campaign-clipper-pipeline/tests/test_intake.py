"""Campaign intake gating: freshness, eligibility walls, platform match.

No browser and no network.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import intake  # noqa: E402

REJECT = ['min followers', 'min views', 'min engagement']


def page(obligations=(), prohibitions=(), unknown=()):
    return {'obligations': list(obligations),
            'prohibitions': list(prohibitions),
            'unknown_marks': list(unknown),
            'requirements': '\n'.join(list(obligations) + list(prohibitions)),
            'card': {}}


class TestFreshness(unittest.TestCase):
    def test_low_progress_is_fresh(self):
        self.assertTrue(intake.is_fresh({'progress': 12}, 20))

    def test_high_progress_is_not_fresh(self):
        self.assertFalse(intake.is_fresh({'progress': 47}, 20))

    def test_boundary_is_not_fresh(self):
        self.assertFalse(intake.is_fresh({'progress': 20}, 20))

    def test_missing_progress_is_treated_as_fresh(self):
        self.assertTrue(intake.is_fresh({}, 20))

    def test_unparseable_progress_is_treated_as_fresh(self):
        self.assertTrue(intake.is_fresh({'progress': 'n/a'}, 20))


class TestEligibilityGate(unittest.TestCase):
    def test_rejects_follower_gate(self):
        verdict = intake.gate_campaign(
            page(['Min Followers per Social Profile: 1000']), REJECT)
        self.assertFalse(verdict['ok'])
        self.assertIn('min followers', verdict['reasons'])

    def test_rejects_min_views_gate(self):
        verdict = intake.gate_campaign(
            page(['Min Views for Earnings: 3000']), REJECT)
        self.assertFalse(verdict['ok'])
        self.assertIn('min views', verdict['reasons'])

    def test_gate_in_a_prohibition_row_still_rejects(self):
        verdict = intake.gate_campaign(
            page(prohibitions=['Accounts under 1000 min subscribers']),
            REJECT + ['min subscribers'])
        self.assertFalse(verdict['ok'])

    def test_gate_in_an_unknown_mark_still_rejects(self):
        """Ambiguous colour rows must not smuggle a gate past intake."""
        verdict = intake.gate_campaign(
            page(unknown=['MIN FOLLOWERS: 5000']), REJECT)
        self.assertFalse(verdict['ok'])

    def test_clean_campaign_passes(self):
        verdict = intake.gate_campaign(
            page(['Post 1 clip per day', 'MUST MENTION ROOBET'],
                 ['NO SPAM']), REJECT)
        self.assertTrue(verdict['ok'])
        self.assertEqual(verdict['reasons'], [])

    def test_platform_mismatch_rejects(self):
        verdict = intake.gate_campaign(page(['TikTok only']), REJECT,
                                       card={'platforms': ['tiktok']})
        self.assertFalse(verdict['ok'])
        self.assertTrue(any('platform' in r for r in verdict['reasons']))

    def test_youtube_card_passes_platform_check(self):
        verdict = intake.gate_campaign(page(['Post daily']), REJECT,
                                       card={'platforms': ['youtube']})
        self.assertTrue(verdict['ok'])

    def test_missing_platform_list_is_not_a_rejection(self):
        verdict = intake.gate_campaign(page(['Post daily']), REJECT, card={})
        self.assertTrue(verdict['ok'])


class TestReport(unittest.TestCase):
    def test_describe_counts_every_bucket(self):
        report = intake.IntakeReport()
        report.rejected.append({'id': 'a', 'reasons': ['min followers']})
        text = report.describe()
        for part in ('added=0', 'rejected=1', 'skipped=0',
                     'waiting_content=0'):
            self.assertIn(part, text)


if __name__ == '__main__':
    unittest.main()
