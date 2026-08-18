"""The opencli submit bridge: command shape, classification, failure modes.

Nothing here launches a browser. What is tested is the part that breaks
silently: whether every command carries the bridge profile, whether a status
badge is read correctly, and whether an opencli error is ever mistaken for a
successful submission.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import opencli_bridge as bridge  # noqa: E402

CAMPAIGN = 'https://clipster.gg/campaign/abc'
VIDEO = 'https://www.youtube.com/shorts/xyz'


class TestCommandShape(unittest.TestCase):
    def setUp(self):
        self.steps = bridge.build_submit_steps('clipster', 'prof1', CAMPAIGN,
                                              VIDEO)

    def test_flow_opens_types_and_sends_in_order(self):
        joined = [' '.join(step) for step in self.steps]
        self.assertIn(CAMPAIGN, joined[0])
        order = [i for i, text in enumerate(joined)
                 if bridge.INPUT in text or bridge.SEND_BUTTON in text]
        self.assertEqual(order, sorted(order))
        self.assertTrue(any(VIDEO in text for text in joined))
        self.assertIn(bridge.SEND_BUTTON, joined[-1])

    def test_every_step_carries_the_bridge_profile(self):
        for step in self.steps:
            self.assertIn('--profile', step)
            self.assertIn('prof1', step)

    def test_every_step_targets_the_bound_session(self):
        for step in self.steps:
            self.assertIn('clipster', step)

    def test_check_command_targets_the_activity_page(self):
        cmd = bridge.build_check_command('clipster', 'prof1')
        self.assertTrue(any('activity/submissions' in part for part in cmd))


class TestClassifier(unittest.TestCase):
    def test_accepted_badges(self):
        for text in ('Submitted', 'Approved', 'Pending review'):
            self.assertEqual(bridge.classify_submission(text), 'submitted')

    def test_rejected_badges(self):
        for text in ('Ineligible', 'Rejected', 'Invalid link', 'Declined'):
            self.assertEqual(bridge.classify_submission(text), 'rejected')

    def test_empty_is_unknown_not_success(self):
        self.assertEqual(bridge.classify_submission(''), 'unknown')
        self.assertEqual(bridge.classify_submission(None), 'unknown')

    def test_ineligible_wins_over_the_word_submitted(self):
        """'Submitted - Ineligible' must never read as a success."""
        self.assertEqual(bridge.classify_submission('Submitted - Ineligible'),
                         'rejected')


class TestSubmitFailureModes(unittest.TestCase):
    def setUp(self):
        self.original = bridge.run_step

    def tearDown(self):
        bridge.run_step = self.original

    def test_dead_bridge_reports_error_not_success(self):
        bridge.run_step = lambda *a, **k: {'ok': False, 'stdout': '',
                                          'error': 'opencli not found'}
        result = bridge.submit(CAMPAIGN, VIDEO, session='s', profile='p')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'error')

    def test_bridge_error_midway_aborts_the_remaining_steps(self):
        calls = []

        def fake(step, timeout=0):
            calls.append(step)
            return {'ok': len(calls) < 2, 'stdout': '', 'error': 'boom'}

        bridge.run_step = fake
        result = bridge.submit(CAMPAIGN, VIDEO, session='s', profile='p')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(len(calls), 2)

    def test_successful_flow_reports_submitted(self):
        bridge.run_step = lambda *a, **k: {'ok': True, 'stdout': 'Submitted',
                                          'error': ''}
        result = bridge.submit(CAMPAIGN, VIDEO, session='s', profile='p')
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'submitted')

    def test_missing_profile_refuses_to_run(self):
        """Ambiguous bridge profiles make opencli pick a random Chrome."""
        bridge.run_step = lambda *a, **k: {'ok': True, 'stdout': 'Submitted',
                                          'error': ''}
        result = bridge.submit(CAMPAIGN, VIDEO, session='s', profile='')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'error')
        self.assertIn('profile', result['detail'].lower())


if __name__ == '__main__':
    unittest.main()
