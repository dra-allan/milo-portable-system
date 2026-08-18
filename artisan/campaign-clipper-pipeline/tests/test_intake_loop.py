"""The intake loop itself: dedupe, per-campaign isolation, channel guard.

The board and the compiler are stubs, so this covers the orchestration and
nothing else. It is the part that decides whether one bad campaign page kills
the daily cycle.
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


class FakeGates:
    min_views_for_earnings = 0
    min_engagement_pct = 0.0


class FakeSources:
    def __init__(self, any_=True):
        self._any = any_

    def has_any(self):
        return self._any


class FakeSpec:
    def __init__(self, cid, niche='finance', has_content=True):
        self.id = cid
        self.name = cid
        self.niche = niche
        self.upload_channel = ''
        self.account_gates = FakeGates()
        self.sources = FakeSources(has_content)

    def to_dict(self):
        return {'campaign': {'id': self.id}}


class FakeBoard:
    def __init__(self, cards, pages, explode_on=''):
        self._cards = cards
        self._pages = pages
        self._explode_on = explode_on

    def list_campaigns(self, platform='youtube'):
        return self._cards

    def read_campaign(self, url):
        if url == self._explode_on:
            raise RuntimeError('selector blew up')
        return self._pages.get(url)


class FakeCompiler:
    def __init__(self, niche='finance', has_content=True):
        self.niche = niche
        self.has_content = has_content
        self.calls = 0

    def compile_to_file(self, raw, campaign_id='', name='', url='', card=None,
                        use_model=False):
        self.calls += 1
        return FakeSpec(campaign_id, self.niche, self.has_content), Path('x')


class FakeDB:
    def __init__(self, existing=()):
        self._existing = [{'url': u} for u in existing]
        self.written = []

    def campaigns(self):
        return list(self._existing)

    def upsert_campaign(self, cid, name, url, spec, requirements):
        self.written.append(cid)


def clean_page():
    return {'obligations': ['Post 1 clip per day'],
            'prohibitions': ['NO SPAM'], 'unknown_marks': [],
            'requirements': 'Post 1 clip per day',
            'card': {'platforms': ['youtube']}}


def gated_page():
    return {'obligations': ['Min Followers per Social Profile: 1000'],
            'prohibitions': [], 'unknown_marks': [],
            'requirements': 'gated', 'card': {'platforms': ['youtube']}}


REJECT = ['min followers', 'min views']


class TestIntakeLoop(unittest.TestCase):
    def test_clean_fresh_campaign_is_added_and_persisted(self):
        cards = [{'id': 'Alpha', 'name': 'Alpha', 'url': 'u1', 'progress': 5}]
        board = FakeBoard(cards, {'u1': clean_page()})
        db = FakeDB()
        report = intake.run(db, board=board, spec_compiler=FakeCompiler(),
                            reject=REJECT, seen_urls=set())
        self.assertEqual(len(report.added), 1)
        self.assertEqual(db.written, ['alpha'])

    def test_gated_campaign_is_rejected_and_never_written(self):
        cards = [{'id': 'Roobet', 'name': 'Roobet', 'url': 'u1',
                  'progress': 5}]
        board = FakeBoard(cards, {'u1': gated_page()})
        db = FakeDB()
        report = intake.run(db, board=board, spec_compiler=FakeCompiler(),
                            reject=REJECT, seen_urls=set())
        self.assertEqual(report.added, [])
        self.assertEqual(db.written, [])
        self.assertIn('min followers', report.rejected[0]['reasons'])

    def test_already_known_url_is_skipped_not_re_added(self):
        cards = [{'id': 'Alpha', 'name': 'Alpha', 'url': 'u1', 'progress': 5}]
        board = FakeBoard(cards, {'u1': clean_page()})
        db = FakeDB(existing=['u1'])
        report = intake.run(db, board=board, spec_compiler=FakeCompiler(),
                            reject=REJECT)
        self.assertEqual(report.added, [])
        self.assertEqual(report.skipped, ['alpha'])

    def test_duplicate_cards_in_one_scan_are_added_once(self):
        cards = [{'id': 'Alpha', 'name': 'Alpha', 'url': 'u1', 'progress': 5},
                 {'id': 'Alpha', 'name': 'Alpha', 'url': 'u1', 'progress': 5}]
        board = FakeBoard(cards, {'u1': clean_page()})
        report = intake.run(FakeDB(), board=board,
                            spec_compiler=FakeCompiler(), reject=REJECT,
                            seen_urls=set())
        self.assertEqual(len(report.added), 1)

    def test_one_exploding_page_does_not_stop_the_cycle(self):
        cards = [{'id': 'Bad', 'name': 'Bad', 'url': 'boom', 'progress': 1},
                 {'id': 'Good', 'name': 'Good', 'url': 'u2', 'progress': 1}]
        board = FakeBoard(cards, {'u2': clean_page()}, explode_on='boom')
        report = intake.run(FakeDB(), board=board,
                            spec_compiler=FakeCompiler(), reject=REJECT,
                            seen_urls=set())
        self.assertEqual(len(report.added), 1)
        self.assertEqual(len(report.rejected), 1)

    def test_board_failure_returns_a_clean_empty_report(self):
        class DeadBoard:
            def list_campaigns(self, platform='youtube'):
                raise RuntimeError('playwright missing')

        report = intake.run(FakeDB(), board=DeadBoard(),
                            spec_compiler=FakeCompiler(), reject=REJECT,
                            seen_urls=set())
        self.assertEqual(report.added, [])
        self.assertTrue(report.errors)

    def test_stale_campaign_is_rejected_before_the_page_is_opened(self):
        cards = [{'id': 'Old', 'name': 'Old', 'url': 'u1', 'progress': 95}]
        board = FakeBoard(cards, {})
        compiler = FakeCompiler()
        report = intake.run(FakeDB(), board=board, spec_compiler=compiler,
                            reject=REJECT, seen_urls=set())
        self.assertEqual(compiler.calls, 0)
        self.assertIn('progress', report.rejected[0]['reasons'][0])

    def test_ranking_channel_niche_is_rejected_not_uploaded(self):
        cards = [{'id': 'Slots', 'name': 'Slots', 'url': 'u1', 'progress': 5}]
        board = FakeBoard(cards, {'u1': clean_page()})
        db = FakeDB()
        report = intake.run(db, board=board,
                            spec_compiler=FakeCompiler(niche='gambling'),
                            reject=REJECT, seen_urls=set())
        self.assertEqual(report.added, [])
        self.assertEqual(db.written, [])
        self.assertIn('non-campaign channel',
                      report.rejected[0]['reasons'][0])

    def test_campaign_without_content_is_flagged_waiting(self):
        cards = [{'id': 'Alpha', 'name': 'Alpha', 'url': 'u1', 'progress': 5}]
        board = FakeBoard(cards, {'u1': clean_page()})
        report = intake.run(FakeDB(), board=board,
                            spec_compiler=FakeCompiler(has_content=False),
                            reject=REJECT, seen_urls=set())
        self.assertEqual(report.waiting_content, ['alpha'])


if __name__ == '__main__':
    unittest.main()
