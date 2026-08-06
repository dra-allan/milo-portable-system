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


class SectionOffsetTests(unittest.TestCase):
    """The keyframe-drift arithmetic, which is what caption sync rests on.

    _describe_section() is pure given a probed duration, so these tests stub
    the probe and assert the offset directly. Getting this wrong does not raise
    -- it silently shifts every caption -- so it is worth pinning down exactly.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        _isolate_config(tmp)
        for mod in [m for m in list(sys.modules) if m.startswith('src.')]:
            del sys.modules[mod]
        from src.downloader import YouTubeDownloader
        self.dl = YouTubeDownloader()

        self.section = self.dl.sections_dir / 'abcdefghijk__sec_92_148.mp4'
        self.section.write_bytes(b'\0' * (1024 * 1024))

    def tearDown(self):
        self._tmp.cleanup()

    def _describe(self, file_duration, start=100.0, end=140.0, pad=8.0):
        """Run the offset math with a stubbed probe result."""
        self.dl._probe_duration = lambda _p: file_duration
        req_start = max(0.0, start - pad)
        req_end = end + pad
        return self.dl._describe_section(
            self.section, start, end, req_start, req_end,
            pad_before=start - req_start,
        )

    def test_no_drift_gives_padding_only_offset(self):
        """An exact 56s section means the clip starts at the padding."""
        info = self._describe(file_duration=56.0)
        self.assertAlmostEqual(info['lead_in'], 0.0, places=3)
        self.assertAlmostEqual(info['clip_start_in_file'], 8.0, places=3)
        self.assertAlmostEqual(info['clip_duration'], 40.0, places=3)

    def test_keyframe_lead_in_is_measured_and_added(self):
        """A 6s-longer file means 6s of keyframe lead-in before the padding.

        This is the trap: without measuring, the cut would start at 8.0s and
        every frame (and caption) would be 6s early.
        """
        info = self._describe(file_duration=62.0)
        self.assertAlmostEqual(info['lead_in'], 6.0, places=3)
        self.assertAlmostEqual(info['clip_start_in_file'], 14.0, places=3)
        self.assertAlmostEqual(info['clip_duration'], 40.0, places=3)

    def test_clip_start_plus_duration_stays_inside_the_file(self):
        """The cut must never run past the end of what we downloaded."""
        for file_duration in (56.0, 62.0, 45.0, 20.0, 10.0):
            info = self._describe(file_duration=file_duration)
            self.assertLessEqual(
                info['clip_start_in_file'] + info['clip_duration'],
                file_duration + 1e-6,
                f"cut runs past end of a {file_duration}s file",
            )
            self.assertGreaterEqual(info['clip_start_in_file'], 0.0)

    def test_padding_clamped_at_zero_does_not_shift_the_cut(self):
        """A clip near t=0 cannot have the full padding before it.

        Requesting 8s of lead padding for a clip starting at 3s only yields 3s,
        so the requested span is 0..48 = 48s. Using the nominal 8s padding as
        the in-file offset would shift the cut 5s late.
        """
        info = self._describe(file_duration=48.0, start=3.0, end=40.0, pad=8.0)
        self.assertAlmostEqual(info['lead_in'], 0.0, places=3)
        self.assertAlmostEqual(info['clip_start_in_file'], 3.0, places=3,
                               msg="clamped padding was not accounted for")

    def test_clamped_padding_and_lead_in_combine(self):
        """Clamped padding and keyframe drift must add, not replace."""
        # Span 0..48 = 48s requested; a 52s file carries 4s of lead-in on top
        # of the 3s of surviving padding.
        info = self._describe(file_duration=52.0, start=3.0, end=40.0, pad=8.0)
        self.assertAlmostEqual(info['lead_in'], 4.0, places=3)
        self.assertAlmostEqual(info['clip_start_in_file'], 7.0, places=3)

    def test_short_section_shrinks_duration_instead_of_overrunning(self):
        """A truncated section (end of video) yields a shorter clip, not junk."""
        info = self._describe(file_duration=20.0)
        self.assertLessEqual(info['clip_start_in_file'] + info['clip_duration'], 20.0)
        self.assertGreater(info['clip_duration'], 0.0)

    def test_unprobeable_file_falls_back_to_padding_offset(self):
        """If ffprobe fails we must still produce a usable, non-negative offset."""
        info = self._describe(file_duration=0.0)
        self.assertAlmostEqual(info['clip_start_in_file'], 8.0, places=3)
        self.assertAlmostEqual(info['clip_duration'], 40.0, places=3)


class SectionDownloadTests(unittest.TestCase):
    """download_section() end to end: it computes pad_before, not the caller.

    The offset tests above call _describe_section directly, which leaves the
    padding-clamp computation in download_section itself unverified. These
    tests drive the public entry point so that logic is covered too.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        _isolate_config(tmp)
        for mod in [m for m in list(sys.modules) if m.startswith('src.')]:
            del sys.modules[mod]
        from src.downloader import YouTubeDownloader
        self.dl = YouTubeDownloader()
        FakeYDL.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def _install_section_fake(self, file_duration):
        """Fake yt_dlp that writes the section file the real one would."""
        dl = self.dl

        class SectionYDL:
            def __init__(self, opts):
                self.opts = opts
                FakeYDL.calls.append(opts)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True):
                path = Path(str(self.opts['outtmpl']).replace('%(ext)s', 'mp4'))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'\0' * (1024 * 1024))
                return {'id': 'abcdefghijk'}

        fake = types.ModuleType('yt_dlp')
        fake.YoutubeDL = SectionYDL
        utils = types.ModuleType('yt_dlp.utils')
        utils.download_range_func = lambda chapters, ranges, from_info=False: {
            'ranges': ranges}
        fake.utils = utils
        sys.modules['yt_dlp'] = fake
        sys.modules['yt_dlp.utils'] = utils
        self.addCleanup(sys.modules.pop, 'yt_dlp', None)
        self.addCleanup(sys.modules.pop, 'yt_dlp.utils', None)
        dl._probe_duration = lambda _p: file_duration

    def test_requested_range_includes_padding_on_both_sides(self):
        self._install_section_fake(file_duration=56.0)
        self.dl.download_section('abcdefghijk', 100.0, 140.0, padding=8.0)

        ranges = FakeYDL.calls[0]['download_ranges']['ranges']
        self.assertEqual(ranges, [(92.0, 148.0)])

    def test_clip_near_zero_uses_surviving_padding_only(self):
        """A clip at t=3 with 8s padding has only 3s of lead before it.

        If download_section passed the nominal 8s through, the cut would land
        5s late and every caption with it. The requested range is clamped to
        0..48, so the offset must be 3.0.
        """
        self._install_section_fake(file_duration=48.0)
        info = self.dl.download_section('abcdefghijk', 3.0, 40.0, padding=8.0)

        ranges = FakeYDL.calls[0]['download_ranges']['ranges']
        self.assertEqual(ranges, [(0.0, 48.0)],
                         "padding was not clamped at the start of the video")
        self.assertAlmostEqual(info['clip_start_in_file'], 3.0, places=3)
        self.assertAlmostEqual(info['clip_duration'], 37.0, places=3)

    def test_section_is_reused_on_a_second_call(self):
        self._install_section_fake(file_duration=56.0)
        first = self.dl.download_section('abcdefghijk', 100.0, 140.0, padding=8.0)
        calls = len(FakeYDL.calls)

        second = self.dl.download_section('abcdefghijk', 100.0, 140.0, padding=8.0)
        self.assertEqual(len(FakeYDL.calls), calls,
                         "an already-fetched section was downloaded again")
        self.assertEqual(first['path'], second['path'])
        self.assertAlmostEqual(first['clip_start_in_file'],
                               second['clip_start_in_file'], places=3)

    def test_rejects_inverted_bounds(self):
        self._install_section_fake(file_duration=56.0)
        self.assertIsNone(self.dl.download_section('abcdefghijk', 140.0, 100.0))
        self.assertEqual(FakeYDL.calls, [], "a bad range still hit the network")

    def test_download_sections_preserves_order_and_reports_failures(self):
        self._install_section_fake(file_duration=56.0)
        results = self.dl.download_sections(
            'abcdefghijk', [(100.0, 140.0), (300.0, 320.0)], padding=8.0,
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r is not None for r in results))
        self.assertAlmostEqual(results[0]['source_start'], 100.0, places=3)
        self.assertAlmostEqual(results[1]['source_start'], 300.0, places=3)


