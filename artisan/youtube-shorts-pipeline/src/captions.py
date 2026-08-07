"""Word-level viral caption engine (ASS output, burned in by FFmpeg).

WHY THIS MODULE EXISTS
----------------------
The previous captioning path took a whole Whisper *segment* (typically 5-10
seconds of speech, 15-25 words), wrapped it onto two lines, and displayed the
whole block for the segment's full duration. That produces a paragraph sitting
statically on screen -- the opposite of what viral Shorts do. The style presets
made it worse rather than better:

* ``_process_hormozi_style`` truncated the text to the first 3 words and threw
  the rest away, so the caption silently *lost* most of the speech.
* ``_process_pop_style`` and ``_process_kinetic_style`` were documented as
  word-by-word / karaoke but their bodies were ``return text`` -- no-ops.
* Every preset named fonts (Impact, Montserrat, Bebas Neue, Komika Axis) with
  no check that they exist. libass silently substitutes its default when a
  family is missing, so the "styles" mostly rendered as the same plain font.

WHAT VIRAL CAPTIONS ACTUALLY DO
-------------------------------
1. 1-4 words on screen at a time, never a paragraph.
2. Each word appears exactly when it is spoken (word-level timestamps).
3. One word per group is emphasised in colour (yellow), with a stronger colour
   (red) reserved for genuine punch words.
4. A short entrance animation (scale pop) on each group, plus an extra bounce
   on the emphasised word.
5. Line breaks that keep the block balanced and inside the platform safe zone.

The engine is deliberately pure-Python and independent of FFmpeg: it consumes
transcript segments carrying ``words`` and returns ASS text. That makes the
grouping and emphasis rules directly unit-testable without rendering a frame,
which is how the timing invariants below are pinned down.

KEY INVARIANT
-------------
Groups never overlap in time and are always clamped to the clip. Overlapping
dialogue lines are the classic cause of "two captions stacked on top of each
other" -- libass will happily draw both.
"""

import re
from typing import Dict, List, Optional, Sequence, Tuple

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Emphasis lexicons
# ---------------------------------------------------------------------------
# Words that carry no meaning on their own. An emphasised "THE" looks broken,
# so function words are never chosen as the highlight.
STOPWORDS = frozenset("""
a an and are as at be been being but by for from had has have he her hers him
his how i if in into is it its me my of on or our ours she so than that the
their theirs them then there these they this those to too us was we were what
when where which who whom why will with you your yours am does did do doing
would could should can may might must shall about above after again against
all also any because before below between both during each few further here
more most no nor not now once only other out over own same some such through
under until up very while
""".split())

# Punch words: high-arousal language that earns the strongest colour. These are
# the words a creator would shout, so they get red rather than yellow.
PUNCH_WORDS = frozenset("""
never always everyone nobody nothing everything free instantly literally
insane crazy brutal shocking terrible awful worst best biggest hardest fastest
easiest huge massive enormous destroyed exploded collapsed skyrocketed
guaranteed proven secret hidden banned illegal dangerous deadly fatal
lose losing lost failed failing failure broke broken bankrupt debt
million billion trillion thousand
stop wrong mistake mistakes myth lie lies scam truth
myself yourself himself herself themselves
""".split())

# Emotional / evaluative language: emphasised in yellow.
EMPHASIS_WORDS = frozenset("""
amazing incredible unbelievable awesome perfect beautiful terrible horrible
love hate fear angry happy sad excited scared worried surprised shocked
important critical essential vital crucial key main biggest smallest
first last only real actual genuine honest simple hard easy difficult
better worse great good bad huge tiny fast slow rich poor smart stupid
win won winning beat crush dominate succeed success grow growth profit money
cash revenue sales business client customer boss job career
work working worked trying tried try change changed different
problem solution answer question reason result
believe think know understand realise realize learn learned discovered
happen happened happening
everyone anyone someone people person
today tomorrow yesterday forever never again
""".split())

