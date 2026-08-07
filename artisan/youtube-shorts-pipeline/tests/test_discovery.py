"""Tests for scheduled discovery: dedup, filtering, and pick logic."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolate_config(tmp: Path):
    os.environ['TEMP_DIR'] = str(tmp / 'temp')
    os.environ['DATA_DIR'] = str(tmp / 'data')
    os.environ['LOG_DIR'] = str(tmp / 'logs')
    os.environ['SHORTS_DIR'] = str(tmp / 'shorts')
    os.environ['DB_PATH'] = str(tmp / 'data' / 'test.db')


class FakeDownloader:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search_videos_by_channel(self, channel_id, published_after='', max_results=10):
        self.calls.append({'channel': channel_id, 'max_results': max_results})
        return [r for r in self.results if r['channel_id'] == channel_id][:max_results]


class FakeDB:
    def __init__(self, processed_ids=()):
        self.processed = set(processed_ids)

    def is_video_processed(self, video_id):
        return video_id in self.processed


class TestDiscovery(unittest.TestCase):
    def test_skips_already_processed_videos(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.discovery import discover_candidates

            vids = [
                {'id': 'aaa11111111', 'title': 'Old one', 'duration': 3600,
                 'channel_id': '@ch1'},
                {'id': 'bbb22222222', 'title': 'Fresh one', 'duration': 1800,
                 'channel_id': '@ch1'},
            ]
            dl = FakeDownloader(vids)
            db = FakeDB(processed_ids=['aaa11111111'])
            config.niches = {'test_niche': {'channels': ['@ch1']}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            ids = [c['id'] for c in result.candidates]
            self.assertNotIn('aaa11111111', ids)
            self.assertIn('bbb22222222', ids)
            self.assertEqual(result.skipped_already_processed, ['aaa11111111'])

    def test_filters_by_duration_band(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.discovery import discover_candidates

            vids = [
                {'id': 'ccc33333333', 'title': 'Too short', 'duration': 120,
                 'channel_id': '@ch1'},
                {'id': 'ddd44444444', 'title': 'Just right', 'duration': 900,
                 'channel_id': '@ch1'},
                {'id': 'eee55555555', 'title': 'Too long', 'duration': 9000,
                 'channel_id': '@ch1'},
            ]
            dl = FakeDownloader(vids)
            db = FakeDB()
            config.niches = {'test_niche': {'channels': ['@ch1'],
                                            'min_duration': 300, 'max_duration': 7200}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            ids = [c['id'] for c in result.candidates]
            self.assertEqual(ids, ['ddd44444444'])

    def test_filters_by_negative_keywords(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.discovery import discover_candidates

            vids = [
                {'id': 'fff66666666', 'title': 'Live stream: full show', 'duration': 3600,
                 'channel_id': '@ch1'},
                {'id': 'ggg77777777', 'title': 'The real interview', 'duration': 3600,
                 'channel_id': '@ch1'},
            ]
            dl = FakeDownloader(vids)
            db = FakeDB()
            config.niches = {'test_niche': {'channels': ['@ch1'],
                                            'negative_keywords': ['livestream', 'live stream', 'clip']}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            ids = [c['id'] for c in result.candidates]
            self.assertEqual(ids, ['ggg77777777'])

    def test_skips_placeholder_channels(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.discovery import discover_candidates

            dl = FakeDownloader([])
            db = FakeDB()
            config.niches = {'test_niche': {'channels': ['UCXXXXX', '@realchannel']}}

            result = discover_candidates(dl, db, 'test_niche', max_videos=5, lookback=10)

            queried = [c['channel'] for c in dl.calls]
            self.assertNotIn('UCXXXXX', queried)
            self.assertIn('@realchannel', queried)

    def test_returns_empty_when_niche_has_no_channels(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.discovery import discover_candidates

            dl = FakeDownloader([])
            db = FakeDB()
            config.niches = {'empty_niche': {}}

            result = discover_candidates(dl, db, 'empty_niche', max_videos=5, lookback=10)

            self.assertEqual(result.candidates, [])


if __name__ == '__main__':
    unittest.main()
