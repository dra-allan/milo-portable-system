"""Word-level caption burn: ASS generation and transcript word collection.

The clipper renders a static hook via Pillow and, separately, burns viral
word-by-word captions from the cached Whisper transcript. The caption engine is
vendored from the Shorts lane (src/viral_captions.py); these tests pin the
integration, not the engine itself.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import viral_captions as vcap  # noqa: E402
from src import highlights  # noqa: E402


def word_segment(tokens, base=0.0, step=0.35):
    """One transcript segment carrying per-word timings."""
    words = []
    cursor = base
    for token in tokens:
        words.append({'word': token, 'start': cursor, 'end': cursor + 0.3})
        cursor += step
    return {'text': ' '.join(tokens), 'start': base,
            'end': cursor, 'confidence': -0.5, 'words': words}


class TestTranscriptWordCollection(unittest.TestCase):
    def test_collect_keeps_words(self):
        class Word:
            def __init__(self, word, start, end):
                self.word = word
                self.start = start
                self.end = end
                self.probability = 0.9

        class Segment:
            text = 'wait for it'
            start = 1.0
            end = 2.0
            avg_logprob = -0.2
            words = [Word('wait', 1.0, 1.2), Word('for', 1.2, 1.4),
                     Word('it', 1.4, 2.0)]

        out = highlights._collect(iter([Segment()]))
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]['words']), 3)
        self.assertEqual(out[0]['words'][0]['word'], 'wait')
        self.assertAlmostEqual(out[0]['words'][0]['start'], 1.0)

    def test_collect_drops_empty_segments(self):
        class Empty:
            text = '   '
            start = 0.0
            end = 1.0
            avg_logprob = 0.0
            words = []

        self.assertEqual(highlights._collect(iter([Empty()])), [])


class TestAssFromTranscript(unittest.TestCase):
    def test_build_viral_ass_produces_dialogue(self):
        segs = [word_segment(['this', 'is', 'insane', 'watch'])]
        doc = vcap.build_viral_ass(segs, preset_name='viral',
                                   clip_duration=3.0)
        self.assertIn('[Events]', doc)
        self.assertIn('Dialogue:', doc)

    def test_no_words_returns_none(self):
        self.assertIsNone(vcap.build_viral_ass([]))

    def test_rebases_to_clip_start(self):
        # Segment spoken at 10-14s of the source; clip starts at 10s. First
        # dialogue line must begin at 0:00:00.00, not 10s.
        segs = [word_segment(['go', 'now'], base=10.0, step=0.5)]
        doc = vcap.build_viral_ass(segs, time_offset=10.0, clip_duration=4.0)
        first = [line for line in doc.splitlines()
                 if line.startswith('Dialogue:')][0]
        self.assertIn(',0:00:00.00,', first)

    def test_words_outside_window_are_dropped(self):
        # Whole transcript lives before the clip start; nothing to caption.
        segs = [word_segment(['early', 'speech'], base=0.0, step=0.5)]
        self.assertIsNone(vcap.build_viral_ass(segs, time_offset=10.0,
                                               clip_duration=5.0))


if __name__ == '__main__':
    unittest.main()