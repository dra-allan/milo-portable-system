"""Tests for scheduled mode wiring: run_niche gate, sweep budget, discover dry-run."""

import contextlib
import io
import logging
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


def _close_log_file_handlers():
    """Release file handles held by module loggers (Windows blocks unlink)."""
    for name in list(logging.Logger.manager.loggerDict):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                logger.removeHandler(handler)


@contextlib.contextmanager
def _workspace():
    """Temp dir that survives src.main's FileHandler lock on Windows."""
    td = tempfile.TemporaryDirectory()
    try:
        yield Path(td.name)
    finally:
        _close_log_file_handlers()
        td.cleanup()


class FakeDownloader:
    def __init__(self, results=None):
        self.results = results or []
        self.calls = []

    def search_videos_by_channel(self, channel_id, published_after='', max_results=10):
        self.calls.append({'channel': channel_id, 'max_results': max_results})
        return [r for r in self.results if r['channel_id'] == channel_id][:max_results]


class FakeDB:
    def __init__(self, processed_ids=()):
        self.processed = set(processed_ids)
        self.recorded = []

    def is_video_processed(self, video_id):
        return video_id in self.processed

    def record_video(self, video_id, title, niche, duration=0,
                     channel_id='', published_at=None):
        self.recorded.append((video_id, niche))


class FakeProcessor:
    def find_highlight_segments(self, *a, **k):
        return []


def _make_pipeline(td, authed_channels=('flick_shorts',)):
    from src.config import config
    from src.main import ShortsPipeline

    config.niches = {
        'flick_shorts': {'channels': ['@ch1'], 'channel': 'flick_shorts'},
        'capital_mindset': {'channels': ['@ch2'], 'channel': 'capital_mindset'},
        'unbound': {'channels': ['@ch3']},
    }
    config.authenticated_channels = lambda: list(authed_channels)

    pipeline = ShortsPipeline.__new__(ShortsPipeline)
    pipeline.config = config
    pipeline.processor = FakeProcessor()
    pipeline.db = FakeDB()
    pipeline._uploaders = {}
    pipeline.upload_enabled = False
    pipeline.transcript_dir = Path(td) / 'transcripts'
    pipeline.transcript_dir.mkdir(parents=True, exist_ok=True)
    pipeline.clip_plan_dir = Path(td) / 'clip_plans'
    pipeline.clip_plan_dir.mkdir(parents=True, exist_ok=True)
    pipeline.stats = {'videos_processed': 0, 'shorts_created': 0,
                      'shorts_uploaded': 0, 'errors': 0}
    pipeline._whisper_model = 'tiny'
    pipeline._downloader = FakeDownloader([
        {'id': 'hhh88888888', 'title': 'Pick me', 'duration': 900, 'channel_id': '@ch1'},
        {'id': 'iii99999999', 'title': 'Pick me too', 'duration': 900, 'channel_id': '@ch2'},
    ])
    return pipeline


class TestRunNicheGate(unittest.TestCase):
    def test_unbound_niche_skipped_returns_zero(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            pipeline = _make_pipeline(Path(td))
            seen = []
            pipeline.process_video_for_shorts = lambda vid, niche, force=False, local_only=False: (seen.append(vid) or True)

            started = pipeline.run_niche('unbound', max_videos=1)

            self.assertEqual(started, 0)
            self.assertEqual(seen, [])

    def test_bound_niche_processes_top_candidate(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            pipeline = _make_pipeline(Path(td))
            seen = []
            pipeline.process_video_for_shorts = lambda vid, niche, force=False, local_only=False: (seen.append(vid) or True)

            started = pipeline.run_niche('flick_shorts', max_videos=1)

            self.assertEqual(seen, ['hhh88888888'])
            self.assertEqual(started, 1)


class TestScheduledSweepBudget(unittest.TestCase):
    def test_sweep_stops_after_global_budget(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.main import _run_scheduled_sweep

            config.niches = {
                'flick_shorts': {'channels': ['@ch1'], 'channel': 'flick_shorts'},
                'capital_mindset': {'channels': ['@ch2'], 'channel': 'capital_mindset'},
                'unbound': {'channels': ['@ch0']},
            }
            config.schedule_max_videos = 1
            calls = []

            class FakePipeline:
                def run_niche(self, niche, max_videos=1, lookback=None):
                    calls.append(niche)
                    return 1

            class Args:
                niche = None
                videos = 5

            _run_scheduled_sweep(FakePipeline(), Args())

            self.assertEqual(calls, ['capital_mindset'])

    def test_sweep_skips_unbound_through_run_niche(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.main import _run_scheduled_sweep

            config.schedule_max_videos = 3
            pipeline = _make_pipeline(Path(td), authed_channels=('flick_shorts', 'capital_mindset'))
            processed = []
            pipeline.process_video_for_shorts = lambda vid, niche, force=False, local_only=False: (processed.append((vid, niche)) or True)

            # Production run_niche is bound-gated: unbound niche starts 0 and
            # never calls process_video_for_shorts.
            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            processed_niches = [n for _v, n in processed]
            self.assertNotIn('unbound', processed_niches)
            self.assertEqual(sorted(processed_niches), ['capital_mindset', 'flick_shorts'])


class TestDiscoverDryRun(unittest.TestCase):
    def test_discover_prints_bound_niches_skips_unbound(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            pipeline = _make_pipeline(Path(td))
            from src.main import run_discover_mode

            class Args:
                niche = None

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_discover_mode(pipeline, Args())

            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn('hhh88888888', out)
            self.assertIn('unbound', out)
            self.assertIn('SKIPPED', out)


class TestDiscoveryConfig(unittest.TestCase):
    def test_discovery_config_keys_exist(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.config import config
            self.assertTrue(hasattr(config, 'discovery_lookback'))
            self.assertTrue(hasattr(config, 'schedule_max_videos'))
            self.assertGreaterEqual(config.discovery_lookback, config.schedule_max_videos)


if __name__ == '__main__':
    unittest.main()

