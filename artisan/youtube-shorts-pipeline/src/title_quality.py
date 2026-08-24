"""Title quality gate.

2026-08-24 fleet audit finding: titles were raw transcript hooks chopped
mid-sentence (``safe_hook[:50]``) and then further truncated by a blind
``[:100]`` at upload. Suppressed channels plus spam-looking metadata reinforce
each other, so every title now passes through here before it reaches YouTube.

Three entry points:

* :func:`clean_hook` -- tidy a raw transcript hook for use as a title base.
  Cuts at a sentence boundary when one exists inside the limit, never ends
  mid-word, and strips dangling conjunctions/prepositions/articles from the
  end ("...AND I?" is exactly the disease).
* :func:`clean_full_title` -- final pass over ``<title> #niche #Shorts`` so
  hashtags are never chopped in half.
* :func:`title_is_spammy` -- lint used at upload time; returns reasons, does
  not raise. Callers log the reasons and proceed with the cleaned text.
"""
from __future__ import annotations

import re

# Words that must not end a title: the clip was cut mid-thought and keeping
# them reads as broken metadata to both viewers and the spam filter.
_DANGLING = {
    'and', 'or', 'but', 'so', 'because', 'that', 'which', 'who', 'whom',
    'when', 'while', 'if', 'then', 'than', 'the', 'a', 'an', 'of', 'to',
    'in', 'on', 'for', 'with', 'at', 'by', 'from', 'about', 'into', 'over',
    'is', 'are', 'was', 'were', 'be', 'been', 'being', 'am',
    'i', "i'm", "i've", "i'll", 'my', 'we', 'our', 'you', 'your', "you're",
    'he', 'she', 'it', 'they', 'them', 'his', 'her', 'their', 'this', 'these',
    'those', 'there', 'here', 'what', 'why', 'how', 'do', 'does', 'did',
    'have', 'has', 'had', 'will', 'would', 'can', 'could', 'should', 'just',
    'and i', 'to be', 'is a', 'in the', 'on the', 'to the', 'of the',
}

_FILLER_START = re.compile(
    r"^(um+|uh+|erm|you know,?|i mean,?|like,?|so,?|well,?|and,?)\s+", re.I)

_SENTENCE_END = re.compile(r'[.!?](?=\s|$)')

_WHITESPACE = re.compile(r'\s+')


def _strip_dangling(text: str) -> str:
    """Drop trailing fragments that read as a cut-off thought."""
    words = text.split()
    while words:
        tail = ' '.join(words[-2:]).lower().rstrip('.,!?;:')
        single = words[-1].lower().rstrip('.,!?;:')
        if tail in _DANGLING and len(words) > 1:
            words.pop()
            continue
        if single in _DANGLING or single in {'.', ',', '!', '?', ';', ':'}:
            words.pop()
            continue
        break
    return ' '.join(words)


def clean_hook(hook: str, max_len: int = 70) -> str:
    """Clean a raw transcript hook into a usable title base.

    Cuts at the last sentence boundary within *max_len* when one occurs far
    enough in (>25 chars) to keep substance; otherwise cuts at the last word
    boundary. Always strips dangling end-words afterwards.
    """
    text = _WHITESPACE.sub(' ', str(hook or '')).strip()
    if not text:
        return ''
    while True:
        stripped = _FILLER_START.sub('', text)
        if stripped == text:
            break
        text = stripped

    if len(text) <= max_len:
        cleaned = _strip_dangling(text)
        return cleaned if len(cleaned) >= 12 else text.rstrip('.,!?;:')

    window = text[:max_len + 1]
    cut = None
    for match in _SENTENCE_END.finditer(window):
        if match.start() >= 25:
            cut = match.start() + 1
    if cut:
        candidate = window[:cut]
    else:
        sp = window.rfind(' ')
        candidate = window[:sp] if sp > 25 else window[:max_len]
    cleaned = _strip_dangling(candidate)
    return cleaned if len(cleaned) >= 12 else candidate.rstrip('.,!?;:')


def split_hashtags(title: str, max_tags: int = 4):
    """Split trailing '#tag' tokens off the front matter of a title."""
    parts = str(title or '').split()
    tags = []
    while parts and len(tags) < max_tags and parts[-1].startswith('#'):
        tags.insert(0, parts.pop())
    return ' '.join(parts), ' '.join(tags)


def clean_full_title(title: str, max_len: int = 95) -> str:
    """Final pass on ``base #niche #Shorts``: never chop a hashtag in half."""
    text = _WHITESPACE.sub(' ', str(title or '')).strip()
    if len(text) <= max_len:
        return text
    main, tags = split_hashtags(text)
    room = max_len - (len(tags) + 1 if tags else 0)
    if len(main) > room:
        main = clean_hook(main, max_len=room)
    return f'{main} {tags}'.strip() if tags else main


def title_is_spammy(title: str, max_len: int = 100) -> list:
    """Return a list of lint reasons, empty when the title looks healthy."""
    reasons = []
    text = str(title or '').strip()
    if not text:
        return ['empty']
    if len(text) > max_len:
        reasons.append(f'too_long({len(text)}>{max_len})')
    last_word = text.split()[-1].lower().rstrip('.,!?;:') if text.split() else ''
    if last_word in _DANGLING:
        reasons.append(f'ends_dangling({last_word})')
    if text.endswith(('...', '..')):
        reasons.append('ends_ellipsis')
    if re.search(r'\s[a-z]{1,2}$', text):
        reasons.append('ends_fragment')
    return reasons
