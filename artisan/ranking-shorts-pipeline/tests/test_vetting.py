"""Vetting tests: the audio gate ('no muted or silent clips')."""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import vetting  # noqa: E402

_HAVE_FFMPEG = shutil.which('ffmpeg') is not None


def _make(kind, path):
    """Build a tiny container with the requested audio situation."""
    silent = (kind == 'silent')
    if kind == 'video_only':
        args = ['-f', 'lavfi', '-i', 'color=c=black:s=160x120:d=1',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)]
    else:
        src = ('anullsrc=channel_layout=stereo:sample_rate=44100'
               if silent else 'sine=frequency=440')
        args = ['-f', 'lavfi', '-i', src, '-t', '2', '-c:a', 'aac',
                str(path)]
    cmd = [shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error',
           '-y'] + args
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE)
    assert proc.returncode == 0, proc.stderr.decode('utf-8', 'replace')
    return path


@pytest.mark.skipif(not _HAVE_FFMPEG, reason='ffmpeg not installed')
def test_silent_clip_is_rejected(tmp_path):
    clip = _make('silent', tmp_path / 'silent.mp4')
    assert vetting.audible_reason(str(clip)) == 'silent_audio'


@pytest.mark.skipif(not _HAVE_FFMPEG, reason='ffmpeg not installed')
def test_audible_clip_passes(tmp_path):
    clip = _make('tone', tmp_path / 'tone.mp4')
    assert vetting.audible_reason(str(clip)) is None


@pytest.mark.skipif(not _HAVE_FFMPEG, reason='ffmpeg not installed')
def test_clip_without_audio_stream_is_rejected(tmp_path):
    clip = _make('video_only', tmp_path / 'video_only.mp4')
    assert vetting.audible_reason(str(clip)) == 'no_audio'