# Strong verbs get emphasis when nothing better is available in the group.
STRONG_VERB_SUFFIXES = ('ize', 'ise', 'ify')

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
# Sentence-ending punctuation forces a caption break: a group that runs across
# a full stop reads as one thought when it is two.
_SENTENCE_END_RE = re.compile(r"[.!?]+[\"')\]]*\s*$")


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
class CaptionPreset:
    """Visual parameters for one caption look.

    Colours are stored as ASS ``&HBBGGRR`` (ASS is BGR, not RGB -- getting this
    backwards is why "yellow" renders as sky blue).
    """

    def __init__(self, name: str, font: str, font_size: int,
                 primary: str = '&H00FFFFFF', emphasis: str = '&H0000D7FF',
                 punch: str = '&H002B2BFF', outline: int = 6, shadow: int = 3,
                 margin_v: int = 420, max_words: int = 4, uppercase: bool = True,
                 pop: bool = True, bounce: bool = True, spacing: int = 0,
                 outline_colour: str = '&H00000000'):
        self.name = name
        self.font = font
        self.font_size = font_size
        self.primary = primary
        self.emphasis = emphasis
        self.punch = punch
        self.outline = outline
        self.shadow = shadow
        self.margin_v = margin_v
        self.max_words = max_words
        self.uppercase = uppercase
        self.pop = pop
        self.bounce = bounce
        self.spacing = spacing
        self.outline_colour = outline_colour


# &H00FFFFFF white / &H0000D7FF gold / &H002B2BFF red / &H0000FF00 green
# &H00F0FF00 cyan-ish highlight used by the 'neon' look.
PRESETS: Dict[str, CaptionPreset] = {
    # The default viral look: big, white, thick outline, gold emphasis.
    'viral': CaptionPreset(
        'viral', font='Montserrat ExtraBold', font_size=104,
        emphasis='&H0000D7FF', punch='&H002B2BFF',
        outline=7, shadow=4, margin_v=430, max_words=4,
    ),
    # Hormozi-style: tighter groups, all caps, gold/green money words.
    'hormozi': CaptionPreset(
        'hormozi', font='Anton', font_size=112,
        emphasis='&H0000D7FF', punch='&H002B2BFF',
        outline=8, shadow=4, margin_v=400, max_words=3,
    ),
    # Karaoke feel: whole group visible, spoken word lights up.
    'kinetic': CaptionPreset(
        'kinetic', font='Montserrat ExtraBold', font_size=96,
        emphasis='&H0000D7FF', punch='&H002B2BFF',
        outline=6, shadow=3, margin_v=430, max_words=4, bounce=False,
    ),
    # One word at a time, maximum punch.
    'single': CaptionPreset(
        'single', font='Anton', font_size=128,
        emphasis='&H0000D7FF', punch='&H002B2BFF',
        outline=8, shadow=5, margin_v=440, max_words=1,
    ),
    # Quieter: mixed case, no colour emphasis, no pop.
    'minimalist': CaptionPreset(
        'minimalist', font='Montserrat SemiBold', font_size=84,
        emphasis='&H00FFFFFF', punch='&H00FFFFFF',
        outline=4, shadow=2, margin_v=420, max_words=4,
        uppercase=False, pop=False, bounce=False,
    ),
    'neon': CaptionPreset(
        'neon', font='Montserrat ExtraBold', font_size=100,
        emphasis='&H00F0FF00', punch='&H00FF00FF',
        outline=7, shadow=4, margin_v=430, max_words=3,
    ),
}
DEFAULT_PRESET = 'viral'

