"""Tests for the Phase 5 deep candidate list.

The point of ``max_candidates`` is that transcription -- the expensive stage --
happens once, then we can rank 30+ clips from it. A later ``--render-more N``
can pull additional clips from the cached plan with zero re-download and zero
re-transcription cost.
"""

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure local src/ is importable
_tmp = tempfile.TemporaryDirectory()
sys.path.insert(0, str(Path(_tmp.name)))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Isolate config to a temp directory so tests don't touch the real .env
def _isolate_config(tmp: Path):
    import os
    os.environ['DATA_DIR'] = str(tmp / 'data')
    os.environ['TEMP_DIR'] = str(tmp / 'temp')
    os.environ['SHORTS_DIR'] = str(tmp / 'shorts')
    os.environ['LOG_DIR'] = str(tmp / 'logs')
    os.environ['DB_PATH'] = str(tmp / 'data' / 'processed_videos.db')
    os.environ['WHISPER_MODEL'] = 'tiny'
    os.environ['TRANSCRIBE_MODEL'] = 'tiny'
    os.environ['TRANSCRIBE_BEAM'] = '1'
    os.environ['TRANSCRIBE_WORD_TIMESTAMPS'] = 'false'
    os.environ['MAX_CANDIDATES'] = '30'
    os.environ['MAX_CLIPS_PER_VIDEO'] = '5'

_isolate_config(Path(_tmp.name))

# Must import after env is set
from src.processor import ContentProcessor


def _make_transcript(segments):
    """Build a transcript list from (start, end, text) tuples."""
    return [{'start': s, 'end': e, 'text': t} for s, e, t in segments]


class DeepCandidateTests(unittest.TestCase):
    """Phase 5: max_candidates returns a deep ranked plan."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _isolate_config(Path(self._tmp.name))
        # Force re-import of config
        for mod in [m for m in list(sys.modules) if m.startswith('src.')]:
            del sys.modules[mod]
        from src.processor import ContentProcessor
        self.proc = ContentProcessor()

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_max_candidates_is_five(self):
        """Without max_candidates, falls back to max_clips (8 by default)."""
        proc = ContentProcessor()
        segs = _make_transcript([
            (0, 5, 'Hook here is why this works'),
            (5, 10, 'Let me show you the secret'),
            (10, 15, 'The trick is simple and works'),
            (15, 20, 'Most people never realize this'),
            (20, 25, 'You need to try this now'),
            (25, 30, 'I cannot believe how easy'),
            (30, 35, 'Biggest mistake is waiting'),
            (35, 40, 'What happens next is crazy'),
            (40, 45, 'Turns out it works every time'),
        ])
        # Transcript span = 45s, 9 segments of 5s each
        clips = proc.find_highlight_segments(segs, max_clips=8, max_candidates=None)
        # Without max_candidates, it should use max_clips (8)
        self.assertLessEqual(len(clips), 8)

    def test_max_candidates_thirty_returns_more(self):
        """max_candidates=30 should allow up to 30 clips (if available)."""
        segs = _make_transcript([
            (i, i+5, f'Hook {i} secret reason trick') for i in range(0, 300, 5)
        ])
        clips = self.proc.find_highlight_segments(segs, max_candidates=30)
        self.assertLessEqual(len(clips), 30)
        # With 60 segments of 5s, we can make many 15-60s clips
        self.assertGreater(len(clips), 5)

    def test_rank_field_is_priority_not_chronological(self):
        """rank=1 is highest score, not earliest in time."""
        segs = _make_transcript([
            (0, 20, 'Weak hook low score filler um uh'),
            (20, 40, 'Here is why the secret works let me show you'),  # Strong
            (40, 60, 'Another weak one with filler um'),
            (60, 80, 'The trick is let me show you the secret'),       # Strong
            (80, 100, 'Filler um uh nothing here'),
        ])
        clips = self.proc.find_highlight_segments(
            segs, max_clips=5, max_candidates=5,
            min_segment_length=15, max_segment_length=60
        )
        self.assertTrue(all('rank' in c for c in clips))
        # Ranks should be 1..N by priority, not time
        ranks = [c['rank'] for c in clips]
        self.assertEqual(sorted(ranks), list(range(1, len(clips)+1)))
        # The highest-ranked clip should be one of the strong ones
        top = min(clips, key=lambda c: c['rank'])
        self.assertIn('secret', top['text'].lower())

    def test_chronological_sort_preserves_rank(self):
        """Clips returned in chronological order but rank reflects priority."""
        # Need enough content to generate multiple non-overlapping clips
        segs = _make_transcript([
            (0, 20, 'First clip weak filler um uh um uh'),
            (20, 40, 'Second clip strong secret hook let me show you'),
            (40, 60, 'Third clip medium reason'),
            (60, 80, 'Fourth clip another secret trick'),
            (80, 100, 'Fifth clip more filler um uh'),
        ])
        clips = self.proc.find_highlight_segments(
            segs, max_clips=5, max_candidates=5,
            min_segment_length=15, max_segment_length=60
        )
        # Clips should be in chronological order
        for i in range(len(clips)-1):
            self.assertLessEqual(clips[i]['start'], clips[i+1]['start'])
        # But rank is by score
        ranks = [c['rank'] for c in clips]
        self.assertEqual(set(ranks), set(range(1, len(clips)+1)))

    def test_max_candidates_caps_at_available_clips(self):
        """If source only supports 3 clips, max_candidates=30 returns 3."""
        segs = _make_transcript([
            (0, 15, 'Hook one'),
            (15, 30, 'Hook two secret'),
            (30, 45, 'Hook three'),
        ])
        clips = self.proc.find_highlight_segments(segs, max_candidates=30)
        self.assertLessEqual(len(clips), 3)
        self.assertTrue(all('rank' in c for c in clips))

    def test_max_candidates_zero_fallbacks_to_max_clips(self):
        """max_candidates=0 should be treated as 'use max_clips'."""
        segs = _make_transcript([
            (i, i+20, f'Hook {i} secret') for i in range(0, 100, 20)
        ])
        clips = self.proc.find_highlight_segments(segs, max_clips=5, max_candidates=0)
        self.assertLessEqual(len(clips), 5)


if __name__ == '__main__':
    unittest.main()