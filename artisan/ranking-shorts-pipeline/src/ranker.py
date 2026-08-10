"""Turn vetted clips into a ranked countdown.

Scoring is deliberately simple and explainable: motion, cleanliness, a small
bonus for engagement on the source, and a penalty for anything the vetting pass
flagged but tolerated. A learned model here would be unauditable and there is
no feedback signal yet.

The ordering rule is the part that matters.

A countdown plays 5 -> 1, and the naive assignment is "worst clip is #5". That
puts the weakest clip in the first three seconds of the video, which is the
only part most viewers watch: they swipe before the good one arrives. The
reference workflow says it directly - open on a strong, engaging clip.

So: the best clip takes **#1** (the payoff, last), the *second* best opens the
video at **#5** (the hook), and the remainder fill 2-4 in descending order. The
viewer gets something good immediately and something better at the end.
"""

from typing import Dict, List

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__)


def score(clip: Dict) -> float:
    motion = float(clip.get('motion_score') or 0.0)
    coverage = float(clip.get('text_coverage') or 0.0)
    music = float(clip.get('music_confidence') or 0.0)
    wps = float(clip.get('words_per_second') or 0.0)
    views = float(clip.get('views') or 0.0)

    # Views are a weak signal (a clip can be great and undiscovered) so they
    # are log-compressed and capped rather than dominating the sort.
    import math
    view_bonus = min(0.25, math.log10(max(views, 1.0)) / 28.0)

    value = (0.55 * motion
             + 0.20 * (1.0 - min(1.0, coverage / 0.18))
             + 0.15 * (1.0 - min(1.0, music / 0.55))
             + view_bonus)
    # Any residual speech competes with the voice-over that gets laid over it.
    value -= 0.10 * min(1.0, wps / 0.45)
    return round(max(0.0, value), 4)


def rank(clips: List[Dict], count: int = None) -> List[Dict]:
    """Return up to ``count`` clips in *playback* order (rank N first).

    Each returned clip gains ``rank`` and ``score``.
    """
    count = count or int(config.get('clips_per_video', 5))
    scored = sorted(clips, key=score, reverse=True)[:count]
    for clip in scored:
        clip['score'] = score(clip)

    total = len(scored)
    if total == 0:
        return []
    if total == 1:
        scored[0]['rank'] = 1
        return scored

    assignments: Dict[int, Dict] = {}
    assignments[1] = scored[0]           # best clip is the payoff
    assignments[total] = scored[1]       # second best opens the video
    remaining = scored[2:]
    # Fill 2..total-1 best-first, so quality rises toward #1.
    for offset, clip in enumerate(remaining):
        assignments[2 + offset] = clip

    ordered = [assignments[r] for r in sorted(assignments, reverse=True)]
    for position in sorted(assignments):
        assignments[position]['rank'] = position

    logger.info('ranking: %s', ', '.join(
        f"#{c['rank']}={c['score']}" for c in ordered))
    return ordered
