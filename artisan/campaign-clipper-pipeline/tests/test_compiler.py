"""Compiler tests against the real requirement blocks.

These fixtures are copied from live campaigns rather than written for the tests.
The parser's entire job is surviving how these boards actually phrase things,
including the typos, so inventing tidier input would test nothing.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Point the runtime at a throwaway dir before importing config, which creates
# directories on import.
os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import compiler  # noqa: E402
from src.spec import MUSIC_NATIVE  # noqa: E402

CASTLE = """### Requirements
Account audience requirements:
*   India <= 15%
1 linked account is eligible.
ADD OWN TEXT & SMALL EDITS
CLIP THIS -> [CONTENT TO CLIP](https://drive.google.com/drive/folders/1WftBLD)
ADD LOGO IF NOT ALREADY ON CLIP [LOGO](https://drive.google.com/drive/folders/1GdLfDD)
10s MINIMUM LENGTH
ENGLISH LANGUAGE
POST SPAM/LOW QUALITY
"""

DUEL = """### Requirements
ONLY CLIPS FROM CONTENT FOLDER -> JOIN DISCORD
YOUTUBE SHORTS ONLY!
MUST ADD OWN TEXT / CAPTION (HIGH QUALITY)
1% ENGAGEMENT MINIMUM & 7s MINIMUN LENGTH!
POST LOW QUALITY / SPAM
"""

ROOBET = """### Requirements
MUST MENTION ROOBET IN CAPTION!
ONLY CLIPS FROM [CONTENT FOLDER](https://drive.google.com/drive/folders/1iKX6F)
ADD PROVIDED [LOGO](https://drive.google.com/drive/folders/1qgXnyx)
3,000 VIEWS FOR EARNINGS & 0,4% ENGAGEMENT MINIMUM!
POST LOW QUALITY / SPAM / TRASH
### Caption Requirements
Required keywords: #roobet
"""

KINGDOM = """### Requirements
Account audience requirements:
*   United States >= 40%
MUST READ THE FULL CAMPAIGN BRIEF: [BRIEF](https://ugcninja.notion.site/spec)
AT LEAST 40% OF YOUR ACCOUNT AUDIENCE MUST BE FROM THE UNITED STATES. ENGLISH CONTENT ONLY.
KEEP VIDEOS LIVE FOR AT LEAST 30 DAYS. FEEL NATIVE TO THE PLATFORM.
GAMEPLAY MUST BE CLEARLY VISIBLE THROUGHOUT THE CONCEPT. MENTION THE APP NAME KINGDOM CLASH SOMEWHERE IN THE VIDEO.
ADD TRENDING MUSIC DIRECTLY WITHIN THE PLATFORM WHEN PUBLISHING CONTENT.
NO ADULT CONTENT, POLITICS, RELIGION, ALCOHOL, DRUGS, HATE SPEECH, DISCRIMINATION, GAMBLING, VIOLENCE.
### Caption Requirements
Required keywords: Kingdom Clash #KingdomClashMove1 @kingdomclashgame
"""

BINGO = """### Requirements
READ THE FULL BRIEF (concepts, assets & captions) -> [BRIEF](https://app.notion.com/p/bingo)
Say "GamePoint Bingo" in full (never just "Bingo") in the hook or CTA
tag the official Bingo account under every video
Gameplay footage MUST be in every video
English content only
"""


class TestCastle(unittest.TestCase):
    def setUp(self):
        self.spec = compiler.compile_requirements(CASTLE, 'castle', 'Castle')

    def test_audience_gate(self):
        gates = self.spec.account_gates.audience
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0].country, 'India')
        self.assertEqual(gates[0].operator, '<=')
        self.assertEqual(gates[0].percent, 15)

    def test_min_duration(self):
        self.assertEqual(self.spec.render.min_duration, 10)

    def test_content_and_logo_split(self):
        self.assertEqual(len(self.spec.sources.content_folders), 1)
        self.assertIn('1WftBLD', self.spec.sources.content_folders[0])
        self.assertEqual(len(self.spec.assets.logo_folders), 1)
        self.assertIn('1GdLfDD', self.spec.assets.logo_folders[0])

    def test_logo_is_conditional(self):
        # "IF NOT ALREADY ON CLIP" must not become an unconditional stamp.
        self.assertTrue(self.spec.assets.logo_required)
        self.assertEqual(self.spec.assets.logo_mode, 'if-absent')

    def test_own_text_and_language(self):
        self.assertTrue(self.spec.render.own_text_required)
        self.assertEqual(self.spec.render.language, 'en')

    def test_linked_accounts(self):
        self.assertEqual(self.spec.account_gates.max_linked_accounts, 1)

    def test_spam_line_is_a_prohibition(self):
        # The red cross is lost when the block is copy-pasted, so this line must
        # be classified by content. Read as an obligation it says "post spam".
        joined = ' '.join(self.spec.policy.prohibitions).lower()
        self.assertIn('spam', joined)


class TestDuel(unittest.TestCase):
    def setUp(self):
        self.spec = compiler.compile_requirements(DUEL, 'duel', 'Duel')

    def test_misspelled_minimun_length(self):
        self.assertEqual(self.spec.render.min_duration, 7)

    def test_engagement_percent(self):
        self.assertEqual(self.spec.account_gates.min_engagement_pct, 1.0)

    def test_youtube_only(self):
        self.assertIn('youtube', self.spec.render.platforms)

    def test_discord_content_is_manual(self):
        self.assertTrue(self.spec.sources.manual_only)
        self.assertFalse(self.spec.sources.content_folders)


class TestRoobet(unittest.TestCase):
    def setUp(self):
        self.spec = compiler.compile_requirements(ROOBET, 'roobet', 'Roobet')

    def test_comma_decimal_vs_comma_thousands(self):
        # Both conventions appear in this one block. 0,4% is four tenths of a
        # percent; 3,000 views is three thousand.
        self.assertEqual(self.spec.account_gates.min_engagement_pct, 0.4)
        self.assertEqual(self.spec.account_gates.min_views_for_earnings, 3000)

    def test_required_keyword(self):
        self.assertIn('#roobet', self.spec.caption.required_keywords)

    def test_caption_mention(self):
        self.assertTrue(any('roobet' in item.lower()
                            for item in self.spec.caption.must_mention))

    def test_logo_unconditional(self):
        self.assertTrue(self.spec.assets.logo_required)
        self.assertEqual(self.spec.assets.logo_mode, 'always')


class TestKingdomClash(unittest.TestCase):
    def setUp(self):
        self.spec = compiler.compile_requirements(KINGDOM, 'kingdom',
                                                 'Kingdom Clash')

    def test_us_audience_gate(self):
        gate = self.spec.account_gates.audience[0]
        self.assertEqual(gate.operator, '>=')
        self.assertEqual(gate.percent, 40)

    def test_native_music_becomes_a_manual_step(self):
        self.assertEqual(self.spec.render.music, MUSIC_NATIVE)
        self.assertTrue(any('composer' in step or 'platform' in step
                            for step in self.spec.manual_steps))

    def test_in_video_phrase(self):
        self.assertTrue(any('kingdom clash' in phrase.lower()
                            for phrase
                            in self.spec.render.must_appear_in_video))

    def test_keep_live_days(self):
        self.assertEqual(self.spec.policy.keep_live_days, 30)

    def test_banned_topics(self):
        topics = ' '.join(self.spec.policy.banned_topics)
        for word in ('politics', 'religion', 'gambling'):
            self.assertIn(word, topics)

    def test_gameplay_and_native_feel(self):
        self.assertTrue(self.spec.render.gameplay_visible)
        self.assertTrue(self.spec.policy.native_feel)

    def test_mentions_split_from_keywords(self):
        self.assertIn('@kingdomclashgame',
                      self.spec.caption.required_mentions)
        self.assertNotIn('@kingdomclashgame',
                         self.spec.caption.required_keywords)

    def test_brief_captured(self):
        self.assertIn('notion', self.spec.sources.brief_url)


class TestBingo(unittest.TestCase):
    def setUp(self):
        self.spec = compiler.compile_requirements(BINGO, 'bingo',
                                                 'GamePoint Bingo')

    def test_say_in_full_phrase(self):
        self.assertTrue(any('gamepoint bingo' in phrase.lower()
                            for phrase
                            in self.spec.render.must_appear_in_video))

    def test_tag_official_account(self):
        self.assertTrue(any('bingo' in item.lower()
                            for item in self.spec.caption.must_mention))

    def test_gameplay(self):
        self.assertTrue(self.spec.render.gameplay_visible)


class TestNumberParsing(unittest.TestCase):
    def test_decimal_conventions(self):
        self.assertEqual(compiler._decimal('0,4'), 0.4)
        self.assertEqual(compiler._decimal('0.4'), 0.4)
        self.assertEqual(compiler._decimal('3,000'), 3000)
        self.assertEqual(compiler._decimal('1'), 1)


class TestCardMerge(unittest.TestCase):
    def test_header_and_body_conflict_keeps_stricter(self):
        # The Castle card header says 8s; its requirements text says 10s.
        spec = compiler.compile_requirements(
            CASTLE, 'castle', 'Castle',
            card={'min_duration': 8, 'rate_per_1m': 3000,
                  'platforms': ['youtube', 'tiktok']})
        self.assertEqual(spec.render.min_duration, 10)
        self.assertEqual(spec.rate_per_1m, 3000)
        self.assertIn('tiktok', spec.render.platforms)

    def test_unparsed_is_never_silent(self):
        spec = compiler.compile_requirements(
            'SOME COMPLETELY NOVEL RULE ABOUT PURPLE HATS', 'x', 'X')
        self.assertTrue(spec.unparsed)


if __name__ == '__main__':
    unittest.main()
