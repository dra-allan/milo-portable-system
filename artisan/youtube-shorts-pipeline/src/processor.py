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
from typing import Dict, List, Optional, Tuple

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger

try:
    from .discovery import matched_keywords
except ImportError:  # pragma: no cover - direct script execution
    from discovery import matched_keywords

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

# ----------------------------------------------------------------------
# Ranking / countdown signals.
# ----------------------------------------------------------------------

# "number 3", "number three", "no. 3", "#3", "at 3:", "3." at a clause start.
_NUMBER_WORDS = (
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "twenty",
)

ENUMERATION_RE = re.compile(
    r"(?:\b(?:number|no\.?|coming in at|in at|next up at|at)\s*#?\s*"
    r"(?:\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\b)"
    r"|(?:#\s*\d{1,2}\b)",
    re.IGNORECASE,
)

# Fixed setup / payoff / reaction / question lexicons (from campaign clipper)
PAYOFF_RE = re.compile(
    r"\b(wait|watch|look|no way|oh my|finally|actually|literally|unbelievable|"
    r"insane|crazy|wow|did he|did she|i can'?t believe|there it is|here we go|"
    r"let'?s go|got him|that was|plot twist|victory|win|won|clutch|"
    r"number one|no\.?\s*1|#\s*1|top spot|first place|the winner|our winner|takes the crown|best of the bunch)\b",
    re.IGNORECASE,
)

SETUP_RE = re.compile(
    r"\b(because|so|but|if|when|then|first|next|about to|watch this|listen|"
    r"the plan|going to|gonna|trying to|challenge|unless|before)\b",
    re.IGNORECASE,
)

REACTION_RE = re.compile(
    r"\b(yes|yeah|no|wow|oh|damn|holy|wait|what|bro|unbelievable|crazy|"
    r"insane|let'?s go)\b",
    re.IGNORECASE,
)

QUESTION_RE = re.compile(
    r"\?|\b(are|is|was|were|do|does|did|can|could|will|would|should|have|has|"
    r"why|what|when|where|who|which|how)\b\s+\w+",
    re.IGNORECASE,
)

WORD_RE = re.compile(r"[\w']+")

# Comparative / superlative language that carries a ranking claim.
SUPERLATIVE_RE = re.compile(
    r"\b\w+(?:est)\b"
    r"|\b(?:most|least|best|worst|biggest|largest|smallest|fastest|slowest|"
    r"richest|deadliest|rarest|strangest|weirdest|craziest)\b"
    r"|\b(?:better|worse|cheaper|bigger|smaller|faster|slower|longer|shorter)"
    r"\s+than\b"
    r"|\b(?:versus|vs\.?)\b",
    re.IGNORECASE,
)


def score_text(text: str, required: Optional[List[str]] = None) -> Tuple[float, float, float, float]:
    """(setup, payoff, relevance, density) in 0..1 for a block of speech."""
    low = (text or '').lower()
    words = WORD_RE.findall(low)
    if not words:
        return 0.0, 0.0, 0.0, 0.0
    setup = min(1.0, len(SETUP_RE.findall(low)) / 2.0)
    payoff = min(1.0, (len(PAYOFF_RE.findall(low))
                       + 0.5 * len(REACTION_RE.findall(low))) / 3.0)
    relevance = min(1.0, sum(1 for token in (required or []) if token.lower() in low)
                    / max(1, len(required or []))) if required else 0.0
    density = min(1.0, len(words) / 70.0)
    return setup, payoff, relevance, density


def question_score(text: str) -> float:
    """How much question-shaped hook material this speech contains (0..1)."""
    hits = len(QUESTION_RE.findall(text or ''))
    return min(1.0, hits / 2.0)


def ranking_signals(text: str) -> Dict[str, int]:
    """Count enumeration, payoff and superlative cues in ``text``."""
    body = text or ''
    return {
        'enumerations': len(ENUMERATION_RE.findall(body)),
        'payoffs': len(PAYOFF_RE.findall(body)),
        'superlatives': len(SUPERLATIVE_RE.findall(body)),
    }


def opens_on_enumeration(text: str, window: int = 60) -> bool:
    """True if an enumeration cue appears in the first ``window`` characters."""
    head = (text or '')[:window]
    return bool(ENUMERATION_RE.search(head))


