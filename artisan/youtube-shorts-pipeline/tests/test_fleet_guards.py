"""Regression tests for the 2026-08-24 fleet-audit fixes.

Covers: title quality gate, per-channel cap overrides, machine lanes, and the
suppression state file. These encode the capital_mindset incident: a channel
flooded to 15-21 uploads/day got suppressed to 0-view uploads for 13 days
while nothing read views back.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.title_quality import clean_full_title, clean_hook, title_is_spammy


class TitleQualityTests(unittest.TestCase):
    def test_dangling_conjunction_is_stripped(self):
        # Real suppressed-channel title from the 2026-08-24 audit.
        self.assertEqual(
            clean_hook('WHEN I FIRST MOVED TO SAN FRANCISCO AND I?'),
            'WHEN I FIRST MOVED TO SAN FRANCISCO')

    def test_sentence_boundary_cut(self):
        hook = ("marketing or sales, marketing is more valuable. And then "
                "hard work adds up over time for everyone involved here")
        self.assertEqual(clean_hook(hook),
                         'marketing or sales, marketing is more valuable.')

    def test_no_mid_word_cut(self):
        long = 'word ' * 40
        cleaned = clean_hook(long)
        self.assertLessEqual(len(cleaned), 71)
        self.assertTrue(cleaned.split()[-1])

    def test_hashtags_never_chopped(self):
        base = 'a very long title that keeps going and going well past any sane limit for sure'
        full = f'{base} #niche #Shorts'
        out = clean_full_title(full)
        self.assertIn('#Shorts', out)
        self.assertTrue(out.endswith('#Shorts'))
        self.assertEqual(len(out.split()[-1]), len('#Shorts'))

    def test_spammy_lint_flags_dangling(self):
        reasons = title_is_spammy('SOMETHING AND')
        self.assertTrue(any(r.startswith('ends_dangling') for r in reasons))
        self.assertEqual(title_is_spammy('A perfectly fine title'), [])


class CapOverrideAndLaneTests(unittest.TestCase):
    def _make(self, env):
        """Build a fresh Config with *env* applied over a neutral base."""
        from src.config import Config
        saved = {k: os.environ.get(k) for k in env}
        saved['PIPELINE_LANES'] = os.environ.get('PIPELINE_LANES')
        saved['UPLOAD_CAP_OVERRIDES'] = os.environ.get('UPLOAD_CAP_OVERRIDES')
        for key, value in env.items():
            os.environ[key] = value

        @self.addCleanup
        def _restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return Config()

    def test_channel_cap_override(self):
        cfg = self._make({'UPLOAD_CAP_OVERRIDES': 'capital_mindset=4',
                          'UPLOAD_MAX_PER_CHANNEL': '6'})
        self.assertEqual(cfg.channel_cap('capital_mindset'), 4)
        self.assertEqual(cfg.channel_cap('CAPITAL_MINDSET'), 4)
        self.assertEqual(cfg.channel_cap('chop_ug'), 6)

    def test_bad_override_ignored(self):
        cfg = self._make({'UPLOAD_CAP_OVERRIDES': 'garbage,cap=abc,chop_ug=2'})
        self.assertNotIn('cap', cfg.upload_cap_overrides)
        self.assertEqual(cfg.upload_cap_overrides.get('chop_ug'), 2)

    def test_lane_allows(self):
        cfg = self._make({'PIPELINE_LANES': 'capital_mindset,chop_ug'})
        self.assertTrue(cfg.lane_allows('Capital_Mindset'))
        self.assertFalse(cfg.lane_allows('rankdrop'))
        self.assertFalse(cfg.lane_allows(''))

    def test_empty_lanes_allows_all(self):
        cfg = self._make({'PIPELINE_LANES': ''})
        self.assertTrue(cfg.lane_allows('anything'))


class SuppressionStateTests(unittest.TestCase):
    def _fresh(self, monkey_tmp: Path):
        import importlib
        from src import suppression as sup
        importlib.reload(sup)
        state = monkey_tmp / 'data' / 'suppressed_channels.yaml'

        original = sup._state_file
        sup._state_file = lambda: state
        self.addCleanup(lambda: setattr(sup, '_state_file', original))
        return sup

    def test_mark_check_clear_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sup = self._fresh(Path(td))
            self.assertFalse(sup.is_suppressed('chop_ug'))
            sup.mark_suppressed('Chop UG', median_views=3, sample_size=10,
                                threshold=15)
            self.assertTrue(sup.is_suppressed('chop_ug'))
            self.assertTrue(sup.is_suppressed('CHOP_UG'))
            sup.mark_healthy('chop_ug')
            self.assertFalse(sup.is_suppressed('chop_ug'))

    def test_expired_entry_not_suppressed(self):
        import tempfile
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as td:
            sup = self._fresh(Path(td))
            sup.mark_suppressed('x_chan', 1, 5, 15)
            # Backdate beyond the TTL by rewriting the entry.
            data = sup._load()
            old = (datetime.now() - timedelta(days=9)).isoformat()
            data['channels']['x_chan']['since'] = old
            sup._save(data)
            self.assertFalse(sup.is_suppressed('x_chan'))


if __name__ == '__main__':
    unittest.main()
