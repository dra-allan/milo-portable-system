"""The edit engine, and the regex regression that made scoring a no-op.

No ffmpeg, no Whisper, no network: everything here is timeline arithmetic and
text matching, which is exactly the part that has to be right before a single
frame is rendered.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import story_edit as se  # noqa: E402
from src import highlights as hl  # noqa: E402


def _segment(text, start, end):
    """A transcript segment with evenly spread word timings."""
    tokens = text.split()
    step = (end - start) / max(1, len(tokens))
    words = [{'word': token, 'start': start + i * step,
              'end': start + (i + 1) * step}
             for i, token in enumerate(tokens)]
    return {'text': text, 'start': start, 'end': end, 'words': words}


# The cows-on-the-plane clip from the playbook, roughly to scale: the question
# lands in the middle and the reveal is at the end.
COWS = [
    _segment('Normally they go under the plane.', 0.0, 3.0),
    _segment('This time they are literally going in it.', 3.0, 6.0),
    _segment('Are cows allowed on the plane?', 8.0, 10.5),
    _segment('They are? Okay, perfect.', 10.5, 12.0),
    _segment('We have space for four cows.', 13.0, 16.0),
    _segment('Five cows. I am sorry, five cows.', 16.0, 19.0),
    _segment('You got the best miniature Highlands in the world right now.', 19.0, 24.0),
]


# ---------------------------------------------------------------------------
# The regression
# ---------------------------------------------------------------------------
def test_payoff_lexicon_actually_matches_words():
    """r'\\\\b(...)' matched a literal backslash, so this never fired."""
    assert hl._PAYOFF.search('wait for it')
    assert hl._PAYOFF.search('that was insane')
    assert not hl._PAYOFF.search('completely unrelated sentence')


def test_word_tokeniser_actually_matches_words():
    """The empty token list short-circuited _score_text to all zeros."""
    assert hl._WORD.findall("don't stop believing") == ["don't", 'stop',
                                                        'believing']


def test_score_text_is_no_longer_uniformly_zero():
    setup, payoff, relevance, density = hl._score_text(
        'so watch this because it was absolutely insane', ['insane'])
    assert setup > 0, 'setup cues are present and must score'
    assert payoff > 0, 'payoff cues are present and must score'
    assert relevance > 0
    assert density > 0


def test_score_text_still_returns_zeros_for_empty_speech():
    assert hl._score_text('', ['x']) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Hook detection
# ---------------------------------------------------------------------------
def test_question_is_found_even_without_a_question_mark():
    """Whisper drops terminal punctuation constantly."""
    assert se.is_question('Are cows allowed on the plane')
    assert se.is_question('why did he do that?')
    assert not se.is_question('Normally they go under the plane.')


def test_a_question_at_the_very_start_is_not_worth_moving():
    segments = [_segment('Are cows allowed on the plane?', 0.2, 2.0),
                _segment('They are, yes.', 2.0, 4.0)]
    assert se.find_question(segments, 0.0, 20.0, min_lead_in=1.0) is None


def test_a_question_in_the_last_fifth_is_skipped():
    """Lifting it to the front would leave the story with nothing to build."""
    segments = [_segment('Filler talking.', 0.0, 17.0),
                _segment('Are cows allowed on the plane?', 18.0, 19.5)]
    assert se.find_question(segments, 0.0, 20.0) is None


def test_title_hook_is_lifted_and_cleaned():
    assert se.title_hook_from('So are cows allowed on the plane?') == \
        'ARE COWS ALLOWED ON THE PLANE?'
    assert se.title_hook_from('are cows allowed?', uppercase=False) == \
        'are cows allowed?'


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------
def test_question_first_puts_the_question_at_the_front():
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=60.0,
                         style=se.STYLE_QUESTION_FIRST)
    assert plan.style == se.STYLE_QUESTION_FIRST
    assert plan.is_reordered
    assert plan.spans[0].role == se.ROLE_HOOK
    assert plan.spans[0].start == pytest.approx(8.0, abs=0.01)
    # The hook keeps the answering beat, so it runs past the question's end.
    assert plan.spans[0].end > 10.5
    assert 'ARE COWS ALLOWED ON THE PLANE?' in plan.title_hook


def test_question_first_preserves_total_duration():
    """Reordering must not change how long the clip is.

    A campaign clip one second under the spec minimum is a wasted daily
    submission slot, so the reorder is a permutation, not a re-length.
    """
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=60.0,
                         style=se.STYLE_QUESTION_FIRST)
    assert plan.duration == pytest.approx(24.0, abs=0.05)


def test_the_whole_window_is_still_covered_after_reordering():
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=60.0,
                         style=se.STYLE_QUESTION_FIRST)
    covered = sorted((s.start, s.end) for s in plan.spans)
    assert covered[0][0] == pytest.approx(0.0)
    assert covered[-1][1] == pytest.approx(24.0)


def test_max_duration_is_respected_by_trimming_the_tail_not_the_hook():
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=20.0,
                         style=se.STYLE_QUESTION_FIRST)
    assert plan.duration <= 20.0 + 0.05
    assert plan.spans[0].role == se.ROLE_HOOK
    assert plan.spans[0].start == pytest.approx(8.0, abs=0.01)


def test_no_question_falls_back_to_a_straight_cut():
    flat = [_segment('Just some even narration with nothing notable.', 0.0, 20.0)]
    plan = se.build_plan({'start': 0.0, 'end': 20.0}, flat,
                         min_duration=10.0, max_duration=60.0,
                         style=se.STYLE_AUTO)
    assert plan.style == se.STYLE_STRAIGHT
    assert not plan.is_reordered
    assert plan.duration == pytest.approx(20.0)


def test_cold_open_teases_the_payoff_then_replays_the_clip():
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=90.0,
                         style=se.STYLE_COLD_OPEN)
    assert plan.style == se.STYLE_COLD_OPEN
    assert plan.spans[0].role == se.ROLE_HOOK
    assert plan.spans[0].duration <= 3.0
    assert plan.spans[1].start == pytest.approx(0.0)
    assert plan.spans[1].end == pytest.approx(24.0)


def test_slivers_are_dropped_and_seams_merged():
    """Contiguous spans are one shot; a concat seam there buys nothing."""
    notes = []
    spans = se._normalise([se.Span(0.0, 5.0), se.Span(5.0, 9.0),
                          se.Span(20.0, 20.05)], 0.4, 0.0, 60.0, notes)
    assert len(spans) == 1
    assert spans[0].start == 0.0 and spans[0].end == 9.0


def test_plan_survives_a_round_trip_through_json_shape():
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=60.0)
    restored = se.EditPlan.from_dict(plan.to_dict())
    assert restored is not None
    assert restored.style == plan.style
    assert restored.duration == pytest.approx(plan.duration)
    assert restored.title_hook == plan.title_hook


def test_old_clip_rows_without_an_edit_plan_render_as_before():
    plan = se.plan_from({'start': 4.0, 'duration': 18.0})
    assert not plan.is_reordered
    assert plan.spans[0].start == 4.0
    assert plan.spans[0].end == 22.0


# ---------------------------------------------------------------------------
# The sync property
# ---------------------------------------------------------------------------
def test_captions_follow_the_reorder():
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=60.0,
                         style=se.STYLE_QUESTION_FIRST)
    remapped = se.remap_segments(COWS, plan)
    assert remapped, 'the clip has speech, so it must have captions'
    # The question is now the first thing said in the output.
    assert 'cows allowed' in remapped[0]['text'].lower()
    assert remapped[0]['start'] == pytest.approx(0.0, abs=0.2)


def test_remapped_captions_stay_inside_the_output_duration():
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=60.0,
                         style=se.STYLE_QUESTION_FIRST)
    remapped = se.remap_segments(COWS, plan)
    for segment in remapped:
        assert segment['start'] >= -0.001
        assert segment['end'] <= plan.duration + 0.05, (
            'a caption past the end of the clip is a caption on a black frame')
        for word in segment['words']:
            assert 0.0 - 0.001 <= word['start'] <= plan.duration + 0.05


def test_remapped_captions_are_monotonic():
    """Overlapping caption events are what libass stacks on top of each other."""
    plan = se.build_plan({'start': 0.0, 'end': 24.0}, COWS,
                         min_duration=10.0, max_duration=60.0,
                         style=se.STYLE_QUESTION_FIRST)
    remapped = se.remap_segments(COWS, plan)
    starts = [s['start'] for s in remapped]
    assert starts == sorted(starts)


def test_speech_outside_every_span_is_dropped():
    plan = se.EditPlan(spans=[se.Span(0.0, 5.0)])
    remapped = se.remap_segments(COWS, plan)
    assert all(s['end'] <= 5.05 for s in remapped)
    assert not any('Highlands' in s['text'] for s in remapped)


# ---------------------------------------------------------------------------
# ffmpeg graph
# ---------------------------------------------------------------------------
def test_filtergraph_rebases_trim_times_against_the_seek():
    """trim works on the decoded timeline, so -ss has to be subtracted.

    Getting this wrong is silent: ffmpeg happily trims the wrong seconds.
    """
    plan = se.EditPlan(spans=[se.Span(100.0, 104.0, se.ROLE_HOOK),
                              se.Span(90.0, 100.0)])
    seek, span = se.read_window(plan)
    assert seek == pytest.approx(90.0)
    assert span == pytest.approx(14.5, abs=0.01)
    chains, video, audio = se.build_filtergraph(plan, has_audio=True, seek=seek)
    assert 'trim=start=10.000:end=14.000' in chains[0]
    assert 'trim=start=0.000:end=10.000' in chains[2]
    assert video == 'evcat' and audio == 'eacat'
    assert 'concat=n=2:v=1:a=1' in chains[-1]


def test_filtergraph_omits_audio_for_a_silent_source():
    """Concatenating a stream that does not exist is a graph ffmpeg rejects."""
    plan = se.EditPlan(spans=[se.Span(0.0, 2.0), se.Span(5.0, 9.0)])
    chains, video, audio = se.build_filtergraph(plan, has_audio=False, seek=0.0)
    assert audio is None
    assert video == 'evcat'
    assert not any('atrim' in chain for chain in chains)
    assert 'concat=n=2:v=1:a=0' in chains[-1]