class ContentProcessor:
    """Scores transcript regions and selects the best Shorts candidates."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def score_segment(self, segment: Dict, prev_segment: Optional[Dict] = None,
                      next_segment: Optional[Dict] = None,
                      niche_keywords: Optional[List[str]] = None,
                      ranking_mode: bool = False,
                      story_mode: bool = False) -> float:
        """Score one candidate region for "interestingness".

        Returns a non-negative score. Kept backwards compatible with the
        previous signature because tests and callers rely on it.

        Args:
            ranking_mode: enable countdown/list scoring.
            story_mode: enable question/setup/payoff text scoring for hook->story->payoff restructure.
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

        # 2. Niche keywords.
        keyword_hits = len(matched_keywords(text, niche_keywords))
        score += min(keyword_hits, 6) * 2.5

        # 3. Hook phrases.
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

        # 8. Filler penalty.
        tokens = re.findall(r"[a-z']+", text)
        filler_count = sum(1 for t in tokens if t in FILLER_WORDS)
        filler_count += sum(text.count(p) for p in FILLER_WORDS if ' ' in p)
        score -= (filler_count / word_count) * 8.0

        # 9. Duration sweet spot for Shorts retention (20-45s).
        if 20.0 <= duration <= 45.0:
            score += 3.0
        elif duration < 12.0:
            score -= 2.0

        # 10. Ranking / countdown structure (opt-in per niche).
        if ranking_mode:
            signals = ranking_signals(raw_text)
            if opens_on_enumeration(raw_text):
                score += 7.0
            elif signals['enumerations']:
                score += 2.0
            else:
                score -= 3.0
            score += min(signals['payoffs'], 2) * 5.0
            score += min(signals['superlatives'], 5) * 1.2
            if signals['enumerations'] > 3:
                score -= (signals['enumerations'] - 3) * 1.5

        # 11. Story / restructure signals (opt-in per niche via story_mode / edit_story flag).
        if story_mode:
            setup, payoff, relevance, _ = score_text(raw_text, niche_keywords)
            q_val = question_score(raw_text)
            score += setup * 4.0 + payoff * 5.0 + q_val * 4.0 + relevance * 2.0

        return max(0.0, score)

    # ------------------------------------------------------------------
    # Candidate construction
    # ------------------------------------------------------------------
    def _build_candidates(self, transcript: List[Dict], niche_keywords: List[str],
                          min_len: float, max_len: float,
                          ranking_mode: bool = False,
                          story_mode: bool = False) -> List[Dict]:
        """Grow candidate clips from every transcript boundary."""
        candidates: List[Dict] = []
        n = len(transcript)
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
                    candidate, prev_seg, next_seg, niche_keywords,
                    ranking_mode=ranking_mode,
                    story_mode=story_mode,
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
                                max_candidates: Optional[int] = None,
                                ranking_mode: bool = False,
                                story_mode: bool = False) -> List[Dict]:
        """Select the best non-overlapping Shorts candidates."""
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
            min_len = max(3.0, total_span * 0.6)
            logger.info(
                "Source span %.1fs is shorter than min clip length; relaxing min to %.1fs",
                total_span, min_len,
            )

        candidates = self._build_candidates(
            transcript, niche_keywords, min_len, max_len,
            ranking_mode=ranking_mode,
            story_mode=story_mode,
        )
        if not candidates:
            logger.warning(
                "No candidate clips could be built from %d transcript segments "
                "(span %.1fs, band %.1f-%.1fs)",
                len(transcript), total_span, min_len, max_len,
            )
            return []

        candidates.sort(key=lambda c: (c['score'], c['end'] - c['start']), reverse=True)

        wanted = int(max_candidates) if max_candidates else int(max_clips)
        wanted = max(1, wanted)

        selected = self._select_non_overlapping(
            candidates, min_gap_between, wanted, min_score
        )

        if not selected and min_score > 0:
            logger.warning(
                "No clip cleared min_score=%.2f; falling back to top-scoring clips",
                min_score,
            )
            selected = self._select_non_overlapping(
                candidates, min_gap_between, wanted, 0.0
            )

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