# Font fallbacks, best first. libass silently substitutes a missing family, so
# the renderer resolves these against fontconfig and picks one that exists --
# otherwise "Montserrat ExtraBold" quietly becomes whatever the default is and
# the caption looks nothing like the preset.
FONT_FALLBACKS: Dict[str, Tuple[str, ...]] = {
    'Montserrat ExtraBold': (
        'Montserrat ExtraBold', 'Montserrat Black', 'Montserrat Bold',
        'Anton', 'Arial Black', 'Impact', 'Archivo Black',
        'Liberation Sans Bold', 'DejaVu Sans Bold', 'Liberation Sans',
        'DejaVu Sans',
    ),
    'Montserrat SemiBold': (
        'Montserrat SemiBold', 'Montserrat Medium', 'Montserrat',
        'Liberation Sans', 'DejaVu Sans',
    ),
    'Anton': (
        'Anton', 'Oswald', 'Bebas Neue', 'Arial Black', 'Impact',
        'Archivo Black', 'Montserrat ExtraBold', 'Liberation Sans Narrow',
        'Liberation Sans Bold', 'DejaVu Sans Bold', 'DejaVu Sans',
    ),
}


# ---------------------------------------------------------------------------
# Word model
# ---------------------------------------------------------------------------
class CaptionWord:
    """One spoken word with its own timing and emphasis decision."""

    __slots__ = ('text', 'start', 'end', 'emphasis', 'ends_sentence')

    def __init__(self, text: str, start: float, end: float,
                 emphasis: str = 'none', ends_sentence: bool = False):
        self.text = text
        self.start = float(start)
        self.end = float(end)
        self.emphasis = emphasis          # 'none' | 'emphasis' | 'punch'
        self.ends_sentence = ends_sentence

    @property
    def core(self) -> str:
        """Letters/digits only, lowercased -- the form used for lexicon hits."""
        m = _WORD_RE.findall(self.text.lower())
        return m[0] if m else ''

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.text!r} {self.start:.2f}-{self.end:.2f} {self.emphasis}>"


def extract_words(segments: Sequence[Dict], time_offset: float = 0.0,
                  clip_duration: Optional[float] = None) -> List[CaptionWord]:
    """Flatten transcript segments into a clean, ordered word list.

    Whisper word timings need real defensive handling; these are all cases that
    occur in practice on ordinary podcast audio:

    * ``words`` missing entirely (the discovery profile runs with
      ``word_timestamps=False``) -- the caller falls back to segment timing.
    * zero-length or negative words, where start == end.
    * words whose timings run *backwards* relative to the previous word.
    * duplicated words across window overlaps in the windowed transcription
      path.

    Args:
        segments: transcript segments, each optionally carrying ``words``.
        time_offset: subtracted from every timestamp, to rebase source-timeline
            transcripts onto a clip that starts at 0.
        clip_duration: words fully outside [0, duration] are dropped and
            partial overlaps are clamped.
    """
    words: List[CaptionWord] = []
    for seg in segments or []:
        raw_words = seg.get('words') or []
        for w in raw_words:
            text = str(w.get('word', '')).strip()
            if not text:
                continue
            start, end = w.get('start'), w.get('end')
            if start is None or end is None:
                continue
            start = float(start) - time_offset
            end = float(end) - time_offset
            if end < start:
                start, end = end, start
            if clip_duration is not None:
                if start >= clip_duration or end <= 0:
                    continue
                start = max(0.0, start)
                end = min(end, float(clip_duration))
            else:
                if end <= 0:
                    continue
                start = max(0.0, start)
            # A zero-length word would render for 0 frames, i.e. never appear.
            if end - start < 0.04:
                end = start + 0.04
            words.append(CaptionWord(text, start, end))

    words.sort(key=lambda x: (x.start, x.end))

    # Enforce a strictly forward timeline. Whisper occasionally emits a word
    # that starts before the previous one ended; left alone that makes two
    # groups overlap and libass draws them on top of each other.
    cleaned: List[CaptionWord] = []
    for w in words:
        if cleaned:
            prev = cleaned[-1]
            if w.start < prev.end:
                w.start = prev.end
            if w.end <= w.start:
                w.end = w.start + 0.04
            # Drop an exact repeat produced by window overlap de-duplication.
            if w.core and w.core == prev.core and (w.start - prev.end) < 0.02:
                continue
        cleaned.append(w)

    for w in cleaned:
        w.ends_sentence = bool(_SENTENCE_END_RE.search(w.text))
    return cleaned


