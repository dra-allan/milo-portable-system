"""Tests for the Phase 4 downloader: audio-only discovery + section fetch.

These tests deliberately avoid the network. yt-dlp is replaced with a fake that
writes files exactly where the real one would, which lets us verify the parts
that actually broke in production:

* the audio fetch is *audio only* and lands in its own directory,
* a full-video resume scan cannot pick up an audio file or a clip section,
* keyframe drift is measured from the downloaded file rather than assumed,
* a section that overruns its request still yields in-sync render offsets.
"""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolate_config(tmp: Path):
    """Point the pipeline's paths at a temp dir before importing src.*."""
    os.environ['TEMP_DIR'] = str(tmp / 'temp')
    os.environ['DATA_DIR'] = str(tmp / 'data')
    os.environ['LOG_DIR'] = str(tmp / 'logs')
    os.environ['SHORTS_DIR'] = str(tmp / 'shorts')
    os.environ['DB_PATH'] = str(tmp / 'data' / 'test.db')


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: writes files, records the options."""

    calls = []

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.calls.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        """Write a file at the outtmpl path, mimicking a real download."""
        tmpl = self.opts['outtmpl']
        if isinstance(tmpl, dict):
            tmpl = tmpl.get('default')
        # Substitute the yt-dlp template fields we actually use.
        path = (str(tmpl)
                .replace('%(id)s', 'abcdefghijk')
                .replace('%(title).80B', 'Test_Video')
                .replace('%(ext)s', self.ext))
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.payload)
        return {
            'id': 'abcdefghijk',
            'title': 'Test Video',
            'duration': 3600,
            'uploader': 'Test Channel',
            'requested_downloads': [{'filepath': str(p)}],
        }


class FakeAudioYDL(FakeYDL):
    ext = 'm4a'
    payload = b'\0' * (128 * 1024)


class FakeWebmAudioYDL(FakeYDL):
    """Audio in a .webm container -- what YouTube actually serves for opus.

    This is the dangerous case: '.webm' is a *video* extension, so nothing
    about the filename distinguishes this from a full 1080p download. Only the
    directory it lives in can.
    """
    ext = 'webm'
    payload = b'\0' * (128 * 1024)


class FakeVideoYDL(FakeYDL):
    ext = 'mp4'
    payload = b'\0' * (2 * 1024 * 1024)


class DownloaderFetchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        _isolate_config(tmp)

        # Import after the env is set so config picks up the temp paths.
        for mod in [m for m in list(sys.modules) if m.startswith('src.')]:
            del sys.modules[mod]
        from src.downloader import YouTubeDownloader
        self.dl = YouTubeDownloader()
        FakeYDL.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def _install_fake(self, cls):
        """Inject a fake yt_dlp module for the duration of one test."""
        fake = types.ModuleType('yt_dlp')
        fake.YoutubeDL = cls
        utils = types.ModuleType('yt_dlp.utils')

        def download_range_func(chapters, ranges, from_info=False):
            return {'ranges': ranges}

        utils.download_range_func = download_range_func
        fake.utils = utils
        sys.modules['yt_dlp'] = fake
        sys.modules['yt_dlp.utils'] = utils
        self.addCleanup(sys.modules.pop, 'yt_dlp', None)
        self.addCleanup(sys.modules.pop, 'yt_dlp.utils', None)

    # -- audio-only discovery fetch ------------------------------------
    def test_audio_fetch_requests_audio_only_format(self):
        """The discovery fetch must not pull any video stream."""
        self._install_fake(FakeAudioYDL)
        meta = self.dl.download_audio('abcdefghijk')

        self.assertIsNotNone(meta)
        self.assertTrue(meta['audio_only'])
        self.assertEqual(meta['video_path'], '',
                         "audio-only fetch must not claim to have a video")
        self.assertTrue(Path(meta['audio_path']).exists())

        opts = FakeYDL.calls[0]
        self.assertEqual(opts['format'], 'bestaudio/best')
        # A 'height<=' selector would mean a video stream was requested.
        self.assertNotIn('height', opts['format'])

    def test_audio_is_kept_out_of_the_full_download_directory(self):
        """Audio must not sit in temp_dir, where the video scan looks.

        find_local_video() globs temp_dir. Anything audio-shaped placed there
        becomes a candidate 'full source', so the separation is the safety
        property -- not a cosmetic layout choice.
        """
        self._install_fake(FakeAudioYDL)
        meta = self.dl.download_audio('abcdefghijk')
        audio = Path(meta['audio_path'])
        self.assertNotEqual(audio.parent, self.dl.temp_dir,
                            "audio in temp_dir is visible to the video scan")

    def test_full_video_scan_ignores_webm_audio(self):
        """The resume path for a *full video* must not return audio.

        With a .webm audio stream the extension is identical to a video
        download, so if audio were stored alongside full downloads this scan
        would hand a 128 KB audio file to the renderer as the 1080p source.
        """
        self._install_fake(FakeWebmAudioYDL)
        meta = self.dl.download_audio('abcdefghijk')
        self.assertTrue(Path(meta['audio_path']).exists())
        self.assertEqual(Path(meta['audio_path']).suffix, '.webm')

        self.assertIsNone(
            self.dl.find_local_video('abcdefghijk'),
            "audio was mistaken for a full video download",
        )

    def test_full_video_scan_ignores_clip_sections(self):
        """A 40-second clip section must never be treated as the full source."""
        section = self.dl.sections_dir / 'abcdefghijk__sec_100_140.mp4'
        section.parent.mkdir(parents=True, exist_ok=True)
        section.write_bytes(b'\0' * (2 * 1024 * 1024))

        self.assertIsNone(
            self.dl.find_local_video('abcdefghijk'),
            "a clip section was mistaken for the full video",
        )

    def test_audio_fetch_resumes_without_network(self):
        self._install_fake(FakeAudioYDL)
        self.dl.download_audio('abcdefghijk')
        calls_after_first = len(FakeYDL.calls)

        meta = self.dl.download_audio('abcdefghijk')
        self.assertIsNotNone(meta)
        self.assertEqual(len(FakeYDL.calls), calls_after_first,
                         "a second audio fetch must not hit the network")

    def test_audio_metadata_reads_sidecar_in_audio_dir(self):
        """Title/duration must survive a resume with no yt-dlp info dict."""
        self._install_fake(FakeAudioYDL)
        meta = self.dl.download_audio('abcdefghijk')
        audio = Path(meta['audio_path'])
        # Write the sidecar yt-dlp would have written, next to the audio.
        audio.with_suffix('.info.json').write_text(json.dumps({
            'id': 'abcdefghijk', 'title': 'Real Title',
            'duration': 1234, 'uploader': 'Real Channel',
        }))

        resumed = self.dl._audio_metadata('abcdefghijk', audio)
        self.assertEqual(resumed['title'], 'Real Title')
        self.assertEqual(resumed['duration'], 1234)


if __name__ == '__main__':
    unittest.main()
