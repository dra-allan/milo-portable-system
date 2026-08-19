"""Hook -> story -> payoff: turning a chosen window into an actual EDIT.

WHY THIS EXISTS
---------------
Until now a "clip" was one continuous cut: pick 22 good seconds, crop to 9:16,
burn captions, done. That is a *trim*, not an edit, and it is why the output
looks like a slice of a podcast rather than something built to be watched.

The structure that actually retains a viewer is well known and mechanical:

    HOOK    open with something that creates a curiosity gap
    STORY   keep forward motion so leaving costs the viewer the answer
    PAYOFF  close the loop the hook opened

The most reliable way to manufacture it from found footage is also the simplest:
**find a question in the middle of the clip and move it to the front.** The rest
of the footage then plays as the story, and the moment it was building toward
lands at the end as the payoff. Nothing is fabricated -- the words and the
pictures are the source's own, only their order changes.

WHAT THIS MODULE PRODUCES
-------------------------
An :class:`EditPlan`: an ordered list of source-timeline :class:`Span` s plus the
on-screen title hook. One span means "a continuous cut" and the renderer takes
its original fast path. Two or more means trim+concat, and the captions are
remapped through the same piecewise timeline so audio, video and text move
together.

THE SYNC PROPERTY (the thing worth being careful about)
-------------------------------------------------------
Reordering video while captioning from a source-timeline transcript would desync
every word, silently, by however far each span moved. So there is exactly one
function that knows the mapping -- :func:`remap_segments` -- and both the
filtergraph and the captions are derived from the same span list and the same
offsets. Sync is therefore structural: if a span moves, both move with it. This
is the same discipline the shorts lane uses for keyframe drift, for the same
reason.

STYLES
------
``question_first``  lift the strongest question to the front (default when one
                    exists). This is the playbook cut.
``cold_open``       a short payoff teaser up front, then the clip in full. Used
                    when there is no question but there is a clear reaction beat.
``straight``        one continuous cut. Previous behaviour, still available and
                    still the fallback.
``auto``            question_first -> cold_open -> straight, first that fits.

All of it is deterministic: the same window always yields the same edit, so a
re-render of a rejected clip is reproducible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__)

STYLE_STRAIGHT = 'straight'
STYLE_QUESTION_FIRST = 'question_first'
STYLE_COLD_OPEN = 'cold_open'
STYLE_AUTO = 'auto'
STYLES = (STYLE_AUTO, STYLE_QUESTION_FIRST, STYLE_COLD_OPEN, STYLE_STRAIGHT)

ROLE_HOOK = 'hook'
ROLE_STORY = 'story'
ROLE_PAYOFF = 'payoff'

# A question worth opening on. Anchored on the question mark OR on an
# interrogative opener, because Whisper drops terminal punctuation often enough
# that requiring '?' alone would miss most of them.
_QUESTION_MARK = re.compile(r'\?')
_INTERROGATIVE = re.compile(
    r'^\s*(?:so\s+|but\s+|and\s+|okay\s+|ok\s+|now\s+)?'
    r'(?:are|is|was|were|do|does|did|can|could|will|would|should|have|has|had|'
    r'am|why|what|when|where|who|whose|which|how|any|anyone|anybody)\b',
    re.IGNORECASE)

# Reaction/payoff language -- the beat a cold open teases.
_PAYOFF_CUE = re.compile(
    r'\b(wait|watch|look|no way|oh my|finally|actually|literally|unbelievable|'
    r'insane|crazy|wow|there it is|here we go|got him|plot twist|holy|'
    r'i can.t believe|that was)\b', re.IGNORECASE)

# Filler openers stripped from a lifted hook line. "So are cows allowed on the
# plane?" is a worse title than "Are cows allowed on the plane?".
_LEADING_FILLER = re.compile(
    r'^(?:so|and|but|like|okay|ok|well|now|then|um|uh|erm|you know|i mean)\b[,\s]*',
    re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Span:
    """A range of the SOURCE timeline, and what job it does in the output."""
    start: float
    end: float
    role: str = ROLE_STORY

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    def to_dict(self) -> Dict:
        return {'start': round(float(self.start), 3),
                'end': round(float(self.end), 3), 'role': self.role}

    @classmethod
    def from_dict(cls, raw: Dict) -> 'Span':
        return cls(float(raw['start']), float(raw['end']),
                   str(raw.get('role') or ROLE_STORY))


@dataclass
class EditPlan:
    """An ordered edit over one source file, plus its on-screen hook.

    Deliberately JSON-serialisable in both directions: the plan is stored on the
    clip row so a rejected clip can be re-rendered identically, and the clipper
    database serialises plan rows with ``json.dumps``.
    """
    style: str = STYLE_STRAIGHT
    spans: List[Span] = field(default_factory=list)
    title_hook: str = ''
    hook_source: str = 'none'   # 'question' | 'payoff' | 'none'
    notes: List[str] = field(default_factory=list)

    # -- geometry ----------------------------------------------------
    @property
    def duration(self) -> float:
        """Length of the rendered output."""
        return round(sum(span.duration for span in self.spans), 3)

    @property
    def is_reordered(self) -> bool:
        """True when the renderer must build a trim+concat graph.

        A single span that happens to equal the window is not a reorder, and
        taking the old single-cut path for it keeps every currently-passing clip
        byte-identical.
        """
        return len(self.spans) > 1

    @property
    def coverage_start(self) -> float:
        return min((span.start for span in self.spans), default=0.0)

    @property
    def coverage_end(self) -> float:
        return max((span.end for span in self.spans), default=0.0)

    def output_offsets(self) -> List[float]:
        """Where each span begins in the OUTPUT timeline."""
        offsets, cursor = [], 0.0
        for span in self.spans:
            offsets.append(cursor)
            cursor += span.duration
        return offsets

    # -- serialisation ------------------------------------------------
    def to_dict(self) -> Dict:
        return {'style': self.style,
                'spans': [span.to_dict() for span in self.spans],
                'title_hook': self.title_hook,
                'hook_source': self.hook_source,
                'notes': list(self.notes),
                'duration': self.duration}

    @classmethod
    def from_dict(cls, raw: Optional[Dict]) -> Optional['EditPlan']:
        if not raw or not raw.get('spans'):
            return None
        return cls(style=str(raw.get('style') or STYLE_STRAIGHT),
                   spans=[Span.from_dict(s) for s in raw['spans']],
                   title_hook=str(raw.get('title_hook') or ''),
                   hook_source=str(raw.get('hook_source') or 'none'),
                   notes=list(raw.get('notes') or []))

    def describe(self) -> str:
        parts = ' + '.join(f'{s.role}[{s.start:.1f}-{s.end:.1f}]'
                           for s in self.spans)
        return f'{self.style}: {parts} = {self.duration:.1f}s'


# ---------------------------------------------------------------------------
# Finding the hook
# ---------------------------------------------------------------------------
def _clean_line(text: str) -> str:
    line = ' '.join(str(text or '').split())
    line = _LEADING_FILLER.sub('', line)
    return line.strip(' -,;:')


def is_question(text: str) -> bool:
    """Does this line read as a question?

    Both tests are needed. Whisper frequently omits the '?', so an
    interrogative opener has to count; and a line can end in '?' without
    starting like a question ("...on the plane, right?").
    """
    line = _clean_line(text)
    if not line:
        return False
    if _QUESTION_MARK.search(line):
        return True
    return bool(_INTERROGATIVE.match(line))


def _segments_within(segments: Sequence[Dict], start: float,
                     end: float) -> List[Dict]:
    return [s for s in segments or []
            if float(s.get('end', 0)) > start and float(s.get('start', 0)) < end]


def find_question(segments: Sequence[Dict], start: float, end: float,
                  min_lead_in: float = 1.0) -> Optional[Dict]:
    """The best question to open the clip with, or None.

    ``min_lead_in`` exists because a question already sitting at the very start
    of the window needs no surgery -- moving it would produce an identical cut
    with an extra concat seam for nothing. A question in the *last* fifth is
    also skipped: lifting it to the front leaves the story with no build.
    """
    window = float(end) - float(start)
    if window <= 0:
        return None
    latest = float(start) + window * 0.8
    best, best_score = None, 0.0
    for segment in _segments_within(segments, start, end):
        seg_start = float(segment['start'])
        if seg_start < float(start) + min_lead_in or seg_start > latest:
            continue
        text = str(segment.get('text') or '')
        if not is_question(text):
            continue
        # Prefer a short, self-contained question: it reads as a title, and a
        # 14-second rambling question makes a terrible cold open.
        length = float(segment['end']) - seg_start
        score = 1.0
        if _QUESTION_MARK.search(text):
            score += 0.6          # explicit punctuation is stronger evidence
        words = len(text.split())
        if 3 <= words <= 14:
            score += 0.5
        if 0.6 <= length <= 5.0:
            score += 0.4
        # Earlier questions leave more room for the story behind them.
        score += max(0.0, 1.0 - (seg_start - float(start)) / max(1.0, window)) * 0.3
        if score > best_score:
            best, best_score = segment, score
    return best


def find_payoff(segments: Sequence[Dict], start: float,
                end: float) -> Optional[Dict]:
    """The strongest reaction beat in the back half of the window."""
    window = float(end) - float(start)
    if window <= 0:
        return None
    from_time = float(start) + window * 0.5
    best, best_hits = None, 0
    for segment in _segments_within(segments, from_time, end):
        hits = len(_PAYOFF_CUE.findall(str(segment.get('text') or '')))
        if hits > best_hits:
            best, best_hits = segment, hits
    return best


def title_hook_from(text: str, uppercase: bool = True,
                    max_words: int = 9) -> str:
    """Turn a spoken line into an on-screen title hook.

    This is deliberately a *lift*, not a rewrite. The playbook's own advice when
    the footage already contains a good hook is "copy the same hook" -- and a
    line the viewer is about to hear matches what they see, which a generated
    hook often does not.
    """
    line = _clean_line(text)
    if not line:
        return ''
    words = line.split()
    trimmed = ' '.join(words[:max(3, int(max_words))])
    if len(words) > max_words:
        trimmed = trimmed.rstrip(',;:. ') + '?'
    elif _QUESTION_MARK.search(line) and not trimmed.endswith('?'):
        trimmed = trimmed.rstrip('.,;: ') + '?'
    return trimmed.upper() if uppercase else trimmed


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------
def _normalise(spans: List[Span], min_span: float, min_total: float,
               max_total: float, notes: List[str]) -> List[Span]:
    """Drop slivers, merge seams that are not really cuts, respect the band.

    Two things matter here:

    * A span below ``min_span`` is a visual glitch, not a shot. Dropped.
    * Two spans that are contiguous in the source AND adjacent in the output are
      the same shot with a pointless concat seam in the middle. Merged, because
      each seam is a re-encode boundary and a chance for an audio click.
    """
    kept = [s for s in spans if s.duration >= min_span]
    if not kept:
        return []

    merged: List[Span] = [kept[0]]
    for span in kept[1:]:
        previous = merged[-1]
        if abs(span.start - previous.end) < 0.05:
            previous.end = span.end
            if previous.role == ROLE_STORY:
                previous.role = span.role
        else:
            merged.append(span)

    total = sum(s.duration for s in merged)
    if max_total and total > max_total:
        # Trim from the TAIL. Never from the head: the head is the hook, and a
        # clipped hook is a clip with no reason to be watched.
        excess = total - max_total
        last = merged[-1]
        if last.duration - excess >= min_span:
            last.end -= excess
            notes.append(f'trimmed {excess:.1f}s off the tail to fit '
                         f'{max_total:.0f}s')
        else:
            merged.pop()
            notes.append('dropped the trailing span to fit the duration cap')

    total = sum(s.duration for s in merged)
    if min_total and total < min_total and merged:
        notes.append(f'{total:.1f}s is under the {min_total:.0f}s minimum; the '
                     'caller should widen the window')
    return merged


def _question_first(window_start: float, window_end: float, question: Dict,
                    hook_tail: float, notes: List[str]) -> List[Span]:
    """Lift the question to the front; the rest plays in order behind it.

    The hook keeps a little of what follows the question (``hook_tail``): the
    answering beat -- "They are? Okay, perfect." -- is what makes the cold open
    land as an exchange rather than a dangling sentence.
    """
    q_start = max(window_start, float(question['start']))
    q_end = min(window_end, float(question['end']) + hook_tail)
    spans = [Span(q_start, q_end, ROLE_HOOK)]
    if q_start - window_start > 0.05:
        spans.append(Span(window_start, q_start, ROLE_STORY))
    if window_end - q_end > 0.05:
        spans.append(Span(q_end, window_end, ROLE_PAYOFF))
    notes.append(f'opened on a question at {q_start:.1f}s')
    return spans


def _cold_open(window_start: float, window_end: float, payoff: Dict,
               teaser: float, notes: List[str]) -> List[Span]:
    """A short teaser of the payoff, then the clip in full.

    The teaser is intentionally tiny. Long enough to raise the question "what am
    I looking at", short enough that it does not spend the payoff it is selling.
    """
    p_start = max(window_start, float(payoff['start']))
    p_end = min(window_end, p_start + teaser)
    spans = [Span(p_start, p_end, ROLE_HOOK),
             Span(window_start, window_end, ROLE_STORY)]
    notes.append(f'cold open teasing the beat at {p_start:.1f}s')
    return spans


def build_plan(window: Dict, segments: Sequence[Dict], min_duration: float,
               max_duration: float, style: Optional[str] = None) -> EditPlan:
    """Decide how to cut one chosen window.

    Args:
        window: needs ``start`` and ``end`` (or ``duration``) in source time.
        segments: transcript segments for the whole source, ideally with
            word-level timings.
        min_duration / max_duration: the campaign's legal duration band. The
            reorder must never push a clip outside it -- a clip one second under
            a campaign's minimum is a wasted daily submission slot.
        style: one of :data:`STYLES`; defaults to ``config.edit_style``.

    Returns an EditPlan that always has at least one span, so callers never have
    to handle "no plan".
    """
    start = float(window.get('start', 0.0))
    end = float(window.get('end', start + float(window.get('duration', 0.0))))
    requested = (style or config.edit_style or STYLE_AUTO).lower()
    if requested not in STYLES:
        requested = STYLE_AUTO

    notes: List[str] = []
    min_span = float(config.edit_min_span)
    hook_tail = float(config.hook_tail_seconds)
    teaser = float(config.cold_open_seconds)

    question = None
    payoff = None
    if requested in (STYLE_AUTO, STYLE_QUESTION_FIRST):
        question = find_question(segments, start, end,
                                min_lead_in=max(min_span, 1.0))
    if requested in (STYLE_AUTO, STYLE_COLD_OPEN) and question is None:
        payoff = find_payoff(segments, start, end)

    if question is not None and requested in (STYLE_AUTO, STYLE_QUESTION_FIRST):
        spans = _question_first(start, end, question, hook_tail, notes)
        chosen, hook_text, hook_source = (STYLE_QUESTION_FIRST,
                                          str(question.get('text') or ''),
                                          'question')
    elif payoff is not None and requested in (STYLE_AUTO, STYLE_COLD_OPEN):
        spans = _cold_open(start, end, payoff, teaser, notes)
        chosen, hook_text, hook_source = (STYLE_COLD_OPEN,
                                          str(payoff.get('text') or ''),
                                          'payoff')
    else:
        spans = [Span(start, end, ROLE_STORY)]
        chosen, hook_text, hook_source = STYLE_STRAIGHT, '', 'none'
        if requested != STYLE_STRAIGHT:
            notes.append('no question or reaction beat found; straight cut')

    spans = _normalise(spans, min_span, min_duration, max_duration, notes)
    if not spans:
        # Every candidate span was a sliver. Fall back to the plain window
        # rather than returning something the renderer cannot build.
        spans = [Span(start, end, ROLE_STORY)]
        chosen, hook_text, hook_source = STYLE_STRAIGHT, '', 'none'
        notes.append('spans collapsed; fell back to a straight cut')

    title_hook = ''
    if config.hook_text_enabled and hook_text:
        title_hook = title_hook_from(hook_text,
                                     uppercase=config.hook_uppercase,
                                     max_words=config.hook_max_words)

    plan = EditPlan(style=chosen, spans=spans, title_hook=title_hook,
                    hook_source=hook_source, notes=notes)
    logger.info('EDIT_PLAN %s hook=%r', plan.describe(), plan.title_hook)
    return plan


def straight_plan(start: float, end: float) -> EditPlan:
    """The trivial one-cut plan, for callers with no transcript."""
    return EditPlan(style=STYLE_STRAIGHT,
                    spans=[Span(float(start), float(end), ROLE_STORY)])


def plan_from(clip_plan: Dict) -> EditPlan:
    """Recover the EditPlan stored on a clip row, or synthesise a straight one.

    Every consumer goes through this, so an old clip row written before edit
    plans existed renders exactly as it used to instead of raising.
    """
    recovered = EditPlan.from_dict(clip_plan.get('edit'))
    if recovered:
        return recovered
    start = float(clip_plan.get('start', 0.0))
    end = float(clip_plan.get('end', start + float(clip_plan.get('duration', 0.0))))
    return straight_plan(start, end)


# ---------------------------------------------------------------------------
# Caption remapping -- the one place the timeline mapping lives
# ---------------------------------------------------------------------------
def remap_segments(segments: Sequence[Dict], plan: EditPlan) -> List[Dict]:
    """Rewrite transcript segments from source time into OUTPUT time.

    Speech that falls outside every span is dropped; speech that straddles a
    span boundary is clipped to it. A segment appearing in two spans (the clip
    shows that moment twice, which a cold open does by design) is emitted twice,
    which is correct: the words are spoken twice in the output.

    The caption builder is then called with ``time_offset=0`` because these
    timestamps are already output-relative. That is the whole reason reordering
    does not desync captions.
    """
    out: List[Dict] = []
    for span, offset in zip(plan.spans, plan.output_offsets()):
        span_start, span_end = float(span.start), float(span.end)
        for segment in segments or []:
            seg_start = float(segment.get('start', 0.0))
            seg_end = float(segment.get('end', 0.0))
            if seg_end <= span_start or seg_start >= span_end:
                continue

            words = []
            for word in segment.get('words') or []:
                try:
                    w_start = float(word['start'])
                    w_end = float(word['end'])
                except (KeyError, TypeError, ValueError):
                    continue
                if w_end <= span_start or w_start >= span_end:
                    continue
                words.append({
                    'word': word.get('word', ''),
                    'start': offset + max(0.0, w_start - span_start),
                    'end': offset + min(span.duration, w_end - span_start),
                })

            new_start = offset + max(0.0, seg_start - span_start)
            new_end = offset + min(span.duration, seg_end - span_start)
            if new_end <= new_start:
                continue
            text = ' '.join(str(w['word']).strip() for w in words).strip()
            out.append({'text': text or str(segment.get('text') or ''),
                        'start': new_start, 'end': new_end, 'words': words})

    out.sort(key=lambda item: item['start'])
    return out


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------
def read_window(plan: EditPlan, pad: float = 0.5) -> Tuple[float, float]:
    """``(seek, read_span)`` for the input.

    A trim filter works on the DECODED timeline, so with ``-ss`` before ``-i``
    every trim time has to be rebased by the seek. Seeking is what keeps a
    reorder cheap on a 26-minute source: without it ffmpeg decodes from zero to
    reach a span at minute 20.
    """
    seek = max(0.0, plan.coverage_start)
    span = max(0.1, plan.coverage_end - seek + pad)
    return seek, span


def build_filtergraph(plan: EditPlan, has_audio: bool,
                      seek: float) -> Tuple[List[str], str, Optional[str]]:
    """trim/concat chains for a reordered edit.

    Returns ``(chains, video_label, audio_label)``. ``audio_label`` is None for a
    silent source, in which case the caller supplies its own silent input --
    concatenating audio that does not exist produces a graph ffmpeg rejects.
    """
    chains: List[str] = []
    video_labels: List[str] = []
    audio_labels: List[str] = []

    for index, span in enumerate(plan.spans):
        rel_start = max(0.0, span.start - seek)
        rel_end = max(rel_start + 0.05, span.end - seek)
        # setpts/asetpts rebase each piece to zero, which is what makes concat
        # produce a continuous timeline instead of overlapping timestamps.
        chains.append(
            f'[0:v]trim=start={rel_start:.3f}:end={rel_end:.3f},'
            f'setpts=PTS-STARTPTS[ev{index}]')
        video_labels.append(f'[ev{index}]')
        if has_audio:
            chains.append(
                f'[0:a:0]atrim=start={rel_start:.3f}:end={rel_end:.3f},'
                f'asetpts=PTS-STARTPTS[ea{index}]')
            audio_labels.append(f'[ea{index}]')

    count = len(plan.spans)
    if has_audio:
        interleaved = ''.join(v + a for v, a in zip(video_labels, audio_labels))
        chains.append(f'{interleaved}concat=n={count}:v=1:a=1[evcat][eacat]')
        return chains, 'evcat', 'eacat'
    chains.append(f'{"".join(video_labels)}concat=n={count}:v=1:a=0[evcat]')
    return chains, 'evcat', None
