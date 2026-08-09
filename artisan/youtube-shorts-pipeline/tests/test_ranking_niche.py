"""Regression tests for the ranking niche and the keyword-matching fix.

These lock in three things that were broken or missing:

1. Keyword matching is WORD-BOUNDARY based, not substring based. Substring
   matching rejected good sources because short negative keywords hide inside
   ordinary words ("live" in "Delivered", "dance" in "Abundance", "guide" in
   "Guided", "concert" in "Concerted"). On a top-10 niche that misfire threw
   away 5 of 6 realistic titles.

2. ranking_mode scoring actually prefers clips that are usable as standalone
   Shorts: ones that OPEN on an enumeration cue and ones containing the #1
   payoff, over mid-item narration with no setup.

3. The ranking_general_commentary niche is wired end to end, and enabling it
   did not disturb the 24 pre-existing niches.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolate_config(tmp: Path):
    os.environ['TEMP_DIR'] = str(tmp / 'temp')
    os.environ['DATA_DIR'] = str(tmp / 'data')
    os.environ['LOG_DIR'] = str(tmp / 'logs')
    os.environ['SHORTS_DIR'] = str(tmp / 'shorts')
    os.environ['DB_PATH'] = str(tmp / 'data' / 'test.db')


# ---------------------------------------------------------------------------
# 1. Word-boundary keyword matching
# ---------------------------------------------------------------------------
class TestKeywordMatching(unittest.TestCase):
    """The substring-matching bug and its edge cases."""

    def test_short_negatives_no_longer_match_inside_words(self):
        """The exact false positives that killed the ranking niche."""
        from src.discovery import matches_keyword

        # (title, keyword) pairs that substring matching WRONGLY rejected.
        cases = [
            ("Top 10 Most Expensive Cars Ever Delivered", "live"),   # De-liver-ed
            ("Top 10 Times Abundance Changed Everything", "dance"),  # Abun-dance
            ("Top 5 Guided Missiles In History", "guide"),           # Guide-d
            ("Top 10 Concerted Efforts That Failed", "concert"),     # Concert-ed
            ("Top 10 Facts About Plastic Pollution", "last"),        # P-last-ic
            ("Top 10 Memento Mori Traditions", "meme"),              # Meme-nto
        ]
        for title, kw in cases:
            with self.subTest(title=title, keyword=kw):
                self.assertFalse(
                    matches_keyword(title, kw),
                    f"{kw!r} must not match inside a word of {title!r}",
                )

    def test_real_whole_word_still_matches(self):
        """Fixing false positives must not create false negatives."""
        from src.discovery import matches_keyword

        cases = [
            ("Top 10 Animals That Live In The Amazon", "live"),
            ("Top 10 Reaction Times", "reaction"),
            ("Movie Trailer Breakdown", "trailer"),
            ("Best Dance Routines", "dance"),
        ]
        for title, kw in cases:
            with self.subTest(title=title, keyword=kw):
                self.assertTrue(matches_keyword(title, kw))

    def test_multi_word_phrases_still_match(self):
        """Existing configs rely on phrases like 'live stream'."""
        from src.discovery import matches_keyword

        self.assertTrue(matches_keyword("Live stream: full show", "live stream"))
        self.assertTrue(matches_keyword("A LIVE   STREAM today", "live stream"))
        self.assertFalse(matches_keyword("Live show, no stream", "live stream"))

    def test_punctuation_edged_keywords(self):
        """'#shorts' and 'vs' must work despite non-word characters."""
        from src.discovery import matches_keyword

        self.assertTrue(matches_keyword("Best #shorts ever", "#shorts"))
        self.assertTrue(matches_keyword("Ranked: Ford vs Ferrari", "vs"))
        # 'vs' must not fire inside 'Vsauce' or 'versus'
        self.assertFalse(matches_keyword("Vsauce explains gravity", "vs"))

    def test_matched_keywords_returns_only_real_hits(self):
        from src.discovery import matched_keywords

        text = "Top 10 most expensive cars ever delivered"
        hits = matched_keywords(text, ["top 10", "most expensive", "live", "clip"])
        self.assertEqual(sorted(hits), ["most expensive", "top 10"])

    def test_empty_and_none_inputs_are_safe(self):
        from src.discovery import matched_keywords, matches_keyword

        self.assertFalse(matches_keyword("", "live"))
        self.assertFalse(matches_keyword("Some title", ""))
        self.assertEqual(matched_keywords("Some title", None), [])
        self.assertEqual(matched_keywords(None, ["live"]), [])


class TestDiscoveryUsesWordBoundaries(unittest.TestCase):
    """End-to-end: a good ranking video survives the negative-keyword filter."""

    def test_ranking_titles_are_not_wrongly_rejected(self):
        from test_discovery import FakeDB, FakeDownloader

        with tempfile.TemporaryDirectory() as td:
            _isolate_config(Path(td))
            from src.config import config
            from src.discovery import discover_candidates

            # config is a module-level singleton: overwriting .niches leaks
            # into every later test in the run, so always put it back.
            original_niches = config.niches
            self.addCleanup(setattr, config, 'niches', original_niches)

            vids = [
                # Previously rejected by substring "live" / "dance" / "guide".
                {'id': 'good1111111', 'channel_id': '@rank',
                 'title': 'Top 10 Most Expensive Cars Ever Delivered',
                 'duration': 1200},
                {'id': 'good2222222', 'channel_id': '@rank',
                 'title': 'Top 10 Times Abundance Changed Everything',
                 'duration': 1200},
                # Genuinely a compilation: must still be rejected.
                {'id': 'bad11111111', 'channel_id': '@rank',
                 'title': 'Top 10 Fails Compilation', 'duration': 1200},
            ]
            config.niches = {
                'rank_test': {
                    'channels': ['@rank'],
                    'negative_keywords': ['compilation', 'live', 'dance', 'guide'],
                    'min_duration': 300,
                    'max_duration': 10800,
                }
            }
            result = discover_candidates(
                FakeDownloader(vids), FakeDB(), 'rank_test',
                max_videos=5, lookback=10,
            )
            picked = {c['id'] for c in result.candidates}
            self.assertIn('good1111111', picked)
            self.assertIn('good2222222', picked)
            self.assertNotIn('bad11111111', picked)
            self.assertIn('bad11111111', result.skipped_negative_keywords)


# ---------------------------------------------------------------------------
# 2. ranking_mode scoring
# ---------------------------------------------------------------------------
class TestRankingSignals(unittest.TestCase):
    def test_enumeration_cues_detected(self):
        from src.processor import ranking_signals

        for text in ("Coming in at number four, the Bugatti",
                     "number three on our list",
                     "No. 7 will surprise you",
                     "and at #2 we have"):
            with self.subTest(text=text):
                self.assertGreater(ranking_signals(text)['enumerations'], 0)

    def test_payoff_detected(self):
        from src.processor import ranking_signals

        for text in ("And the number one spot goes to",
                     "this takes the crown",
                     "our winner tonight"):
            with self.subTest(text=text):
                self.assertGreater(ranking_signals(text)['payoffs'], 0)

    def test_plain_narration_has_no_signals(self):
        from src.processor import ranking_signals

        s = ranking_signals(
            "and so they had to redesign the rear suspension, "
            "which took the engineers a while"
        )
        self.assertEqual(s['enumerations'], 0)
        self.assertEqual(s['payoffs'], 0)

    def test_opens_on_enumeration_is_position_sensitive(self):
        from src.processor import opens_on_enumeration

        self.assertTrue(opens_on_enumeration(
            "Coming in at number four, the fastest jet ever built."))
        # Cue exists but far past the opening window -> not self-contained.
        self.assertFalse(opens_on_enumeration(
            "So anyway the engineers argued about the wing design for a very "
            "long time indeed, and eventually we get to number four."))


class TestRankingModeScoring(unittest.TestCase):
    """ranking_mode must reorder clips toward usable Shorts."""

    KW = ["top 10", "countdown", "most expensive", "richest"]

    SELF_CONTAINED = ("Coming in at number four, the Bugatti La Voiture Noire. "
                      "It is the most expensive car ever sold at nineteen "
                      "million dollars. Only one was ever built.")
    PAYOFF = ("And the number one spot goes to the Rolls-Royce Boat Tail. "
              "This is the single richest commission in history. "
              "Twenty eight million dollars for one car!")
    MID_ITEM = ("and so they had to redesign the rear suspension because of "
                "that, which took a while, and then the engineers went back "
                "to the drawing board again.")

    def _score(self, text, ranking_mode):
        from src.processor import ContentProcessor
        seg = {'text': text, 'start': 0.0, 'end': 28.0}
        return ContentProcessor().score_segment(
            seg, None, None, self.KW, ranking_mode=ranking_mode)

    def test_self_contained_item_is_boosted(self):
        self.assertGreater(self._score(self.SELF_CONTAINED, True),
                           self._score(self.SELF_CONTAINED, False))

    def test_payoff_is_boosted(self):
        self.assertGreater(self._score(self.PAYOFF, True),
                           self._score(self.PAYOFF, False))

    def test_mid_item_narration_is_penalised(self):
        self.assertLess(self._score(self.MID_ITEM, True),
                        self._score(self.MID_ITEM, False))

    def test_ranking_mode_widens_the_gap(self):
        """Good vs bad separation must improve, which is the whole point."""
        plain_gap = (self._score(self.SELF_CONTAINED, False)
                     - self._score(self.MID_ITEM, False))
        rank_gap = (self._score(self.SELF_CONTAINED, True)
                    - self._score(self.MID_ITEM, True))
        self.assertGreater(rank_gap, plain_gap)

    def test_compilation_span_penalised_against_single_item(self):
        """A clip crossing many boundaries loses the payoff structure."""
        many = ("Number ten was fast. Number nine was faster. Number eight "
                "was quicker. Number seven beat it. Number six was best. "
                "Number five won.")
        self.assertGreater(self._score(self.SELF_CONTAINED, True),
                           self._score(many, True))

    def test_scores_never_negative(self):
        self.assertGreaterEqual(self._score("uh um erm hmm", True), 0.0)

    def test_default_is_off_so_other_niches_unchanged(self):
        """ranking_mode must default to False in the signature."""
        import inspect
        from src.processor import ContentProcessor

        for fn in (ContentProcessor.score_segment,
                   ContentProcessor.find_highlight_segments):
            with self.subTest(fn=fn.__name__):
                p = inspect.signature(fn).parameters['ranking_mode']
                self.assertIs(p.default, False)

    def test_find_highlight_segments_accepts_ranking_mode(self):
        """The flag must reach the real selection path, not just scoring."""
        from src.processor import ContentProcessor

        transcript = []
        t = 0.0
        for i in (5, 4, 3, 2, 1):
            transcript.append({
                'start': t, 'end': t + 6.0,
                'text': f"Coming in at number {i}, the most expensive item "
                        f"on this countdown, and here is why it matters.",
            })
            t += 6.0
        clips = ContentProcessor().find_highlight_segments(
            transcript, niche_keywords=self.KW, max_clips=2, ranking_mode=True,
        )
        self.assertTrue(clips)
        for c in clips:
            self.assertGreater(c['end'], c['start'])


# ---------------------------------------------------------------------------
# 3. Niche configuration wiring
# ---------------------------------------------------------------------------
class TestRankingNicheConfig(unittest.TestCase):
    NICHE = 'ranking_general_commentary'

    def _cfg(self):
        from src.config import config
        return config.get_niche_config(self.NICHE)

    def test_niche_exists_with_ranking_mode_enabled(self):
        cfg = self._cfg()
        self.assertTrue(cfg['channels'], "niche must have source channels")
        self.assertIs(cfg['ranking_mode'], True)

    def test_upload_channel_bound(self):
        cfg = self._cfg()
        self.assertEqual(cfg['upload_channels'], [self.NICHE])

    def test_ranking_mode_defaults_off_for_other_niches(self):
        from src.config import config
        for other in ('flick_shorts', 'capital_mindset'):
            with self.subTest(niche=other):
                self.assertFalse(config.get_niche_config(other)['ranking_mode'])

    def test_no_keyword_is_also_a_negative_keyword(self):
        """A word in both lists would filter out the content it targets."""
        cfg = self._cfg()
        pos = {k.lower() for k in cfg['keywords']}
        neg = {k.lower() for k in cfg['negative_keywords']}
        self.assertEqual(pos & neg, set())

    def test_negative_keywords_do_not_reject_healthy_ranking_titles(self):
        """The regression that broke the niche in the first place."""
        from src.discovery import matches_keyword

        cfg = self._cfg()
        titles = [
            "Top 10 Most Expensive Cars Ever Delivered",
            "Top 10 Times Abundance Changed Everything",
            "Top 5 Guided Missiles In History",
            "Top 10 Deadliest Animals In The Amazon",
            "Ranked: Every Ferrari From Worst To Best",
        ]
        for title in titles:
            hits = [k for k in cfg['negative_keywords']
                    if matches_keyword(title, k)]
            with self.subTest(title=title):
                self.assertEqual(hits, [], f"wrongly rejected by {hits}")

    def test_real_junk_titles_are_still_rejected(self):
        """The filter must not be so loose that it accepts anything."""
        from src.discovery import matches_keyword

        cfg = self._cfg()
        junk = [
            "Top 10 Fails Compilation",
            "My reaction to the new trailer",
            "Official Music Video",
            "Minecraft gameplay walkthrough part 4",
            "#shorts quick tip",
        ]
        for title in junk:
            hits = [k for k in cfg['negative_keywords']
                    if matches_keyword(title, k)]
            with self.subTest(title=title):
                self.assertTrue(hits, "junk title should have been rejected")

    def test_yaml_still_parses_and_other_niches_intact(self):
        from src.config import config
        # 24 pre-existing + the new one.
        self.assertGreaterEqual(len(config.niches), 25)
        self.assertIn(self.NICHE, config.niches)
        for required in ('flick_shorts', 'capital_mindset'):
            self.assertIn(required, config.niches)


if __name__ == '__main__':
    unittest.main(verbosity=2)