def words_from_segment_text(segments: Sequence[Dict], time_offset: float = 0.0,
                            clip_duration: Optional[float] = None) -> List[CaptionWord]:
    """Synthesise word timings by spreading each segment over its own words.

    Used when the transcript has no word-level data at all. The result is not
    as tight as real alignment, but it still yields 1-4 word groups that change
    continuously rather than a static paragraph -- a large visual improvement
    over the old behaviour, and it degrades gracefully.
    """
    out: List[CaptionWord] = []
    for seg in segments or []:
        text = str(seg.get('text', '')).strip()
        if not text:
            continue
        tokens = text.split()
        if not tokens:
            continue
        start = float(seg.get('start', 0.0)) - time_offset
        end = float(seg.get('end', 0.0)) - time_offset
        if end <= start:
            end = start + 0.4 * len(tokens)
        # Weight by word length: "extraordinarily" takes longer to say than "a".
        weights = [max(1, len(t)) for t in tokens]
        total = float(sum(weights))
        cursor = start
        for token, weight in zip(tokens, weights):
            share = (end - start) * (weight / total)
            w_start, w_end = cursor, cursor + share
            cursor = w_end
            if clip_duration is not None:
                if w_start >= clip_duration or w_end <= 0:
                    continue
                w_start = max(0.0, w_start)
                w_end = min(w_end, float(clip_duration))
            else:
                if w_end <= 0:
                    continue
                w_start = max(0.0, w_start)
            if w_end - w_start < 0.04:
                w_end = w_start + 0.04
            out.append(CaptionWord(token, w_start, w_end))
    out.sort(key=lambda x: x.start)
    for w in out:
        w.ends_sentence = bool(_SENTENCE_END_RE.search(w.text))
    return out


# ---------------------------------------------------------------------------
# Emphasis detection
# ---------------------------------------------------------------------------
def _is_number(core: str) -> bool:
    return any(ch.isdigit() for ch in core)


def score_word_importance(word: CaptionWord, keywords: Sequence[str] = ()) -> float:
    """How much this word deserves to be the highlighted one in its group.

    Ranked so that concrete, high-information words win: niche keywords and
    numbers first, then punch words, then emotional words, then long/rare
    words. Function words score 0 and can never win.
    """
    core = word.core
    if not core:
        return 0.0
    if core in STOPWORDS:
        return 0.0

    score = 0.0
    kw = {str(k).lower().strip() for k in (keywords or []) if str(k).strip()}
    if core in kw or any(core in k.split() for k in kw):
        score += 6.0
    if _is_number(core):
        score += 5.0
    if core in PUNCH_WORDS:
        score += 4.0
    if core in EMPHASIS_WORDS:
        score += 3.0
    if core.endswith(STRONG_VERB_SUFFIXES):
        score += 1.5
    # Longer words are more content-bearing than short ones.
    score += min(len(core), 12) / 8.0
    # Words the speaker lingered on are usually the ones being stressed.
    if (word.end - word.start) > 0.42:
        score += 1.0
    # A word carrying sentence-final punctuation lands the point.
    if word.ends_sentence:
        score += 0.5
    return score


