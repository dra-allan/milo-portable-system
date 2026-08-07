"""Tests for the word-level viral caption engine (src/captions.py).

The behaviour being protected here is what the old renderer got wrong:

* It emitted one dialogue line per Whisper segment -- a 15-25 word paragraph
  held static for 5-10 seconds -- instead of 1-4 word groups that change with
  the speech.
* Its "hormozi" preset truncated each segment to its first 3 words, silently
  discarding most of what was said.
* It wrote a literal ``\\n`` into lines and did not escape ASS syntax, so a
  brace in the speech swallowed the rest of the caption.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import captions as cap  # noqa: E402


def make_words(pairs, start=0.0, step=0.35):
    """Build CaptionWords with regular timing from (text) pairs."""
    words = []
    t = start
    for text in pairs:
        words.append(cap.CaptionWord(text, t, t + step))
        t += step
    return words


def segment(text, start, end, words=None):
    """A transcript segment, optionally with word-level timings."""
    seg = {'text': text, 'start': start, 'end': end}
    if words is not None:
        seg['words'] = words
    return seg


def word_segment(tokens, start=0.0, step=0.4):
    """A segment carrying explicit word timings, as faster-whisper produces."""
    words, t = [], start
    for tok in tokens:
        words.append({'word': tok, 'start': round(t, 3), 'end': round(t + step, 3)})
        t += step
    return segment(' '.join(tokens), start, t, words)


# ---------------------------------------------------------------------------
# Word extraction
# ---------------------------------------------------------------------------
def test_extract_words_reads_word_level_timings():
    seg = word_segment(['this', 'is', 'the', 'moment'])
    words = cap.extract_words([seg])
    assert [w.text for w in words] == ['this', 'is', 'the', 'moment']
    assert words[0].start == pytest.approx(0.0)
    assert words[-1].end == pytest.approx(1.6)


def test_extract_words_rebases_onto_the_clip():
    """Segment times are absolute; the rendered clip restarts at 0.

    Getting this wrong is what put every caption past the end of the clip.
    """
    seg = word_segment(['alpha', 'beta'], start=600.0)
    words = cap.extract_words([seg], time_offset=600.0)
    assert words[0].start == pytest.approx(0.0)
    assert all(w.start >= 0 for w in words)


def test_extract_words_drops_words_past_the_clip_end():
    seg = word_segment(['a', 'b', 'c', 'd', 'e'], step=1.0)
    words = cap.extract_words([seg], clip_duration=2.5)
    assert len(words) < 5
    assert all(w.start < 2.5 for w in words)
    assert all(w.end <= 2.5 + 1e-6 for w in words)


def test_extract_words_survives_missing_timings():
    """Whisper occasionally emits words with null/backwards times."""
    seg = segment('hello world', 0.0, 2.0, words=[
        {'word': 'hello', 'start': None, 'end': None},
        {'word': 'world', 'start': 1.5, 'end': 0.5},   # backwards
    ])
    words = cap.extract_words([seg])
    for w in words:
        assert w.end > w.start, "a non-positive duration never displays"


def test_fallback_estimates_timing_from_segment_text():
    """No word timings must still produce word-level captions.

    The alternative -- reverting to a static paragraph -- is the bug being
    fixed, so the degraded path still has to be word-level.
    """
    seg = segment('one two three four five six', 0.0, 3.0)
    words = cap.words_from_segment_text([seg])
    assert [w.text for w in words] == ['one', 'two', 'three', 'four', 'five', 'six']
    assert words[0].start >= 0.0
    assert words[-1].end <= 3.0 + 1e-6
    # Monotonic and non-overlapping.
    for a, b in zip(words, words[1:]):
        assert a.end <= b.start + 1e-6


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------
def test_groups_are_short():
    """The whole point: 1-4 words, not a paragraph."""
    words = make_words(['the', 'single', 'biggest', 'mistake', 'people',
                        'make', 'is', 'quitting', 'early'])
    groups = cap.group_words(words, max_words=4)
    assert groups
    assert all(1 <= len(g) <= 4 for g in groups)


def test_every_word_survives_grouping():
    """The old hormozi preset threw away everything after the third word."""
    tokens = ['you', 'have', 'to', 'understand', 'that', 'consistency',
              'beats', 'intensity', 'every', 'single', 'time']
    groups = cap.group_words(make_words(tokens), max_words=3)
    assert [w.text for g in groups for w in g] == tokens


def test_group_splits_on_a_speaker_pause():
    """A pause is a phrase boundary; captions should break there."""
    words = [
        cap.CaptionWord('wait', 0.0, 0.3),
        cap.CaptionWord('for', 0.3, 0.6),
        # 1.2s of silence
        cap.CaptionWord('it', 1.8, 2.1),
    ]
    groups = cap.group_words(words, max_words=4, gap_threshold=0.42)
    assert len(groups) == 2
    assert [w.text for w in groups[1]] == ['it']


def test_group_splits_on_sentence_end():
    words = [
        cap.CaptionWord('that.', 0.0, 0.3, ends_sentence=True),
        cap.CaptionWord('now', 0.35, 0.6),
    ]
    groups = cap.group_words(words, max_words=4)
    assert len(groups) == 2


def test_group_respects_character_budget():
    """Long words must not overflow the frame at a 104px font."""
    words = make_words(['extraordinarily', 'complicated', 'terminology'])
    groups = cap.group_words(words, max_words=4, max_chars=22)
    assert all(sum(len(w.text) for w in g) <= 30 for g in groups)


def test_group_closes_on_max_duration():
    """No group should linger; slow speech still needs a changing caption."""
    words = [cap.CaptionWord(f'w{i}', i * 1.0, i * 1.0 + 0.9) for i in range(4)]
    groups = cap.group_words(words, max_words=4, max_duration=1.9,
                             gap_threshold=5.0)
    assert len(groups) > 1


# ---------------------------------------------------------------------------
# Emphasis
# ---------------------------------------------------------------------------
def test_emphasis_prefers_meaningful_words_over_filler():
    """Highlighting "the" instead of "never" wastes the emphasis."""
    filler = cap.CaptionWord('the', 0.0, 0.3)
    strong = cap.CaptionWord('never', 0.3, 0.7)
    assert cap.score_word_importance(strong) > cap.score_word_importance(filler)


def test_numbers_are_emphasis_worthy():
    """Figures are what viewers stop for."""
    number = cap.CaptionWord('$10,000', 0.0, 0.4)
    plain = cap.CaptionWord('thing', 0.4, 0.8)
    assert cap.score_word_importance(number) > cap.score_word_importance(plain)


def test_niche_keywords_raise_a_words_score():
    word = cap.CaptionWord('bitcoin', 0.0, 0.4)
    base = cap.score_word_importance(word)
    boosted = cap.score_word_importance(word, keywords=['bitcoin'])
    assert boosted > base


def test_emphasis_is_rationed():
    """A punch word in every group stops registering as emphasis."""
    tokens = ['this', 'is', 'the', 'single', 'biggest', 'secret', 'nobody',
              'ever', 'tells', 'you', 'about', 'money', 'and', 'freedom',
              'today', 'right', 'now', 'seriously', 'listen', 'closely']
    groups = cap.group_words(make_words(tokens), max_words=4)
    cap.assign_emphasis(groups, punch_ratio=0.22)
    punches = sum(1 for g in groups for w in g if w.emphasis == 'punch')
    assert punches <= max(1, round(len(groups) * 0.22)) + 1


def test_assign_emphasis_marks_at_most_one_word_per_group():
    tokens = ['massive', 'incredible', 'unbelievable', 'huge']
    groups = cap.group_words(make_words(tokens), max_words=4)
    cap.assign_emphasis(groups)
    for g in groups:
        assert sum(1 for w in g if w.emphasis != 'none') <= 1


# ---------------------------------------------------------------------------
# ASS document
# ---------------------------------------------------------------------------
def test_build_viral_ass_produces_a_valid_document():
    seg = word_segment(['the', 'one', 'thing', 'nobody', 'tells', 'you'])
    doc = cap.build_viral_ass([seg], preset_name='viral')
    assert doc
    assert '[Script Info]' in doc
    assert '[V4+ Styles]' in doc
    assert '[Events]' in doc
    assert 'PlayResX: 1080' in doc
    assert 'PlayResY: 1920' in doc
    # Many short dialogue lines, not one paragraph.
    dialogues = [l for l in doc.splitlines() if l.startswith('Dialogue:')]
    assert len(dialogues) >= 2


def test_ass_dialogue_times_are_ordered_and_positive():
    seg = word_segment(['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot'])
    doc = cap.build_viral_ass([seg])
    times = []
    for line in doc.splitlines():
        if not line.startswith('Dialogue:'):
            continue
        parts = line.split(',')
        times.append((parts[1], parts[2]))
    assert times
    for start, end in times:
        assert not start.startswith('-') and not end.startswith('-')
        assert end > start


def test_captions_never_run_past_the_clip():
    """Captions after the cut are invisible and waste the emphasis budget."""
    seg = word_segment(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'], step=1.0)
    doc = cap.build_viral_ass([seg], clip_duration=3.0)
    assert doc
    for line in doc.splitlines():
        if line.startswith('Dialogue:'):
            end = line.split(',')[2]
            h, m, rest = end.split(':')
            assert int(h) * 3600 + int(m) * 60 + float(rest) <= 3.0 + 0.05


def test_no_literal_backslash_n_in_output():
    """The old renderer wrote a literal '\\n' into every line."""
    seg = word_segment(['one', 'two', 'three', 'four', 'five', 'six'])
    doc = cap.build_viral_ass([seg])
    for line in doc.splitlines():
        if line.startswith('Dialogue:'):
            # \N is the legitimate ASS hard break; \n (lowercase) is not.
            assert '\\n' not in line.replace('\\N', '')


def test_braces_in_speech_are_neutralised():
    """A literal brace would swallow the rest of the line as an ASS tag."""
    seg = word_segment(['use', '{this}', 'now'])
    doc = cap.build_viral_ass([seg])
    for line in doc.splitlines():
        if not line.startswith('Dialogue:'):
            continue
        text = line.split(',', 9)[-1]
        # Every remaining brace pair must be a real override block, i.e. begin
        # with a backslash tag.
        import re
        for block in re.findall(r'\{([^}]*)\}', text):
            assert block.startswith('\\'), f"unescaped brace content: {block!r}"


@pytest.mark.parametrize('preset', ['viral', 'hormozi', 'kinetic', 'single',
                                    'minimalist', 'neon'])
def test_every_preset_renders(preset):
    seg = word_segment(['make', 'this', 'work', 'for', 'every', 'style'])
    doc = cap.build_viral_ass([seg], preset_name=preset)
    assert doc and 'Dialogue:' in doc


def test_single_preset_shows_one_word_at_a_time():
    seg = word_segment(['one', 'word', 'at', 'a', 'time'])
    doc = cap.build_viral_ass([seg], preset_name='single')
    dialogues = [l for l in doc.splitlines() if l.startswith('Dialogue:')]
    assert len(dialogues) >= 5


def test_unknown_preset_falls_back_to_default():
    seg = word_segment(['still', 'renders', 'fine'])
    assert cap.build_viral_ass([seg], preset_name='does-not-exist')


def test_empty_input_returns_none():
    """None lets the renderer skip the subtitles filter entirely."""
    assert cap.build_viral_ass([]) is None
    assert cap.build_viral_ass([segment('', 0.0, 1.0)]) is None


def test_font_size_override_reaches_the_style():
    seg = word_segment(['big', 'text'])
    doc = cap.build_viral_ass([seg], font_size=133)
    style = [l for l in doc.splitlines() if l.startswith('Style:')][0]
    assert ',133,' in style


def test_font_size_override_does_not_mutate_the_shared_preset():
    before = cap.PRESETS['viral'].font_size
    cap.build_viral_ass([word_segment(['x', 'y'])], font_size=200, max_words=1)
    assert cap.PRESETS['viral'].font_size == before


def test_max_words_override_is_respected():
    tokens = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    doc = cap.build_viral_ass([word_segment(tokens)], max_words=1)
    dialogues = [l for l in doc.splitlines() if l.startswith('Dialogue:')]
    assert len(dialogues) >= len(tokens)


# ---------------------------------------------------------------------------
# Font resolution
# ---------------------------------------------------------------------------
def test_resolve_font_picks_an_installed_family():
    """libass silently substitutes; we must choose deliberately."""
    got = cap.resolve_font('Montserrat ExtraBold',
                           available=['DejaVu Sans', 'DejaVu Sans Bold'])
    assert got == 'DejaVu Sans Bold'


def test_resolve_font_prefers_the_exact_family_when_present():
    got = cap.resolve_font('Anton', available=['Anton', 'DejaVu Sans'])
    assert got == 'Anton'


def test_resolve_font_without_a_font_list_keeps_the_preference():
    assert cap.resolve_font('Anton', available=None) == 'Anton'


def test_resolve_font_falls_back_to_last_resort_when_nothing_matches():
    got = cap.resolve_font('Anton', available=['Some Unrelated Font'])
    assert got in cap.FONT_FALLBACKS['Anton']
