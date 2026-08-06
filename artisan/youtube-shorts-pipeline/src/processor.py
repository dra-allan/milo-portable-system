"""Highlight detection for the shorts pipeline.

Historical bug (fixed here): candidate clips were built as fixed 5-second
sliding windows and then filtered with ``min_segment_length <= duration <=
max_segment_length``.  With the shipped defaults (min=15s, max=60s) a 5s
window can never satisfy the filter, so the selector returned an empty list
for *every* video -- exactly the "Found 0 highlight segments from 723
windows" seen in production logs.

The rewrite builds variable-length candidates that grow along real
transcript boundaries until they land inside the requested duration band, so
the candidates are valid Shorts by construction.
"""

import re
from typing import Dict, List, Optional

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger

logger = setup_logger(__name__)

# Words that signal a hook / payoff moment rather than filler narration.
HOOK_PHRASES = (
    "the secret", "nobody tells you", "here is why", "here's why",
    "the truth", "watch this", "look at this", "check this out",
    "the problem is", "the trick is", "most people", "you need to",
    "i can't believe", "i cannot believe", "no way", "oh my god",
    "let me show you", "this is how", "the reason", "turns out",
    "biggest mistake", "never do", "always do", "what happens",
)

FILLER_WORDS = ("um", "uh", "erm", "hmm", "like", "you know", "i mean", "kinda", "sorta")


