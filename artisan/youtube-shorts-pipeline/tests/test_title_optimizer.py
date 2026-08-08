"""Tests for the rule-based title optimizer."""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.title_optimizer import _clean, _truncate, optimize_title


def test_question_hook_kept():
    t = optimize_title("Why do most businesses fail in the first year?",
                       niche="capital_mindset", clip_index=1)
    assert t == "Why do most businesses fail in the first year?"
    assert t.endswith("?")


def test_exclamation_kept():
    t = optimize_title("This is the single biggest mistake I ever made!",
                       niche="flick_shorts", clip_index=1)
    assert t.endswith("!")


def test_filler_stripped():
    t = optimize_title("So and then as a result, the market moved hard this week.",
                       niche="flick_shorts", clip_index=1)
    assert not t.lower().startswith(("so ", "and ", "then "))
    assert "the market moved hard this week" in t


def test_strong_hook_left_alone():
    t = optimize_title("The secret is that most people never save first.",
                       niche="capital_mindset", clip_index=1)
    # Contains a strong marker => no framing prefix added.
    assert "The secret" in t
    assert t.startswith("The secret")


def test_plain_statement_framed_with_niche_label():
    t = optimize_title(
        "you have to understand why the market moved this week",
        niche="flick_shorts", clip_index=1,
    )
    assert t.startswith("The brutal truth:")
    assert len(t) <= 72


def test_unknown_niche_gets_generic_frame():
    t = optimize_title(
        "you must understand the real pattern behind all of this",
        niche="some_unknown_niche", clip_index=1,
    )
    assert t.startswith("Here's the part people miss:")


def test_length_cap_never_exceeded():
    long_hook = (
        "The secret is that most people never actually save the first ten "
        "percent, they spend it first and then they wonder why they have "
        "nothing left at the end of the month"
    )
    for niche in ("capital_mindset", "flick_shorts", ""):
        t = optimize_title(long_hook, niche=niche, clip_index=1)
        assert len(t) <= 72


def test_empty_hook_fallback():
    t = optimize_title("", niche="flick_shorts", clip_index=7)
    assert "7" in t


def test_stability_same_input_same_output():
    hook = "The market moved hard this week and nobody saw it coming."
    a = optimize_title(hook, niche="flick_shorts", clip_index=1)
    b = optimize_title(hook, niche="flick_shorts", clip_index=1)
    assert a == b


def test_truncate_keeps_sentence_boundary():
    t = _truncate("This is a long sentence. It continues beyond the limit.", 25)
    assert len(t) <= 25


def test_clean_collapses_whitespace():
    assert _clean("  so   you   know   it  ") == "it"
