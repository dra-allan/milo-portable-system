"""Tests for scheduled mode wiring: run_niche gate, sweep budget, discover dry-run."""

import contextlib
import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

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
        self.marked = []
        self.unuploaded = list(unuploaded)
        self.used_by_source = {}
        self.used_by_channel = {}

    def is_video_processed(self, video_id):
        return video_id in self.processed

    def record_video(self, video_id, title, niche, duration=0,
                     channel_id='', published_at=None):
        self.recorded.append((video_id, niche))

    def unuploaded_shorts(self, limit=50):
        # Only return clips that are still queued (not uploaded/expired)
        queued = [c for c in self.unuploaded 
                  if c.get('status', 'queued') == 'queued' 
                  and c.get('local_path')]
        return queued[:limit]

    def mark_short_uploaded(self, source_video_id, segment_index, youtube_short_id,
                            channel=''):
        self.marked.append((source_video_id, segment_index, channel))
        # Mark the clip as uploaded in the unuploaded list
        for c in self.unuploaded:
            if (c.get('source_video_id') == source_video_id and 
                c.get('segment_index') == segment_index):
                c['status'] = 'uploaded'
                c['youtube_short_id'] = youtube_short_id
                c['upload_channel'] = channel
                break
        # Update internal counters for per-source/per-channel caps
        if not hasattr(self, '_run_source_counts'):
            self._run_source_counts = {}
        if not hasattr(self, '_run_channel_counts'):
            self._run_channel_counts = {}
        self._run_source_counts[source_video_id] = self._run_source_counts.get(source_video_id, 0) + 1
        self._run_channel_counts[channel] = self._run_channel_counts.get(channel, 0) + 1

    def uploaded_count_for_source_since(self, source_video_id, hours=24):
        base = getattr(self, 'used_by_source', {}).get(source_video_id, 0)
        run = getattr(self, '_run_source_counts', {}).get(source_video_id, 0)
        return base + run

    def uploaded_count_for_channel_since(self, channel, hours=24):
        base = getattr(self, 'used_by_channel', {}).get(channel, 0)
        run = getattr(self, '_run_channel_counts', {}).get(channel, 0)
        return base + run

    def record_performance(self, *a, **k):
        pass

    def rendered_segment_indices(self, source_video_id):
        return set()

    def source_performance(self):
        return {}

    # New methods for queue health and backlog management
    def expire_stale_backlog(self, niche: str, ttl_days: int = 7) -> int:
        return 0

    def get_queue_health(self, niche: str) -> Dict:
        # Count queued clips per source for this niche
        source_counts = {}
        total = 0
        for clip in self.unuploaded:
            if (clip.get('niche') or '') == niche and clip.get('local_path'):
                if clip.get('status', 'queued') == 'queued':
                    src = clip['source_video_id']
                    source_counts[src] = source_counts.get(src, 0) + 1
                    total += 1
        max_source = max(source_counts.values()) if source_counts else 0
        top_share = max_source / total if total > 0 else 0.0
        return {
            'total_queued': total,
            'distinct_sources': len(source_counts),
            'eligible_clips': 0,
            'top_source_share': round(top_share, 2),
            'channel_remaining': 0,
            'capped_sources': [],
            'source_counts': source_counts,
        }

    def expire_stale_backlog(self, niche: str, ttl_days: int = 7) -> int:
        return 0

    def get_queued_clips_for_upload(self, niche: str, limit: int = 100) -> List[Dict]:
        clips = [c for c in self.unuploaded 
                 if (c.get('niche') or '') == niche 
                 and c.get('local_path')
                 and c.get('status', 'queued') == 'queued']
        # Fair source rotation: group by source, round-robin
        by_source = {}
        for c in clips:
            src = c['source_video_id']
            if src not in by_source:
                by_source[src] = []
            by_source[src].append(c)
        
        sources = sorted(by_source.keys(), key=lambda s: len(by_source[s]))
        result = []
        pointers = {s: 0 for s in sources}
        remaining = list(sources)
        
        while remaining and len(result) < limit:
            next_remaining = []
            for src in remaining:
                idx = pointers[src]
                if idx < len(by_source[src]):
                    result.append(by_source[src][idx])
                    pointers[src] += 1
                    if pointers[src] < len(by_source[src]):
                        next_remaining.append(src)
            remaining = next_remaining
            if len(result) >= limit:
                break
        return result[:limit]

    def count_queued_by_source(self, niche: str) -> Dict[str, int]:
        counts = {}
        for c in self.unuploaded:
            if (c.get('niche') or '') == niche:
                if c.get('status', 'queued') == 'queued':
                    src = c['source_video_id']
                    counts[src] = counts.get(src, 0) + 1
        return counts

    def update_clip_status(self, source_video_id: str, segment_index: int, status: str) -> bool:
        for c in self.unuploaded:
            if (c.get('source_video_id') == source_video_id and 
                c.get('segment_index') == segment_index):
                c['status'] = status
                return True
        return False

    def get_max_queued_per_source(self, niche: str) -> int:
        counts = self.count_queued_by_source(niche)
        return max(counts.values()) if counts else 0


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
            config.queue_target_total = 12
            config.queue_min_distinct_sources = 4
            config.queue_max_top_source_share = 0.50
            config.backlog_ttl_days = 7
            calls = []

        class FakePipeline:

            upload_enabled = False
            db = FakeDB()

            def run_niche(self, niche, max_videos=1, lookback=None):
                calls.append(niche)
                return 1

        class Args:
            niche = None
            videos = 5

        pipeline = FakePipeline()
        pipeline.config = config
        _run_scheduled_sweep(pipeline, Args())

        # Only one niche processed due to total budget
        self.assertEqual(len(calls), 1)

    def test_sweep_skips_unbound_through_run_niche(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.main import _run_scheduled_sweep

            config.schedule_max_videos = 3
            config.queue_target_total = 12
            config.queue_min_distinct_sources = 4
            config.queue_max_top_source_share = 0.50
            config.backlog_ttl_days = 7
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
    """Pull-once: sweeps drain existing clip supply, then run discovery if queue unhealthy."""

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
        # Queue health configs for testing
        config.queue_target_total = 12
        config.queue_min_distinct_sources = 4
        config.queue_max_top_source_share = 0.50
        config.backlog_ttl_days = 7

        pipeline = ShortsPipeline.__new__(ShortsPipeline)
        pipeline.config = config
        pipeline.upload_enabled = True
        pipeline.db = FakeDB(unuploaded=unuploaded)
        pipeline.stats = {'videos_processed': 0, 'shorts_created': 0,
                          'shorts_uploaded': 0, 'errors': 0}
        pipeline._uploaders = {}
        pipeline._uploader_for_channel = lambda ch: _FakeUploader()
        return pipeline

    def test_backlog_supply_drains_then_runs_discovery_if_queue_unhealthy(self):
        """Backlog drains first; if queue unhealthy, discovery runs."""
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

            # 2 clips uploaded from backlog
            self.assertEqual(pipeline.stats['shorts_uploaded'], 2)
            # Queue unhealthy (only 1 source, below min_distinct_sources=4) -> discovery runs
            self.assertEqual(pulled, ['flick_shorts'])

    def test_rich_backlog_means_no_pull_if_queue_healthy(self):
        """If queue is healthy (enough distinct sources AND total >= target), no discovery runs."""
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            from src.config import config
            config.queue_min_distinct_sources = 1
            config.queue_target_total = 3
            clips = []
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            for i in range(6):  # 6 clips, cap 3 -> 3 remain after drain
                clips.append({
                    'source_video_id': f'aaa111111{i}',
                    'segment_index': i + 1,
                    'local_path': str(Path(td) / 'clips' / f'{i:02d}.mp4'),
                    'niche': 'flick_shorts', 'title': f'Hook {i}',
                })
                Path(clips[-1]['local_path']).write_bytes(b'fake-mp4')

            pulled = []
            pipeline = self._make_pipeline_with_backlog(td, clips)
            # Override the cap set by _make_pipeline_with_backlog
            config.upload_max_per_run = 3
            pipeline.run_niche = lambda niche, max_videos=1, lookback=None: (pulled.append(niche) or 0)

            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            # 3 clips uploaded from backlog (cap 3), 3 remain in queue
            self.assertEqual(pipeline.stats['shorts_uploaded'], 3)
            # Queue healthy (3 remaining >= target 3, 6 distinct sources >= 1) -> no discovery
            self.assertEqual(pulled, [])

    def test_per_source_daily_cap_limits_backlog_drain(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            from src.config import config

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

            # 5 supplied but per-source cap stops at 3
            self.assertEqual(pipeline.stats['shorts_uploaded'], 3)
            # 2 clips from capped source remain, queue unhealthy -> discovery runs
            self.assertEqual(pulled, ['flick_shorts'])

    def test_per_source_cap_counts_already_uploaded(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            from src.config import config

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

            # 2 already uploaded, cap 3 -> only 1 more can go
            self.assertEqual(pipeline.stats['shorts_uploaded'], 1)
            # Queue has 1 remaining from capped source -> unhealthy -> discovery runs
            self.assertEqual(pulled, ['flick_shorts'])

    def test_per_channel_daily_cap_limits_backlog_drain(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            from src.config import config

            config.upload_max_per_channel = 5
            config.upload_max_per_source = 3
            clips = []
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            for i in range(3):
                clips.append({
                    'source_video_id': f'src{i}',
                    'segment_index': i + 1,
                    'local_path': str(Path(td) / 'clips' / f'{i:02d}.mp4'),
                    'niche': 'flick_shorts', 'title': f'Hook {i}',
                })
                Path(clips[-1]['local_path']).write_bytes(b'fake-mp4')

            pulled = []
            pipeline = self._make_pipeline_with_backlog(td, clips)
            pipeline.db.used_by_channel = {'flick_shorts': 4}
            pipeline.run_niche = lambda niche, max_videos=1, lookback=None: (pulled.append(niche) or 0)

            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            # Channel at 4/5, cap 5 -> only 1 more
            self.assertEqual(pipeline.stats['shorts_uploaded'], 1)
            # Queue has 2 remaining but channel capped -> unhealthy -> discovery runs
            self.assertEqual(pulled, ['flick_shorts'])

    def test_per_channel_cap_exhausted_skips_drain_entirely(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            from src.main import _run_scheduled_sweep
            from src.config import config

            config.upload_max_per_channel = 5
            config.upload_max_per_source = 3
            clips = [
                {'source_video_id': 'aaa11111111', 'segment_index': 1,
                 'local_path': str(Path(td) / 'clips' / '01.mp4'),
                 'niche': 'flick_shorts', 'title': 'Hook one'},
            ]
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            for c in clips:
                Path(c['local_path']).write_bytes(b'fake-mp4')

            pulled = []
            pipeline = self._make_pipeline_with_backlog(td, clips)
            pipeline.db.used_by_channel = {'flick_shorts': 5}
            pipeline.run_niche = lambda niche, max_videos=1, lookback=None: (pulled.append(niche) or 0)

            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            # Channel at 5/5, cap 5 -> 0 uploads
            self.assertEqual(pipeline.stats['shorts_uploaded'], 0)
            # Channel full, queue unhealthy -> discovery runs (but will also be capped)
            self.assertEqual(pulled, ['flick_shorts'])


class TestBacklogRoundRobin(unittest.TestCase):
    """Multi-channel backlog drain: clips round-robin across a niche's
    upload_channels so every bound channel posts and the same clip never
    lands on two channels."""

    def _pipeline(self, td, clips, authed=('capital_mindset', 'wealth_mindset'),
                  used_by_channel=None):
        from src.config import config
        from src.main import ShortsPipeline
        config.niches = {
            'capital_mindset': {
                'channels': ['@ch2'],
                'channel': 'capital_mindset',
                'upload_channels': ['capital_mindset', 'wealth_mindset'],
            },
        }
        config.authenticated_channels = lambda: list(authed)
        config.schedule_backlog_first = True
        config.upload_max_per_run = 5
        config.upload_max_per_channel = 5
        config.upload_max_per_source = 3
        config.upload_pacing_min = 0
        config.upload_pacing_max = 0
        config.queue_target_total = 12
        config.queue_min_distinct_sources = 4
        config.queue_max_top_source_share = 0.50
        config.backlog_ttl_days = 7

        pipeline = ShortsPipeline.__new__(ShortsPipeline)
        pipeline.config = config
        pipeline.upload_enabled = True
        pipeline.db = FakeDB(unuploaded=clips)
        if used_by_channel:
            pipeline.db.used_by_channel = dict(used_by_channel)
        pipeline.stats = {'videos_processed': 0, 'shorts_created': 0,
                          'shorts_uploaded': 0, 'errors': 0}
        pipeline._uploaders = {}
        pipeline._uploader_for_channel = lambda ch: _FakeUploader()
        pipeline.run_niche = lambda niche, max_videos=1, lookback=None: 0
        return pipeline

    def _clip(self, td, idx):
        path = str(Path(td) / 'clips' / f'{idx:02d}.mp4')
        Path(path).write_bytes(b'fake-mp4')
        return {'source_video_id': f'src{idx:02d}', 'segment_index': idx,
                'local_path': path, 'niche': 'capital_mindset', 'title': f'Hook {idx}'}

    def test_drain_round_robins_across_niche_channels(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            from src.main import _run_scheduled_sweep
            clips = [self._clip(td, i) for i in range(1, 5)]

            pipeline = self._pipeline(td, clips)
            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            self.assertEqual(pipeline.stats['shorts_uploaded'], 4)
            channels = [m[2] for m in pipeline.db.marked]
            self.assertEqual(sorted(channels),
                             ['capital_mindset', 'capital_mindset',
                              'wealth_mindset', 'wealth_mindset'])

    def test_one_channel_at_cap_does_not_starve_other(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            Path(td).joinpath('clips').mkdir(exist_ok=True)
            from src.main import _run_scheduled_sweep
            clips = [self._clip(td, i) for i in range(1, 4)]

            # capital_mindset already published its 5/day; wealth_mindset has
            # budget left, so the whole supply must flow to wealth_mindset.
            pipeline = self._pipeline(
                td, clips,
                used_by_channel={'capital_mindset': 5, 'wealth_mindset': 0},
            )
            _run_scheduled_sweep(pipeline, type('Args', (), {'niche': None})())

            self.assertEqual(pipeline.stats['shorts_uploaded'], 3)
            self.assertEqual([m[2] for m in pipeline.db.marked],
                             ['wealth_mindset'] * 3)


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


class TestAddChannelBind(unittest.TestCase):
    """Line-based niches.yaml binding must survive mid-file niches + comments."""

    def _bind(self, td, yaml_text, channel, niche):
        from src.config import config
        from src.add_channel import bind_channel_to_niche
        path = td / 'niches.yaml'
        path.write_text(yaml_text, encoding='utf-8')
        config.niches_file = path
        bind_channel_to_niche(channel, niche)
        return path.read_text(encoding='utf-8')

    def test_appends_to_existing_upload_channels_list(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            out = self._bind(
                td,
                'flick_shorts:\n  upload_channels:\n    - flick_shorts\n  max_videos: 1\n',
                'wealth_mindset', 'flick_shorts',
            )
            import yaml
            self.assertEqual(
                yaml.safe_load(out)['flick_shorts']['upload_channels'],
                ['flick_shorts', 'wealth_mindset'],
            )

    def test_mid_file_niche_only_gets_its_own_block(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            out = self._bind(
                td,
                'flick_shorts:\n  upload_channels:\n    - flick_shorts\n\n'
                'capital_mindset:\n  upload_channels:\n    - capital_mindset\n'
                '    - wealth_mindset\n\n'
                'future_tech_daily:\n  channels:\n    - "@OpenAI"\n',
                'my_new_channel', 'capital_mindset',
            )
            import yaml
            d = yaml.safe_load(out)
            self.assertEqual(d['capital_mindset']['upload_channels'],
                             ['capital_mindset', 'wealth_mindset', 'my_new_channel'])
            # Sibling niches must be untouched.
            self.assertEqual(d['flick_shorts']['upload_channels'], ['flick_shorts'])
            self.assertEqual(d['future_tech_daily']['channels'], ['@OpenAI'])

    def test_legacy_channel_only_gets_upload_channels_added(self):
        with _workspace() as td:
            _isolate_config(Path(td))
            out = self._bind(
                td,
                'capital_mindset:\n  channel: capital_mindset\n  max_videos: 2\n',
                'wealth_mindset', 'capital_mindset',
            )
            import yaml
            self.assertEqual(
                yaml.safe_load(out)['capital_mindset']['upload_channels'],
                ['wealth_mindset'],
            )


if __name__ == '__main__':
    unittest.main()

