"""Dead-channel cache: failed listings are remembered and skipped silently."""

import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolate_config(tmp: Path):
    os.environ['TEMP_DIR'] = str(tmp / 'temp')
    os.environ['DATA_DIR'] = str(tmp / 'data')
    os.environ['LOG_DIR'] = str(tmp / 'logs')
    os.environ['SHORTS_DIR'] = str(tmp / 'shorts')
    os.environ['DB_PATH'] = str(tmp / 'data' / 'test.db')


class DeadChannelCacheTests(unittest.TestCase):
    def _downloader(self, td):
        from src.downloader import YouTubeDownloader
        dl = YouTubeDownloader.__new__(YouTubeDownloader)
        dl.temp_dir = Path(td) / 'temp'
        dl.dead_channels_path = Path(td) / 'data' / 'dead_channels.json'
        dl.dead_channel_cooldown = 14
        dl._dead_channels = dl._load_dead_channels()
        return dl

    def test_failure_marks_channel_dead_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            dl = self._downloader(Path(td))

            # Simulate the real failure path directly.
            dl._mark_channel_dead('@DeadChannel')
            self.assertIn('@DeadChannel', dl._dead_channels)
            saved = json.loads(dl.dead_channels_path.read_text(encoding='utf-8'))
            self.assertIn('@DeadChannel', saved)

            # A second downloader (fresh instance) sees the same cache.
            dl2 = self._downloader(Path(td))
            self.assertTrue(dl2._channel_is_dead('@DeadChannel'))
            self.assertFalse(dl2._channel_is_dead('@LiveChannel'))

    def test_expired_cooldown_reprobes(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            dl = self._downloader(Path(td))
            dl._dead_channels['@OldDead'] = time.time() - (15 * 86400)
            dl._save_dead_channels()

            self.assertFalse(dl._channel_is_dead('@OldDead'))
            self.assertNotIn('@OldDead', dl._dead_channels)

    def test_success_clears_dead_entry(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            dl = self._downloader(Path(td))
            dl._dead_channels['@CameBack'] = time.time()
            dl._save_dead_channels()

            dl._dead_channels.pop('@CameBack', None)
            dl._save_dead_channels()

            dl2 = self._downloader(Path(td))
            self.assertFalse(dl2._channel_is_dead('@CameBack'))


class SearchVideosDeadGateTests(unittest.TestCase):
    """search_videos_by_channel must skip cached-dead channels before yt-dlp."""

    def test_dead_channel_skips_ytdlp_entirely(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.downloader import YouTubeDownloader
            dl = YouTubeDownloader.__new__(YouTubeDownloader)
            dl.temp_dir = Path(td) / 'temp'
            dl.dead_channels_path = Path(td) / 'data' / 'dead_channels.json'
            dl.dead_channel_cooldown = 14
            dl._dead_channels = {'@DeadChannel': time.time()}

            calls = []
            fake_yt = types.SimpleNamespace()

            class Boom:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def extract_info(self, *a, **k):
                    calls.append(1)
                    raise RuntimeError('should never be reached')

            fake_yt.YoutubeDL = lambda opts: Boom()
            old_mod = sys.modules.get('yt_dlp')
            sys.modules['yt_dlp'] = fake_yt
            try:
                results = dl.search_videos_by_channel('@DeadChannel')
            finally:
                if old_mod is not None:
                    sys.modules['yt_dlp'] = old_mod
                else:
                    del sys.modules['yt_dlp']

            self.assertEqual(results, [])
            self.assertEqual(calls, [], 'cached-dead channel must not hit yt-dlp')

    def test_live_channel_failure_gets_cached(self):
        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.downloader import YouTubeDownloader
            dl = YouTubeDownloader.__new__(YouTubeDownloader)
            dl.temp_dir = Path(td) / 'temp'
            dl.dead_channels_path = Path(td) / 'data' / 'dead_channels.json'
            dl.dead_channel_cooldown = 14
            dl._dead_channels = {}

            calls = []
            fake_yt = types.SimpleNamespace()

            class Boom:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def extract_info(self, *a, **k):
                    calls.append(1)
                    raise RuntimeError('This channel does not exist.')

            fake_yt.YoutubeDL = lambda opts: Boom()
            old_mod = sys.modules.get('yt_dlp')
            sys.modules['yt_dlp'] = fake_yt
            try:
                results = dl.search_videos_by_channel('@LiveButBroken')
                self.assertEqual(results, [])
                self.assertIn('@LiveButBroken', dl._dead_channels)

                # Second call: cache hit, must skip yt-dlp entirely.
                results2 = dl.search_videos_by_channel('@LiveButBroken')
                self.assertEqual(results2, [])
            finally:
                if old_mod is not None:
                    sys.modules['yt_dlp'] = old_mod
                else:
                    del sys.modules['yt_dlp']

            self.assertEqual(len(calls), 1,
                             'only the first call may touch yt-dlp')


if __name__ == '__main__':
    unittest.main()