class SectionRealFileTests(unittest.TestCase):
    """End-to-end check of the offset against a real ffmpeg-probed file.

    The stubbed tests above pin the arithmetic; this one proves the arithmetic
    is fed a real duration correctly, i.e. that _probe_duration and
    _describe_section actually agree on a file that exists.
    """

    @classmethod
    def setUpClass(cls):
        import shutil as _shutil
        if not _shutil.which('ffmpeg'):
            raise unittest.SkipTest('ffmpeg not available')

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        _isolate_config(tmp)
        for mod in [m for m in list(sys.modules) if m.startswith('src.')]:
            del sys.modules[mod]
        from src.downloader import YouTubeDownloader
        self.dl = YouTubeDownloader()

    def tearDown(self):
        self._tmp.cleanup()

    def _make_section(self, name, duration):
        """Synthesise a real video file of a known duration."""
        import subprocess
        path = self.dl.sections_dir / name
        subprocess.run(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
             '-f', 'lavfi', '-i', f'testsrc=size=320x240:rate=10:duration={duration}',
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)],
            check=True, capture_output=True, timeout=120,
        )
        return path

    def test_measured_offset_on_a_real_file_with_lead_in(self):
        # Requested span is 100-8 .. 140+8 = 56s; the file is 62s, so 6s of it
        # is keyframe lead-in.
        path = self._make_section('abcdefghijk__sec_92_148.mp4', 62)
        info = self.dl._describe_section(path, 100.0, 140.0, 92.0, 148.0,
                                         pad_before=8.0)

        self.assertAlmostEqual(info['file_duration'], 62.0, delta=0.5)
        self.assertAlmostEqual(info['lead_in'], 6.0, delta=0.5)
        self.assertAlmostEqual(info['clip_start_in_file'], 14.0, delta=0.5)
        self.assertLessEqual(info['clip_start_in_file'] + info['clip_duration'],
                             info['file_duration'] + 1e-6)

    def test_measured_offset_on_a_real_exact_length_file(self):
        path = self._make_section('abcdefghijk__sec_92_148.mp4', 56)
        info = self.dl._describe_section(path, 100.0, 140.0, 92.0, 148.0,
                                         pad_before=8.0)
        self.assertAlmostEqual(info['lead_in'], 0.0, delta=0.5)
        self.assertAlmostEqual(info['clip_start_in_file'], 8.0, delta=0.5)


if __name__ == '__main__':
    unittest.main()
