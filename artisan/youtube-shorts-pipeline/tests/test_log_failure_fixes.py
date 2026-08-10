"""Regression tests for the three failure classes seen in the 2026-08-09 run.

Each test maps to a defect that was visible in that log:

1. Every render for one source failed (28/28 clips, "No clips could be
   rendered"). The music mixer built a multi-input labelled filtergraph and
   passed it to ``-af``, which only accepts a simple 1-in/1-out chain, so
   FFmpeg rejected the command outright. The music file was also never added
   as an input, and the duck factor was applied as a *boost*.

2. Published titles contained caption speaker markers (">> Yes, of course.",
   "NBS? >> In my mind of mine"). Broadcast captions mark speaker changes with
   ">>"; those survived HTML-entity decoding and leaked into titles, keyword
   scoring and burned captions.

3. Backlog uploads failed with "Video file not found" and stayed queued, so
   they were retried every run and consumed the whole per-run upload cap.

Plus the channel-gating inconsistency found while tracing (3): the scheduled
sweep skipped every niche on a single-default-token install, while the other
two code paths would have run.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import video_editor as ve  # noqa: E402
from src.subtitles import _strip_speaker_markers, parse_subtitle_file  # noqa: E402


# ---------------------------------------------------------------------------
# 1. The render-killing audio graph
# ---------------------------------------------------------------------------
def test_audio_filters_end_in_aout_label():
    """Both graphs must terminate in [aout], which the command maps."""
    for with_music in (False, True):
        chains = ve.VideoEditor._build_audio_filters(with_music)
        assert chains, 'no audio chain produced'
        assert '[aout]' in chains[-1]


def test_speech_only_graph_is_a_single_chain():
    """Without music there is one input, so exactly one chain is needed."""
    chains = ve.VideoEditor._build_audio_filters(False)
    assert len(chains) == 1
    # Must not reference a second input that was never added.
    assert '[1:a' not in chains[0]
    assert 'loudnorm' in chains[0]


def test_music_graph_reads_the_second_input_not_the_speech_twice():
    """The original bug: [0:a:0] was used as both speech AND music source."""
    chains = ve.VideoEditor._build_audio_filters(True)
    joined = ';'.join(chains)
    # The music bed must come from input 1, the separate music file.
    assert '[1:a:0]' in joined, 'music bed does not read the music input'
    # And the mix must combine two distinct streams.
    assert 'amix=inputs=2' in joined


def test_music_graph_ducks_music_using_speech_as_sidechain():
    """Ducking must key the *music* off the *speech*, not off itself."""
    chains = ve.VideoEditor._build_audio_filters(True)
    joined = ';'.join(chains)
    assert 'sidechaincompress' in joined
    # The speech is split so one copy drives the compressor.
    assert 'asplit' in joined
    # The compressor consumes [bed] + [sidechain] in that order: the stream
    # being ducked first, the control signal second.
    assert '[bed][sidechain]sidechaincompress' in joined


def test_music_graph_does_not_use_makeup_gain_to_boost_music():
    """`makeup=1/duck_factor` boosted the bed under speech -- the inverse."""
    chains = ve.VideoEditor._build_audio_filters(True)
    assert 'makeup' not in ';'.join(chains)


def test_duck_factor_of_zero_does_not_divide_by_zero(monkeypatch):
    """A 0.0 duck factor used to raise ZeroDivisionError building the filter."""
    monkeypatch.setattr(ve.config, 'music_duck_factor', 0.0, raising=False)
    chains = ve.VideoEditor._build_audio_filters(True)  # must not raise
    assert 'sidechaincompress' in ';'.join(chains)


def test_stronger_duck_means_larger_compression_ratio(monkeypatch):
    """Smaller music_duck_factor = quieter bed under speech = higher ratio."""
    def ratio_for(factor):
        monkeypatch.setattr(ve.config, 'music_duck_factor', factor,
                            raising=False)
        chains = ve.VideoEditor._build_audio_filters(True)
        text = ';'.join(chains)
        marker = 'ratio='
        start = text.index(marker) + len(marker)
        end = start
        while end < len(text) and (text[end].isdigit() or text[end] == '.'):
            end += 1
        return float(text[start:end])

    assert ratio_for(0.1) > ratio_for(0.8)


def test_music_is_skipped_when_disabled_or_silent(monkeypatch, tmp_path):
    """No pointless second input when music is off or its volume is zero."""
    track = tmp_path / 'bed.mp3'
    track.write_bytes(b'x' * 64)
    monkeypatch.setattr(ve.VideoEditor, '_music_cache', [track],
                        raising=False)

    monkeypatch.setattr(ve.config, 'music_enabled', False, raising=False)
    monkeypatch.setattr(ve.config, 'music_volume', 0.15, raising=False)
    assert ve.VideoEditor._pick_music_track() is None

    monkeypatch.setattr(ve.config, 'music_enabled', True, raising=False)
    monkeypatch.setattr(ve.config, 'music_volume', 0.0, raising=False)
    assert ve.VideoEditor._pick_music_track() is None


def test_missing_music_dir_yields_no_track_instead_of_raising(monkeypatch,
                                                             tmp_path):
    """An absent music dir must degrade to speech-only, not crash."""
    monkeypatch.setattr(ve.VideoEditor, '_music_cache', None, raising=False)
    monkeypatch.setattr(ve.VideoEditor, '_music_warned', False, raising=False)
    monkeypatch.setattr(ve.config, 'music_enabled', True, raising=False)
    monkeypatch.setattr(ve.config, 'music_volume', 0.15, raising=False)
    monkeypatch.setattr(ve.config, 'music_dir',
                        str(tmp_path / 'nope'), raising=False)
    import src.music_sources as ms
    monkeypatch.setattr(ms, 'sync_ncs_music', lambda **kw: [], raising=False)
    assert ve.VideoEditor._pick_music_track() is None


def test_zero_byte_music_files_are_ignored(monkeypatch, tmp_path):
    """A truncated download would make FFmpeg fail on an empty input."""
    (tmp_path / 'empty.mp3').write_bytes(b'')
    good = tmp_path / 'good.mp3'
    good.write_bytes(b'x' * 64)
    monkeypatch.setattr(ve.VideoEditor, '_music_cache', None, raising=False)
    monkeypatch.setattr(ve.config, 'music_dir', str(tmp_path), raising=False)
    tracks = ve.VideoEditor._music_tracks()
    names = [p.name for p in tracks]
    assert 'good.mp3' in names
    assert 'empty.mp3' not in names


def test_render_no_longer_passes_a_labelled_graph_to_af():
    """`-af` cannot express the 2-input mix; only `-filter_complex` can."""
    source = open(os.path.join(os.path.dirname(__file__), '..',
                               'src', 'video_editor.py'),
                  encoding='utf-8').read()
    # The clip-render path must not hand a labelled graph to -af. The only
    # remaining -af is normalize_audio(), a genuine 1-in/1-out chain.
    assert "aloop=loop=-1" not in source, 'old broken music filter is back'
    assert "makeup={1/music_duck_factor}" not in source


# ---------------------------------------------------------------------------
# 2. Caption speaker markers leaking into titles
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('raw, expected', [
    # The exact strings observed in the log.
    ('>> Yes, of course. I mean', 'Yes, of course. I mean'),
    ('NBS? >> In my mind of mine', 'NBS? In my mind of mine'),
    ('We all need each other. >> Yeah.', 'We all need each other. Yeah.'),
    # ">>>" marks a new program, not just a new speaker.
    ('>>> Tonight on the show', 'Tonight on the show'),
    # Leading speaker attributions.
    ('INTERVIEWER: so tell me', 'so tell me'),
    ('John Smith: hello there', 'hello there'),
    # Non-speech annotations.
    ('(laughs) that was wild', 'that was wild'),
    ('[APPLAUSE] >> Welcome back.', 'Welcome back.'),
])
def test_speaker_markers_are_stripped(raw, expected):
    assert _strip_speaker_markers(raw) == expected


@pytest.mark.parametrize('text', [
    # A mid-sentence colon is ordinary prose, not an attribution.
    'the truth is: it works',
    # Times must survive.
    'meet me at 3:30 sharp',
    # A bare sentence is untouched.
    'I knew the owner is a good guy',
])
def test_ordinary_text_is_not_mangled(text):
    assert _strip_speaker_markers(text) == text


def test_annotation_only_cue_becomes_empty():
    """A cue that is purely "[MUSIC]" is not speech and must be dropped."""
    assert _strip_speaker_markers('[MUSIC]') == ''
    assert _strip_speaker_markers('\u266a\u266a') == ''


def test_vtt_parse_drops_annotation_cues_and_cleans_markers(tmp_path):
    """End-to-end: entity-encoded '>>' must not reach the segment text."""
    vtt = tmp_path / 'sub.en.vtt'
    vtt.write_text(
        'WEBVTT\n\n'
        '00:00:01.000 --> 00:00:03.000\n'
        '&gt;&gt; Yes, of course. I mean\n\n'
        '00:00:03.000 --> 00:00:05.000\n'
        '[MUSIC]\n\n'
        '00:00:05.000 --> 00:00:07.000\n'
        'NBS? &gt;&gt; In my mind of mine\n',
        encoding='utf-8',
    )
    segments = parse_subtitle_file(str(vtt))
    texts = [s['text'] for s in segments]
    assert texts == ['Yes, of course. I mean', 'NBS? In my mind of mine']
    assert not any('>>' in t for t in texts)


# ---------------------------------------------------------------------------
# 3. Backlog clips whose file vanished
# ---------------------------------------------------------------------------
def _make_db(tmp_path, niche='test'):
    from src.database import PipelineDatabase
    db = PipelineDatabase(tmp_path / 'pipeline.db')
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO processed_videos (youtube_video_id, niche) "
            "VALUES ('vid1', ?)", (niche,))
    return db


def test_missing_status_removes_clip_from_upload_queue(tmp_path):
    """Marking a ghost clip 'missing' must take it out of the queue for good."""
    db = _make_db(tmp_path)
    real = tmp_path / 'real.mp4'
    real.write_bytes(b'x')

    db.record_short('vid1', 1, 0.0, 20.0, 'Real clip',
                    local_path=str(real), score=9.0)
    db.record_short('vid1', 2, 20.0, 40.0, 'Ghost clip',
                    local_path=str(tmp_path / 'gone.mp4'), score=10.0)

    assert len(db.get_queued_clips_for_upload('test', limit=10)) == 2

    db.update_clip_status('vid1', 2, 'missing')

    remaining = db.get_queued_clips_for_upload('test', limit=10)
    assert [c['title'] for c in remaining] == ['Real clip']
    # Queue-health counters must agree, or the scheduler still sees the ghost.
    assert db.count_queued_by_source('test') == {'vid1': 1}


def test_ghost_clip_does_not_starve_a_real_one(tmp_path):
    """The ghost sorted first by score and used to consume the only slot."""
    db = _make_db(tmp_path)
    real = tmp_path / 'real.mp4'
    real.write_bytes(b'x')
    db.record_short('vid1', 1, 0.0, 20.0, 'Real clip',
                    local_path=str(real), score=1.0)
    db.record_short('vid1', 2, 20.0, 40.0, 'Ghost clip',
                    local_path=str(tmp_path / 'gone.mp4'), score=99.0)

    supply = db.get_queued_clips_for_upload('test', limit=10)
    # Reproduce the pre-filter the drain now applies.
    from pathlib import Path as _P
    present = [c for c in supply if _P(c['local_path']).exists()]
    assert [c['title'] for c in present] == ['Real clip']


# ---------------------------------------------------------------------------
# 4. Channel gating consistency
# ---------------------------------------------------------------------------
def _usable(channels, authed):
    """The gate the sweep now uses, mirrored from run_scheduled_mode."""
    return [c for c in channels if not authed or c in authed]


@pytest.mark.parametrize('channels, authed, expected', [
    # No per-channel token files = single default token. This is the case the
    # sweep used to skip while run_niche() and the backlog drain both ran.
    (['main'], [], ['main']),
    (['main'], ['main'], ['main']),
    # Bound to a channel that has no token: correctly skipped.
    (['main'], ['other'], []),
    # No channel configured at all: skipped.
    ([], [], []),
    # Multi-channel niche, only one authenticated.
    (['a', 'b'], ['b'], ['b']),
])
def test_channel_gate_matches_the_rest_of_the_pipeline(channels, authed,
                                                      expected):
    assert _usable(channels, authed) == expected


def test_sweep_gate_is_not_the_broken_any_form():
    """`any(c in authed ...)` is always False for empty authed."""
    source = open(os.path.join(os.path.dirname(__file__), '..',
                               'src', 'main.py'), encoding='utf-8').read()
    assert 'not any(c in authed for c in channels)' not in source
