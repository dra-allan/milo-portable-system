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
    def __init__(self, processed_ids=(), unuploaded=()):
        self.processed = set(processed_ids)
        self.recorded = []
        self.unuploaded = list(unuploaded)

    def is_video_processed(self, video_id):
        return video_id in self.processed

    def record_video(self, video_id, title, niche, duration=0,
                     channel_id='', published_at=None):
        self.recorded.append((video_id, niche))

    def unuploaded_shorts(self, limit=50):
        return self.unuploaded[:limit]

    def mark_short_uploaded(self, source_video_id, segment_index, youtube_short_id):
        pass

    def uploaded_count_for_source_since(self, source_video_id, hours=24):
        return getattr(self, 'used_by_source', {}).get(source_video_id, 0)

    def record_performance(self, *a, **k):
        pass

    def rendered_segment_indices(self, source_video_id):
        return set()

    def source_performance(self):
        return {}


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
            pipeline.process_video_for_shorts = lambda vid, niche, force=False, local_only=False, source_channel='': (seen.append(vid) or True)

            started = pipeline.run_niche('unbound', max_videos=1)

            self.assertEqual(started, 0)
            self.assertEqual(seen, [])

    def test_bound_niche_processes_top_candidate(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            pipeline = _make_pipeline(Path(td))
            seen = []
            pipeline.process_video_for_shorts = lambda vid, niche, force=False, local_only=False, source_channel='': (seen.append(vid) or True)

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
            config.schedule_max_total = 1
            config.schedule_backlog_first = False
            calls = []

            class FakePipeline:
                upload_enabled = False

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
            config.schedule_backlog_first = False
            pipeline = _make_pipeline(Path(td), authed_channels=('flick_shorts', 'capital_mindset'))
            processed = []
            pipeline.process_video_for_shorts = lambda vid, niche, force=False, local_only=False, source_channel='': (processed.append((vid, niche)) or True)

            # Production run_niche is bound-gated: unbound niche starts 0 and
            # never calls process_video_for_shorts.
            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            processed_niches = [n for _v, n in processed]
            self.assertNotIn('unbound', processed_niches)
            self.assertEqual(sorted(processed_niches), ['capital_mindset', 'flick_shorts'])


class TestPullOnceBacklog(unittest.TestCase):
    """Pull-once: sweeps drain existing clip supply instead of pulling again."""

    def _make_pipeline_with_backlog(self, td, unuploaded):
        from src.config import config
        from src.main import ShortsPipeline
        config.niches = {
            'flick_shorts': {'channels': ['@ch1'], 'channel': 'flick_shorts'},
        }
        config.authenticated_channels = lambda: ['flick_shorts']
        config.schedule_backlog_first = True
        config.upload_max_per_run = 5
        config.upload_pacing_min = 0
        config.upload_pacing_max = 0

        pipeline = ShortsPipeline.__new__(ShortsPipeline)
        pipeline.config = config
        pipeline.upload_enabled = True
        pipeline.db = FakeDB(unuploaded=unuploaded)
        pipeline.stats = {'videos_processed': 0, 'shorts_created': 0,
                          'shorts_uploaded': 0, 'errors': 0}
        pipeline._uploaders = {}
        pipeline._uploader_for_channel = lambda ch: _FakeUploader()
        return pipeline

    def test_backlog_supply_skips_pull_and_uploads_existing(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            clips = [
                {'source_video_id': 'aaa11111111', 'segment_index': 1,
                 'local_path': str(Path(td) / 'clips' / '01.mp4'),
                 'niche': 'flick_shorts', 'title': 'Hook one'},
                {'source_video_id': 'aaa11111111', 'segment_index': 2,
                 'local_path': str(Path(td) / 'clips' / '02.mp4'),
                 'niche': 'flick_shorts', 'title': 'Hook two'},
            ]
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            for c in clips:
                Path(c['local_path']).write_bytes(b'fake-mp4')

            pulled = []
            pipeline = self._make_pipeline_with_backlog(td, clips)
            pipeline.run_niche = lambda niche, max_videos=1, lookback=None: (pulled.append(niche) or 0)

            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            # 2 clips >= backlog_min(1): drain both, no pull.
            self.assertEqual(pulled, [])
            self.assertEqual(pipeline.stats['shorts_uploaded'], 2)

    def test_rich_backlog_means_no_pull(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            clips = []
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            for i in range(6):
                clips.append({
                    'source_video_id': f'aaa111111{i}',
                    'segment_index': i + 1,
                    'local_path': str(Path(td) / 'clips' / f'{i:02d}.mp4'),
                    'niche': 'flick_shorts', 'title': f'Hook {i}',
                })
                Path(clips[-1]['local_path']).write_bytes(b'fake-mp4')

            pulled = []
            pipeline = self._make_pipeline_with_backlog(td, clips)
            pipeline.run_niche = lambda niche, max_videos=1, lookback=None: (pulled.append(niche) or 0)

            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            # 6 clips >= backlog_min(5): drain, no pull.
            self.assertEqual(pulled, [])
            self.assertEqual(pipeline.stats['shorts_uploaded'], 5)

    def test_per_source_daily_cap_limits_backlog_drain(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            from src.config import config

            # 5 clips from the SAME source video -- the "How to Get Rich" burst
            # pattern. Only upload_max_per_source (3) may go up in one run.
            config.upload_max_per_source = 3
            clips = []
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            for i in range(5):
                clips.append({
                    'source_video_id': 'fj5uxdv_j5Y',
                    'segment_index': i + 1,
                    'local_path': str(Path(td) / 'clips' / f'{i:02d}.mp4'),
                    'niche': 'flick_shorts', 'title': f'Hook {i}',
                })
                Path(clips[-1]['local_path']).write_bytes(b'fake-mp4')

            pulled = []
            pipeline = self._make_pipeline_with_backlog(td, clips)
            pipeline.run_niche = lambda niche, max_videos=1, lookback=None: (pulled.append(niche) or 0)

            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            # 5 supplied but per-source cap stops at 3.
            self.assertEqual(pipeline.stats['shorts_uploaded'], 3)

    def test_per_source_cap_counts_already_uploaded(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            from src.config import config

            # 2 clips from this source were already posted in the last 24h, so
            # with a cap of 3 only 1 more may go up this run.
            config.upload_max_per_source = 3
            clips = [
                {'source_video_id': 'aaa11111111', 'segment_index': 1,
                 'local_path': str(Path(td) / 'clips' / '01.mp4'),
                 'niche': 'flick_shorts', 'title': 'Hook one'},
                {'source_video_id': 'aaa11111111', 'segment_index': 2,
                 'local_path': str(Path(td) / 'clips' / '02.mp4'),
                 'niche': 'flick_shorts', 'title': 'Hook two'},
            ]
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            for c in clips:
                Path(c['local_path']).write_bytes(b'fake-mp4')

            pulled = []
            pipeline = self._make_pipeline_with_backlog(td, clips)
            pipeline.db.used_by_source = {'aaa11111111': 2}
            pipeline.run_niche = lambda niche, max_videos=1, lookback=None: (pulled.append(niche) or 0)

            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            self.assertEqual(pipeline.stats['shorts_uploaded'], 1)


class _FakeUploader:
    def __init__(self):
        self.count = 0

    def upload_short(self, video_path, title, description, tags):
        self.count += 1
        return f'vid{self.count}'

    def fetch_statistics(self, short_id):
        return {'views': 1, 'likes': 0, 'comments': 0, 'favorites': 0}


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