def assign_emphasis(groups: List[List[CaptionWord]], keywords: Sequence[str] = (),
                    punch_ratio: float = 0.22) -> None:
    """Mark exactly one word per group (at most) as emphasis or punch.

    Rules that keep it from looking like a highlighter accident:

    * One emphasised word per group maximum -- two colours in a 3-word group
      reads as noise.
    * A group whose best word is only a function word gets no emphasis at all,
      so "AND THEN THE" stays plain white.
    * Red (punch) is rationed to roughly ``punch_ratio`` of groups and reserved
      for the strongest words, because a red word every group stops reading as
      emphasis.
    """
    scored: List[Tuple[float, CaptionWord]] = []
    for group in groups:
        best: Optional[CaptionWord] = None
        best_score = 0.0
        for word in group:
            s = score_word_importance(word, keywords)
            if s > best_score:
                best, best_score = word, s
        # 2.0 filters out "merely long" words with no semantic weight.
        if best is not None and best_score >= 2.0:
            best.emphasis = 'emphasis'
            scored.append((best_score, best))

    if not scored or punch_ratio <= 0:
        return

    # Promote the strongest handful to punch (red), respecting the ratio.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    budget = max(1, int(round(len(groups) * float(punch_ratio))))
    for score, word in scored[:budget]:
        # Only genuinely strong words earn red; a merely "important" noun stays
        # gold. Without this floor a calm sentence still gets a red word.
        if score >= 4.0 or word.core in PUNCH_WORDS or _is_number(word.core):
            word.emphasis = 'punch'


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------
def group_words(words: Sequence[CaptionWord], max_words: int = 4,
                max_duration: float = 1.9, max_chars: int = 22,
                gap_threshold: float = 0.42) -> List[List[CaptionWord]]:
    """Split the word stream into 1-``max_words`` caption groups.

    A group is closed when any of these is true, which is what makes the
    captions feel like they are reacting to the speaker rather than ticking
    along on a fixed cadence:

    * it is full (``max_words``),
    * adding the next word would exceed ``max_chars`` (keeps the block inside
      the frame at a large font size),
    * the group has been on screen for ``max_duration``,
    * the speaker paused (``gap_threshold``) -- a natural phrase boundary,
    * the last word ended a sentence.
    """
    groups: List[List[CaptionWord]] = []
    current: List[CaptionWord] = []
    max_words = max(1, int(max_words))

    def flush():
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for word in words:
        if current:
            prev = current[-1]
            visible_chars = sum(len(w.text) for w in current) + len(current)
            if (len(current) >= max_words
                    or prev.ends_sentence
                    or (word.start - prev.end) >= gap_threshold
                    or (word.end - current[0].start) > max_duration
                    or (visible_chars + len(word.text)) > max_chars):
                flush()
        current.append(word)
    flush()
    return groups


# ---------------------------------------------------------------------------
# ASS generation
# ---------------------------------------------------------------------------
def _ass_time(seconds: float) -> str:
    """ASS timestamps are H:MM:SS.cc, floored to centiseconds and never < 0."""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape_text(text: str) -> str:
    """Escape a token for ASS.

    ``{`` and ``}`` delimit override blocks, so a literal brace in the speech
    would silently swallow the rest of the line as an unknown tag. Backslashes
    likewise introduce tags and are stripped rather than escaped, because a
    hard line break mid-word is never what we want.
    """
    text = str(text)
    text = text.replace('\\', '').replace('{', '(').replace('}', ')')
    return text.strip()


def resolve_font(preferred: str, available: Optional[Sequence[str]] = None) -> str:
    """Pick the first font in the fallback chain that actually exists.

    Guards against libass' silent substitution: without this, a preset naming
    a font the box does not have renders in the default family and every
    "style" looks identical.
    """
    chain = FONT_FALLBACKS.get(preferred, (preferred,))
    if available is None:
        return chain[0]
    lowered = {str(a).strip().lower() for a in available}
    for candidate in chain:
        if candidate.strip().lower() in lowered:
            return candidate
    return chain[-1]


