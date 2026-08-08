"""Rule-based title optimizer for Shorts.

Turns a raw clip hook (the first words of a clip) into a curiosity-gap
headline. No LLM, no network, no external deps: pure stdlib rules.

Design constraints:
  * Never fabricate a claim. We shorten, strip filler, and *frame* the hook;
    we do not invent facts that are not in the transcript.
  * No em-dashes (channel-wide style rule).
  * Titles stay under ``max_len`` chars so YouTube shows the whole thing.
  * Stable: same hook + niche + clip index always yields the same title.

Framing is conservative: a hook that is already a question, already contains a
curiosity keyword ("secret", "real reason", "nobody tells you"...), or is very
short is left mostly intact. Plain long statements get a short ``Label: ...``
opener drawn from the niche's keyword bank when one is available.
"""

import re

# Words that, as a sentence lead-in, add nothing. Stripped repeatedly.
FILLER_LEADS = [
    "so", "and then", "and", "but", "you know", "i mean", "basically",
    "honestly", "look", "listen", "well", "um", "uh", "like", "right",
    "now", "as a result", "the thing is", "here's the thing", "the truth is",
]

# If the hook already contains any of these, it already has a hook: leave it.
STRONG_HOOK_MARKERS = [
    "secret", "real reason", "nobody tells you", "nobody talks about",
    "the truth", "this is why", "here's why", "that's why", "the reason",
    "mistake", "worst", "best", "most people", "nobody",
]

# Generic label openers, mapped from niche keywords to a framing. The label is
# always short and the hook follows after ": ". Picked deterministically from
# the niche's keyword bank so each niche gets a stable flavor.
NICHE_FRAME = {
    'capital_mindset': "The money lesson",
    'flick_shorts': "The brutal truth",
    'future_tech_daily': "The tech shift",
    'peak_human_lab': "The body's truth",
    'untold_frontlines': "The real story",
    'psychology_behavior': "The psychology",
    'self_improvement': "The lesson",
    'relationships_dating': "What he/she won't say",
    'personal_finance': "The money mistake",
    'real_estate_wealth': "The property play",
    'crypto_web3': "The crypto move",
    'history_mysteries': "What history hides",
    'true_crime_cases': "The case file",
    'productivity_career': "The career hack",
    'fitness_strength': "The training truth",
    'nutrition_metabolism': "The nutrition truth",
    'stoicism_philosophy': "The stoic truth",
    'science_space': "The discovery",
    'books_big_ideas': "The big idea",
    'documentaries_society': "The hidden world",
    'creator_economy_marketing': "The creator play",
    'geopolitics_power': "The power move",
}

GENERIC_FRAME = "Here's the part people miss"


def _clean(text: str) -> str:
    text = re.sub(r'\s+', ' ', text or '').strip().strip('"\'')
    lowered = text.lower()
    changed = True
    while changed:
        changed = False
        for lead in FILLER_LEADS:
            if lowered.startswith(lead + ' '):
                text = text[len(lead):].strip()
                lowered = text.lower()
                changed = True
                break
            if lowered.startswith(lead + ','):
                text = text[len(lead):].strip()
                lowered = text.lower()
                changed = True
                break
    # Strip leading punctuation left behind by filler removal.
    text = re.sub(r'^[,\s.:;]+', '', text).strip()
    return text


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    # Cut at the last space inside the limit, then re-trim trailing junk.
    space = cut.rfind(' ')
    if space > max_len * 0.6:
        cut = cut[:space]
    cut = cut.rstrip(',.;: ')
    # If a sentence boundary is visible inside the limit, keep it clean.
    for boundary in ('. ', '! ', '? '):
        idx = cut.find(boundary)
        if 0 < idx < max_len:
            result = cut[:idx + 1]
            return result if len(result) <= max_len else result[:max_len - 3] + '...'
    result = cut + '...'
    if len(result) > max_len:
        result = result[:max_len - 3] + '...'
    return result


def optimize_title(hook: str, niche: str = '', keywords=None,
                   clip_index: int = 0, max_len: int = 72) -> str:
    """Return an optimized, hashtag-free title for a clip hook."""
    text = _clean(hook)
    if not text:
        return f"{niche or 'short'} clip #{clip_index}"

    lowered = text.lower()
    ends_question = text.rstrip().endswith('?')
    ends_bang = text.rstrip().endswith('!')

    # Already a question or exclamation: keep the energy, just trim length.
    if ends_question or ends_bang:
        return _truncate(text, max_len)

    # Short hook (< ~8 words) or already strong: leave the hook as the title.
    if len(text.split()) <= 8 or any(m in lowered for m in STRONG_HOOK_MARKERS):
        return _truncate(text, max_len)

    # Plain statement. Frame it with the niche's label so the title reads as a
    # headline, then trim the whole thing to budget if needed.
    frame = NICHE_FRAME.get(niche) or GENERIC_FRAME
    framed = f"{frame}: {text}"
    if len(framed) <= max_len:
        return framed
    return _truncate(framed, max_len)