class ContentProcessor:
    """Scores transcript regions and selects the best Shorts candidates."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def score_segment(self, segment: Dict, prev_segment: Optional[Dict] = None,
                      next_segment: Optional[Dict] = None,
                      niche_keywords: Optional[List[str]] = None) -> float:
        """Score one candidate region for "interestingness".

        Returns a non-negative score. Kept backwards compatible with the
        previous signature because tests and callers rely on it.
        """
        if niche_keywords is None:
            niche_keywords = []

        raw_text = segment.get('text', '') or ''
        text = raw_text.lower()
        duration = float(segment['end']) - float(segment['start'])
        if duration <= 0:
            return 0.0

        words = raw_text.split()
        word_count = len(words)
        if word_count == 0:
            return 0.0

        score = 0.0

        # 1. Speech density. Silence and rambling both make bad Shorts.
        wps = word_count / duration
        if 2.0 <= wps <= 4.5:
            score += 6.0
        elif wps < 2.0:
            score += wps * 1.5          # sparse speech, weak but not zero
        else:
            score += max(0.0, 6.0 - (wps - 4.5) * 2)

        # 2. Niche keywords. Normalised per-clip so a 60s clip full of the
        #    same keyword cannot dwarf a tight 20s clip.
        keyword_hits = sum(1 for kw in niche_keywords if kw and kw.lower() in text)
        score += min(keyword_hits, 6) * 2.5

        # 3. Hook phrases -- the strongest retention signal we can detect
        #    from text alone.
        hook_hits = sum(1 for phrase in HOOK_PHRASES if phrase in text)
        score += min(hook_hits, 4) * 4.0

        # 4. Enthusiasm signals.
        excited = raw_text.count('!') + raw_text.count('?') * 0.5
        excited += sum(1 for w in words if len(w) > 2 and w.isupper() and w.isalpha())
        score += min(excited, 6) * 1.5

        # 5. Clean entry/exit on a natural pause.
        if prev_segment:
            gap = float(segment['start']) - float(prev_segment['end'])
            if 0.25 <= gap <= 2.5:
                score += 2.0
            elif gap < 0.1:
                score -= 1.5            # starts mid-sentence
        if next_segment:
            gap = float(next_segment['start']) - float(segment['end'])
            if 0.25 <= gap <= 2.5:
                score += 1.5
            elif gap < 0.1:
                score -= 1.0            # cuts off mid-sentence

        # 6. Sentence completeness -- ending on punctuation reads far better.
        stripped = raw_text.strip()
        if stripped.endswith(('.', '!', '?')):
            score += 2.0
        if stripped[:1].isupper():
            score += 1.0

        # 7. Vocabulary richness.
        unique_words = len(set(re.findall(r"[a-z']+", text)))
        score += (unique_words / word_count) * 3.0

        # 8. Filler penalty (token-accurate, not substring counting -- the old
        #    version matched "like" inside "unlike" and "so" inside "also").
        tokens = re.findall(r"[a-z']+", text)
        filler_count = sum(1 for t in tokens if t in FILLER_WORDS)
        filler_count += sum(text.count(p) for p in FILLER_WORDS if ' ' in p)
        score -= (filler_count / word_count) * 8.0

        # 9. Duration sweet spot for Shorts retention (20-45s).
        if 20.0 <= duration <= 45.0:
            score += 3.0
        elif duration < 12.0:
            score -= 2.0

        return max(0.0, score)

    # ------------------------------------------------------------------
    # Candidate construction
    # ------------------------------------------------------------------
    def _build_candidates(self, transcript: List[Dict], niche_keywords: List[str],
                          min_len: float, max_len: float) -> List[Dict]:
        """Grow candidate clips from every transcript boundary.

        A candidate starts at transcript[i] and absorbs following segments
        until its duration reaches ``min_len``; every extension that still
        fits inside ``max_len`` is emitted. This guarantees the duration
        filter downstream can actually be satisfied.
        """
        candidates: List[Dict] = []
        n = len(transcript)

        # Emitting *every* valid extension of every start point is O(n * k)
        # and produced >20k candidates on a 30-minute source. We only need a
        # few well-spaced lengths per start point, so keep the first
        # MAX_LENGTHS_PER_START valid extensions (shortest ones, which are
        # the tightest cuts) and move on.
        MAX_LENGTHS_PER_START = 4

        for i in range(n):
            start = float(transcript[i]['start'])
            texts: List[str] = []
            emitted = 0

            for j in range(i, n):
                seg = transcript[j]
                texts.append((seg.get('text') or '').strip())
                end = float(seg['end'])
                duration = end - start

                if duration > max_len:
                    break
                if duration < min_len:
                    continue
                if emitted >= MAX_LENGTHS_PER_START:
                    break

                prev_seg = transcript[i - 1] if i > 0 else None
                next_seg = transcript[j + 1] if j + 1 < n else None

                candidate = {
                    'start': start,
                    'end': end,
                    'text': ' '.join(t for t in texts if t),
                    'first_index': i,
                    'last_index': j,
                }
                candidate['score'] = self.score_segment(
                    candidate, prev_seg, next_seg, niche_keywords
                )
                candidates.append(candidate)
                emitted += 1

        return candidates

    def find_highlight_segments(self, transcript: List[Dict],
                                niche_keywords: Optional[List[str]] = None,
                                min_segment_length: int = 15,
                                max_segment_length: int = 60,
                                min_gap_between: int = 30,
                                max_clips: int = 8,
                                min_score: float = 0.0,
                                max_candidates: Optional[int] = None) -> List[Dict]:
        """Select the best non-overlapping Shorts candidates.

        Args:
            transcript: Whisper segments with 'text', 'start', 'end'.
            niche_keywords: Keywords that boost a clip's score.
            min_segment_length: Shortest acceptable clip, seconds.
            max_segment_length: Longest acceptable clip, seconds.
            min_gap_between: Minimum spacing between two chosen clips.
            max_clips: Hard cap on clips returned per source video.
            min_score: Score floor; ignored if it would return nothing.
            max_candidates: Select this many ranked clips instead of
                ``max_clips``. Used to build a deep plan: transcription is the
                expensive stage, so once it is done, ranking 30 clips costs
                nothing extra and "give me 10 more" needs no re-download and no
                re-transcribe. Rendering is still capped separately by the
                caller.

        Returns:
            Chronologically sorted list of clips with start/end/text/score/rank.
            The ``rank`` field reflects priority order (1 = highest score).
        """
        if niche_keywords is None:
            niche_keywords = []
        if not transcript:
            logger.warning("Empty transcript, no highlights to find")
            return []

        transcript = sorted(
            (s for s in transcript if s.get('end') is not None and s.get('start') is not None),
            key=lambda s: float(s['start']),
        )

        min_len = float(min_segment_length)
        max_len = float(max_segment_length)
        if min_len > max_len:
            logger.warning(
                "min_segment_length (%.1f) > max_segment_length (%.1f); swapping",
                min_len, max_len,
            )
            min_len, max_len = max_len, min_len

        total_span = float(transcript[-1]['end']) - float(transcript[0]['start'])
        if total_span < min_len:
            # Source is shorter than one clip; relax so short sources still work.
            min_len = max(3.0, total_span * 0.6)
            logger.info(
                "Source span %.1fs is shorter than min clip length; relaxing min to %.1fs",
                total_span, min_len,
            )

        candidates = self._build_candidates(transcript, niche_keywords, min_len, max_len)
        if not candidates:
            logger.warning(
                "No candidate clips could be built from %d transcript segments "
                "(span %.1fs, band %.1f-%.1fs)",
                len(transcript), total_span, min_len, max_len,
            )
            return []

        # Prefer higher score, then longer clip as tie-break.
        candidates.sort(key=lambda c: (c['score'], c['end'] - c['start']), reverse=True)

        # How many clips to actually select. The deep plan wants every clip the
        # source can support; a plain run wants only what it will render.
        wanted = int(max_candidates) if max_candidates else int(max_clips)
        wanted = max(1, wanted)

        selected = self._select_non_overlapping(
            candidates, min_gap_between, wanted, min_score
        )

        # Never return zero clips just because the score floor was too strict:
        # a run that downloads and transcribes a 30-minute video and then
        # produces nothing is the failure mode we are fixing.
        if not selected and min_score > 0:
            logger.warning(
                "No clip cleared min_score=%.2f; falling back to top-scoring clips",
                min_score,
            )
            selected = self._select_non_overlapping(
                candidates, min_gap_between, wanted, 0.0
            )

        # Rank order is the plan's priority order and must survive the
        # chronological sort below -- otherwise "render the top 5" would mean
        # "render the 5 earliest", which is not the same thing at all.
        for position, clip in enumerate(
            sorted(selected, key=lambda c: (-c['score'], c['start'])), start=1
        ):
            clip['rank'] = position

        selected.sort(key=lambda c: c['start'])
        logger.info(
            "Selected %d highlight clips from %d candidates (%d transcript segments)",
            len(selected), len(candidates), len(transcript),
        )
        for idx, clip in enumerate(selected, 1):
            logger.info(
                "  clip %d: %.1f-%.1fs (%.1fs) score=%.2f | %s",
                idx, clip['start'], clip['end'], clip['end'] - clip['start'],
                clip['score'], clip['text'][:70].replace('\n', ' '),
            )
        return selected

    def _select_non_overlapping(self, candidates: List[Dict], min_gap: float,
                                max_clips: int, min_score: float) -> List[Dict]:
        """Greedy non-maximum suppression over scored candidates."""
        selected: List[Dict] = []

        for cand in candidates:
            if len(selected) >= max_clips:
                break
            if cand['score'] < min_score:
                continue

            conflict = False
            for chosen in selected:
                # Reject if the clips overlap, or sit closer than min_gap.
                if (cand['start'] < chosen['end'] + min_gap
                        and chosen['start'] < cand['end'] + min_gap):
                    conflict = True
                    break
            if conflict:
                continue

            selected.append({
                'start': cand['start'],
                'end': cand['end'],
                'text': cand['text'],
                'score': cand['score'],
            })

        return selected

    # ------------------------------------------------------------------
    def merge_close_segments(self, segments: List[Dict], max_gap: float = 5.0) -> List[Dict]:
        """Merge segments separated by less than ``max_gap`` seconds."""
        if not segments:
            return []

        ordered = sorted(segments, key=lambda x: float(x['start']))
        merged: List[Dict] = []
        current = dict(ordered[0])

        for nxt in ordered[1:]:
            if float(nxt['start']) - float(current['end']) <= max_gap:
                current['end'] = max(float(current['end']), float(nxt['end']))
                current['text'] = f"{current.get('text', '')} {nxt.get('text', '')}".strip()
                if 'score' in current and 'score' in nxt:
                    current['score'] = (current['score'] + nxt['score']) / 2
            else:
                merged.append(current)
                current = dict(nxt)

        merged.append(current)
        return merged
