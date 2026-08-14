"""Required-keyword extraction.

The phrase case is the one that matters. ``Required keywords: Kingdom Clash
#KingdomClashMove1 @kingdomclashgame`` split on whitespace yields the tokens
"Kingdom" and "Clash", and a caption reading "clash of kingdoms" then satisfies
both while failing the campaign, which required the product name.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import compiler  # noqa: E402
from src.spec import CLIPPING  # noqa: E402


class TestKeywordLine(unittest.TestCase):
    def test_phrase_stays_whole(self):
        tokens = compiler.parse_keyword_line(
            'Kingdom Clash #KingdomClashMove1 @kingdomclashgame')
        self.assertIn('Kingdom Clash', tokens)
        self.assertIn('#KingdomClashMove1', tokens)
        self.assertIn('@kingdomclashgame', tokens)
        self.assertNotIn('Kingdom', tokens)
        self.assertNotIn('Clash', tokens)

    def test_single_hashtag(self):
        self.assertEqual(compiler.parse_keyword_line('#roobet'), ['#roobet'])

    def test_comma_separated_phrases(self):
        tokens = compiler.parse_keyword_line('GamePoint Bingo, play now')
        self.assertIn('GamePoint Bingo', tokens)
        self.assertIn('play now', tokens)

    def test_empty(self):
        self.assertEqual(compiler.parse_keyword_line('  '), [])


class TestSpecUsesPhrases(unittest.TestCase):
    def test_compiled_spec_keeps_the_phrase(self):
        spec = compiler.compile_requirements(
            '### Caption Requirements\n'
            'Required keywords: Kingdom Clash #KingdomClashMove1 '
            '@kingdomclashgame\n',
            'kingdom', 'Kingdom Clash')
        self.assertIn('Kingdom Clash', spec.caption.required_keywords)
        self.assertIn('@kingdomclashgame', spec.caption.required_mentions)


class TestTypeInference(unittest.TestCase):
    def test_concept_alone_does_not_make_it_ugc(self):
        # GamePoint Bingo is a clipping campaign whose brief describes concepts.
        spec = compiler.compile_requirements(
            'READ THE FULL BRIEF (concepts, assets & captions)\n'
            'Gameplay footage MUST be in every video\n',
            'bingo', 'GamePoint Bingo')
        self.assertEqual(spec.type, CLIPPING)

    def test_explicit_ugc_sets_the_type(self):
        spec = compiler.compile_requirements(
            'THIS IS A UGC CAMPAIGN\n', 'x', 'X')
        self.assertEqual(spec.type, 'ugc')


if __name__ == '__main__':
    unittest.main()