def build_ass(groups: Sequence[Sequence[CaptionWord]], preset: CaptionPreset,
              play_res: Tuple[int, int] = (1080, 1920),
              font_name: Optional[str] = None,
              clip_duration: Optional[float] = None) -> str:
    """Render caption groups to a complete ASS document.

    Two words on the animation: the entrance uses ``\\fscx/\\fscy`` transforms
    rather than ``\\move`` because scaling is composited without shifting the
    text baseline, so the block does not appear to drift. And every group's end
    time is clamped against the *next* group's start, which is what guarantees
    only one caption is ever on screen.
    """
    width, height = play_res
    font = font_name or preset.font

    head = [
        '[Script Info]',
        'Title: Viral Shorts captions',
        'ScriptType: v4.00+',
        'WrapStyle: 2',                 # we place our own line breaks
        'ScaledBorderAndShadow: yes',
        'YCbCr Matrix: TV.709',
        f'PlayResX: {width}',
        f'PlayResY: {height}',
        '',
        '[V4+ Styles]',
        ('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
         'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
         'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
         'Alignment, MarginL, MarginR, MarginV, Encoding'),
        # Alignment 2 = bottom-centre; MarginV lifts it into the safe zone,
        # clear of the Shorts UI overlay at the bottom of the screen.
        (f'Style: Viral,{font},{preset.font_size},{preset.primary},'
         f'{preset.primary},{preset.outline_colour},&H64000000,-1,0,0,0,'
         f'100,100,{preset.spacing},0,1,{preset.outline},{preset.shadow},'
         f'2,80,80,{preset.margin_v},1'),
        '',
        '[Events]',
        ('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, '
         'Effect, Text'),
    ]

    lines: List[str] = []
    total = len(groups)
    for index, group in enumerate(groups):
        if not group:
            continue
        start = group[0].start
        end = group[-1].end

        # Hold the group a beat past the last word so it does not vanish the
        # instant the speaker stops -- but never into the next group.
        end += 0.12
        if index + 1 < total and groups[index + 1]:
            end = min(end, groups[index + 1][0].start)
        if clip_duration is not None:
            end = min(end, float(clip_duration))
        if end <= start:
            end = start + 0.08

        body = _render_group(group, preset, start, end)
        if not body:
            continue
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Viral,,0,0,0,,{body}"
        )

    return '\n'.join(head + lines) + '\n'


def _colour_for(word: CaptionWord, preset: CaptionPreset) -> Optional[str]:
    if word.emphasis == 'punch':
        return preset.punch
    if word.emphasis == 'emphasis':
        return preset.emphasis
    return None


def _render_group(group: Sequence[CaptionWord], preset: CaptionPreset,
                  start: float, end: float) -> str:
    """Build the ASS text for one group, including per-word reveal and colour.

    Word-by-word reveal is done with ``\\alpha`` transforms on a single
    dialogue line rather than one line per word. That matters: a separate line
    per word means N overlapping events, and libass stacks overlapping events
    vertically, which is exactly the jitter this is meant to avoid.
    """
    tokens = [_escape_text(w.text) for w in group]
    if not any(tokens):
        return ''
    if preset.uppercase:
        tokens = [t.upper() for t in tokens]

    duration_ms = max(1, int(round((end - start) * 1000)))
    parts: List[str] = []

    # Group-level entrance: a quick scale pop. 'single'/'viral' feel snappy at
    # ~140ms; longer reads as sluggish on a 30fps timeline (~4 frames).
    prefix = ''
    if preset.pop:
        prefix = (r'{\fscx78\fscy78'
                  r'\t(0,140,\fscx100\fscy100)}')

    line_breaks = _line_break_positions(tokens, preset)

    for i, (word, token) in enumerate(zip(group, tokens)):
        if not token:
            continue
        # Reveal: the word is invisible until it is spoken, then fades in over
        # ~90ms. Offsets are relative to the line's own start time.
        reveal_ms = int(round(max(0.0, word.start - start) * 1000))
        overrides: List[str] = []

        colour = _colour_for(word, preset)
        if colour:
            overrides.append(rf'\c{colour}')

        if reveal_ms > 20:
            # Start fully transparent, then snap to opaque at the word's onset.
            overrides.append(r'\alpha&HFF&')
            overrides.append(
                rf'\t({reveal_ms},{min(reveal_ms + 90, duration_ms)},\alpha&H00&)'
            )

        # Emphasised words get an extra bounce as they land, which is the
        # 'reacting to the speaker' beat.
        if preset.bounce and word.emphasis in ('emphasis', 'punch'):
            b0 = reveal_ms
            b1 = min(reveal_ms + 90, duration_ms)
            b2 = min(reveal_ms + 190, duration_ms)
            overrides.append(rf'\t({b0},{b1},\fscx116\fscy116)')
            overrides.append(rf'\t({b1},{b2},\fscx100\fscy100)')

        chunk = ('{' + ''.join(overrides) + '}' + token) if overrides else token
        parts.append(chunk)

        if i in line_breaks:
            parts.append(r'\N')
        elif i < len(tokens) - 1:
            parts.append(' ')

    return prefix + ''.join(parts)


