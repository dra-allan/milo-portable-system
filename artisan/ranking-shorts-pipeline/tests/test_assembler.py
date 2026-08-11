"""Assembler tests: duration arithmetic, ranking order, graph shape."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import assembler, ranker  # noqa: E402
from src.config import config  # noqa: E402


# ---------------------------------------------------------------------------
# Duration maths
# ---------------------------------------------------------------------------
def test_transitions_shorten_the_result():
    """xfade overlaps its inputs, so the sum of clip lengths overstates the
    runtime by (n-1) x transition. Budgeting on the sum ships videos over 60s.
    """
    assert assembler.visible_total([10.0] * 5, 0.28) == pytest.approx(48.88)


def test_single_clip_has_no_overlap():
    assert assembler.visible_total([7.0], 0.28) == pytest.approx(7.0)


def test_empty_is_zero_not_negative():
    assert assembler.visible_total([], 0.28) == 0.0


def test_overlong_build_is_fitted_under_the_cap():
    cap = float(config.get('hard_max_total_seconds', 59))
    transition = float(config.get('transition_duration', 0.28))
    fitted = assembler.fit_durations([20.0] * 5)
    assert assembler.visible_total(fitted, transition) <= cap


def test_fitting_takes_from_the_longest_clip_first():
    """A 3-second clip is already at the payoff; 45-second ones have slack."""
    fitted = assembler.fit_durations([45.0, 3.0, 45.0, 45.0, 45.0])
    assert fitted[1] == pytest.approx(3.0)
    assert fitted[0] < 45.0
    cap = float(config.get('hard_max_total_seconds', 59))
    assert assembler.visible_total(fitted, 0.28) <= cap


# ---------------------------------------------------------------------------
# Head-trim fitting (fit_windows)
# ---------------------------------------------------------------------------
def _window(start, duration, action_offset):
    return {'start': start, 'duration': duration, 'action_offset': action_offset}


def test_fit_windows_fits_every_clip_under_the_cap():
    cap = float(config.get('hard_max_total_seconds', 59))
    transition = float(config.get('transition_duration', 0.28))
    clips = [_window(2.0, 40.0, 20.0) for _ in range(5)]
    assembler.fit_windows(clips)
    assert assembler.visible_total(
        [c['duration'] for c in clips], transition) <= cap
    assert len(clips) == 5  # nothing gets dropped to make room


def test_fit_windows_trims_the_head_not_the_end():
    """Sliding the window forward must keep the peak at the same absolute
    time: the climax stays on screen while the lead-in is shaved off."""
    before = _window(2.0, 40.0, 20.0)
    clips = [dict(before) for _ in range(5)]
    assembler.fit_windows(clips)
    for before_c, after in zip([before] * 5, clips):
        peak_before = before_c['start'] + before_c['action_offset']
        peak_after = after['start'] + after['action_offset']
        assert peak_after == pytest.approx(peak_before)
        assert after['start'] > before_c['start']  # head moved forward
        assert after['action_offset'] <= after['duration']
        assert after['duration'] >= float(config.get('min_clip_seconds', 2.5))


def test_fit_windows_leaves_a_short_build_alone():
    clips = [_window(0.0, 4.0, 1.5), _window(0.0, 3.5, 1.0),
             _window(0.0, 5.0, 2.0)]
    originals = [dict(c) for c in clips]
    assembler.fit_windows(clips)
    for before, after in zip(originals, clips):
        assert (after['start'], after['duration'],
                after['action_offset']) == (
            before['start'], before['duration'], before['action_offset'])


def test_fit_windows_never_goes_below_the_floor():
    floor = float(config.get('min_clip_seconds', 2.5))
    clips = [_window(5.0, 40.0, 25.0) for _ in range(6)]
    assembler.fit_windows(clips)
    assert min(c['duration'] for c in clips) >= floor
    assert all(c['action_offset'] <= c['duration'] for c in clips)


def test_fitting_never_goes_below_the_floor():
    floor = float(config.get('min_clip_seconds', 2.5))
    fitted = assembler.fit_durations([60.0] * 5)
    assert min(fitted) >= floor


def test_short_build_is_left_alone():
    original = [4.0, 3.5, 5.0]
    assert assembler.fit_durations(list(original)) == original


def test_fitting_terminates_when_everything_is_at_the_floor():
    """Guards the trim loop: at the floor there is nothing left to give, and
    without the break this spins until the guard counter saves it."""
    floor = float(config.get('min_clip_seconds', 2.5))
    fitted = assembler.fit_durations([floor] * 40)
    assert fitted == [floor] * 40


# ---------------------------------------------------------------------------
# Encoder flags
# ---------------------------------------------------------------------------
def test_crf_is_translated_per_encoder(monkeypatch):
    """Hardware encoders do not speak -crf. Passing it through would silently
    change quality when VIDEO_ENCODER is set."""
    monkeypatch.setattr(assembler._Encoder, '_resolved', 'h264_nvenc')
    args = assembler.video_encode_args()
    assert '-crf' not in args
    assert '-cq' in args


def test_libx264_path_uses_crf(monkeypatch):
    monkeypatch.setattr(assembler._Encoder, '_resolved', 'libx264')
    args = assembler.video_encode_args()
    assert '-crf' in args


def test_encoder_off_means_cpu(monkeypatch):
    monkeypatch.setattr(assembler._Encoder, '_resolved', None)
    monkeypatch.setattr(config, 'encoder', 'off')
    assert assembler._Encoder.resolve() == 'libx264'


# ---------------------------------------------------------------------------
# Ranking order
# ---------------------------------------------------------------------------
def _clip(motion, views=1000):
    return {'motion_score': motion, 'views': views, 'text_coverage': 0.0,
            'music_confidence': 0.0, 'words_per_second': 0.0}


def test_best_clip_takes_number_one():
    clips = [_clip(m) for m in (0.2, 0.9, 0.5, 0.4, 0.3)]
    ordered = ranker.rank(clips, count=5)
    best = max(clips, key=ranker.score)
    assert best['rank'] == 1


def test_second_best_opens_the_video():
    """The naive ordering puts the *worst* clip first, where most of the
    audience is. The hook slot gets the runner-up instead."""
    clips = [_clip(m) for m in (0.2, 0.9, 0.85, 0.4, 0.3)]
    ordered = ranker.rank(clips, count=5)
    scores = sorted((ranker.score(c) for c in clips), reverse=True)
    assert ordered[0]['rank'] == 5
    assert ordered[0]['score'] == pytest.approx(scores[1])


def test_playback_order_counts_down():
    clips = [_clip(m) for m in (0.2, 0.9, 0.5, 0.4, 0.3)]
    ordered = ranker.rank(clips, count=5)
    assert [c['rank'] for c in ordered] == [5, 4, 3, 2, 1]


def test_quality_rises_towards_number_one():
    clips = [_clip(m) for m in (0.2, 0.9, 0.5, 0.4, 0.3)]
    ordered = ranker.rank(clips, count=5)
    middle = [c['score'] for c in ordered if c['rank'] in (2, 3, 4)]
    assert middle == sorted(middle)


def test_short_build_still_ranks():
    clips = [_clip(0.5), _clip(0.9)]
    ordered = ranker.rank(clips, count=2)
    assert sorted(c['rank'] for c in ordered) == [1, 2]


def test_commentary_is_penalised():
    """Residual speech competes with the voice-over laid over it."""
    clean = _clip(0.5)
    talky = dict(_clip(0.5), words_per_second=0.44)
    assert ranker.score(clean) > ranker.score(talky)


def test_views_cannot_dominate_the_sort():
    """A viral but static clip must not outrank a great unknown one."""
    viral_static = _clip(0.1, views=50_000_000)
    unknown_good = _clip(0.9, views=200)
    assert ranker.score(unknown_good) > ranker.score(viral_static)