def _line_break_positions(tokens: Sequence[str], preset: CaptionPreset) -> set:
    """Indices after which to insert a hard break.

    Balanced two-line blocks read better than one long ribbon at 100px+, and
    an over-wide line gets clipped by the frame edge. Only groups that are
    actually too wide are split, so most 2-3 word groups stay on one line.
    """
    if len(tokens) <= 1:
        return set()
    widths = [len(t) for t in tokens]
    total = sum(widths) + len(tokens) - 1
    # ~14 chars fits 1080px at ~104px in a condensed bold face.
    if total <= 14 or len(tokens) < 3:
        return set()
    # Choose the split that most evenly balances the two lines.
    best_index, best_delta = None, None
    running = 0
    for i in range(len(tokens) - 1):
        running += widths[i] + 1
        delta = abs((total - running) - running)
        if best_delta is None or delta < best_delta:
            best_index, best_delta = i, delta
    return {best_index} if best_index is not None else set()


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------
def build_viral_ass(segments: Sequence[Dict], preset_name: str = DEFAULT_PRESET,
                    time_offset: float = 0.0, clip_duration: Optional[float] = None,
                    keywords: Sequence[str] = (), font_size: Optional[int] = None,
                    max_words: Optional[int] = None,
                    available_fonts: Optional[Sequence[str]] = None,
                    play_res: Tuple[int, int] = (1080, 1920),
                    punch_ratio: float = 0.22) -> Optional[str]:
    """Turn transcript segments into a viral-style ASS document.

    Returns None when there is nothing to caption, so the caller can skip the
    subtitles filter entirely rather than burning an empty file.
    """
    preset = PRESETS.get((preset_name or '').lower()) or PRESETS[DEFAULT_PRESET]
    if font_size or max_words:
        # Copy so a per-call override never mutates the shared preset.
        preset = CaptionPreset(
            preset.name, preset.font, int(font_size or preset.font_size),
            preset.primary, preset.emphasis, preset.punch, preset.outline,
            preset.shadow, preset.margin_v, int(max_words or preset.max_words),
            preset.uppercase, preset.pop, preset.bounce, preset.spacing,
            preset.outline_colour,
        )

    words = extract_words(segments, time_offset=time_offset,
                          clip_duration=clip_duration)
    if not words:
        # No word-level timings: degrade to estimated word timing rather than
        # dropping back to a static paragraph.
        words = words_from_segment_text(segments, time_offset=time_offset,
                                        clip_duration=clip_duration)
        if words:
            logger.info(
                "No word-level timestamps in transcript; estimating word timing "
                "from segment text (enable word timestamps for exact sync)"
            )
    if not words:
        return None

    groups = group_words(words, max_words=preset.max_words)
    if not groups:
        return None

    assign_emphasis(groups, keywords=keywords, punch_ratio=punch_ratio)
    font = resolve_font(preset.font, available_fonts)
    if available_fonts is not None and font != preset.font:
        logger.info("Caption font '%s' unavailable; using '%s'", preset.font, font)

    logger.info(
        "Captions: %d words -> %d groups (preset '%s', font '%s', %d emphasised)",
        len(words), len(groups), preset.name, font,
        sum(1 for g in groups for w in g if w.emphasis != 'none'),
    )
    return build_ass(groups, preset, play_res=play_res, font_name=font,
                     clip_duration=clip_duration)
