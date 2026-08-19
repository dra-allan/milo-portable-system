"""Writing the operator's own text and the platform caption.

The division of labour here is the whole point:

* **the footage writes the hook** when it can - a line the viewer is about to
  hear;
* **the model writes the hook** when the footage cannot;
* **the code enforces the requirements** - the part that has to be exact.

A model asked to "write a caption including #roobet" will drop the hashtag some
percentage of the time. That produces a submission which fails for a reason no
retry fixes, on a campaign that may only let you post a handful of times a day.
So required keywords, mentions and brand phrases are appended and verified
*after* generation, and the model's output is treated as a suggestion for the
creative half only.

WHY A LIFTED HOOK BEATS A GENERATED ONE
---------------------------------------
The overlay text used to come from a six-entry fallback pool -- "WAIT FOR IT",
"THIS IS INSANE" -- which says nothing about the clip and is interchangeable
across every campaign. When :mod:`story_edit` has restructured the clip to open
on a question, that question is already the best possible on-screen hook: it is
specific, it creates a real curiosity gap, and the audio confirms it two seconds
later instead of contradicting it. So a lifted hook wins, and the templates stay
as the fallback they were always meant to be.

Phrases a campaign requires to appear *in the video* (not the caption) are still
forced into the burned-in text afterwards, whichever source the hook came from.
"""

import random
import re
from typing import Dict, List, Optional, Tuple

from .config import config
from .spec import CampaignSpec
from .utils import setup_logger

logger = setup_logger(__name__)

_FALLBACK_HOOKS = [
    'WAIT FOR IT',
    'THIS IS INSANE',
    'HE ACTUALLY DID THAT',
    'NO WAY THIS HAPPENED',
    'WATCH TILL THE END',
    'THIS CHANGED EVERYTHING',
]

_PROMPT = """Write short-form video copy for a clip.

Campaign: {name}
Clip source: {source}
Required phrases that MUST appear in the on-screen text: {in_video}
Required caption tokens (hashtags/mentions/keywords): {tokens}
Forbidden topics: {banned}
Language: {language}

Return strict JSON:
{{"overlay_text": "<max 8 words, uppercase, hook style>",
  "caption": "<1-2 short sentences, native to the platform, not an ad>"}}

Rules: no emoji in overlay_text. Do not mention being an advertisement.
JSON only, no prose, no code fence."""


def _lifted_hook(plan: Dict) -> Tuple[str, str]:
    """``(hook, source)`` from the clip's edit plan, or ``('', 'none')``.

    Imported lazily so this module stays usable in contexts where the edit
    engine is not needed (the copy path is exercised by tests that never build a
    plan).
    """
    if not config.hook_prefer_transcript:
        return '', 'none'
    try:
        from . import story_edit
        edit = story_edit.plan_from(plan)
    except Exception as exc:  # pragma: no cover - never block copy on this
        logger.debug('could not read the edit plan for a hook: %s', exc)
        return '', 'none'
    hook = (edit.title_hook or '').strip()
    return (hook, edit.hook_source) if hook else ('', 'none')


def _model_copy(spec: CampaignSpec, plan: Dict) -> Optional[Dict]:
    if not config.script_api_key:
        return None
    prompt = (_PROMPT
              .replace('{name}', spec.name)
              .replace('{source}', str(plan.get('source_name') or 'clip'))
              .replace('{in_video}',
                       ', '.join(spec.render.must_appear_in_video) or 'none')
              .replace('{tokens}',
                       ', '.join(spec.caption.all_required()) or 'none')
              .replace('{banned}',
                       ', '.join(spec.policy.banned_topics) or 'none')
              .replace('{language}', spec.render.language))
    import json
    for model in [config.script_model] + config.script_model_fallbacks:
        try:
            import google.generativeai as genai
            genai.configure(api_key=config.script_api_key)
            response = genai.GenerativeModel(model).generate_content(prompt)
            body = re.sub(r'^```(?:json)?|```$', '',
                          (response.text or '').strip(),
                          flags=re.MULTILINE).strip()
            parsed = json.loads(body)
            logger.info('COPY_MODEL_OK model=%s', model)
            return parsed
        except Exception as exc:
            logger.warning('COPY_MODEL_FAILED model=%s error=%s', model,
                           str(exc)[:140])
    return None


def _template_copy(spec: CampaignSpec, plan: Dict) -> Dict:
    """Deterministic copy for when there is no model or the model failed.

    Seeded by the source name and window so the same clip always gets the same
    copy. A random hook would make a re-render of a rejected clip produce
    different text, which makes debugging a rejection impossible.
    """
    pool = [str(h) for h in (config.hook_templates or [])] or _FALLBACK_HOOKS
    seed = f"{plan.get('source_name')}:{plan.get('start')}"
    hook = pool[random.Random(seed).randrange(len(pool))]
    return {'overlay_text': hook, 'caption': hook.capitalize()}


def _inject_phrases(text: str, phrases: List[str]) -> str:
    """Ensure every required phrase is present in the on-screen text.

    Case-insensitive containment check, appended in the campaign's own wording
    when absent. Matching loosely and appending exactly is deliberate: the
    reviewer reads the frame, so the phrase has to be there verbatim, but a hook
    that already says it in different case should not get it twice.
    """
    out = text.strip()
    low = out.lower()
    for phrase in phrases:
        clean = phrase.strip()
        if clean and clean.lower() not in low:
            out = f'{out} {clean}'.strip() if out else clean
            low = out.lower()
    return out


def enforce_caption(caption: str, spec: CampaignSpec) -> Tuple[str, List[str]]:
    """Append every missing required token; report what had to be added.

    Returns ``(caption, added)``. The added list is not cosmetic: a campaign
    whose tokens are always being appended is a campaign whose copy prompt is
    wrong, and that is worth seeing in the logs.
    """
    text = (caption or '').strip()
    low = text.lower()
    added: List[str] = []
    for token in spec.caption.all_required():
        if token.lower() not in low:
            text = f'{text} {token}'.strip()
            low = text.lower()
            added.append(token)
    if spec.caption.max_length and len(text) > spec.caption.max_length:
        # Trim from the front, never from the tail: the tail is where the
        # required tokens now live and dropping one fails the submission.
        keep = ' '.join(spec.caption.all_required())
        room = max(0, spec.caption.max_length - len(keep) - 1)
        text = (text[:room].rsplit(' ', 1)[0] + ' ' + keep).strip()
    return text, added


def banned_hits(text: str, spec: CampaignSpec) -> List[str]:
    """Forbidden topic words present in a piece of copy.

    Word-boundary matched. Substring matching produced nonsense here: 'religion'
    inside a longer word, or the classic 'politics' inside a hashtag, would
    block copy that is fine.
    """
    hits = []
    low = f' {(text or "").lower()} '
    pool = list(spec.policy.banned_topics) + list(config.banned_words)
    for word in pool:
        token = str(word).strip().lower()
        if token and re.search(r'\b' + re.escape(token) + r'\b', low):
            hits.append(token)
    return sorted(set(hits))


def build_copy(spec: CampaignSpec, plan: Dict,
               use_model: bool = True) -> Dict:
    """Produce ``{overlay_text, caption, caption_added, banned, hook_source}``.

    Hook precedence: the transcript-lifted hook, then the model, then the
    deterministic template pool. The caption body still comes from the model
    (or the template) regardless, because a spoken question makes a good
    on-screen hook and a poor platform caption.
    """
    copy = (_model_copy(spec, plan) if use_model else None) \
        or _template_copy(spec, plan)

    overlay = str(copy.get('overlay_text') or '').strip()
    caption = str(copy.get('caption') or '').strip()

    lifted, hook_source = _lifted_hook(plan)
    if lifted:
        overlay = lifted
        logger.info('HOOK_LIFTED campaign=%s source=%s text=%r', spec.id,
                    hook_source, lifted)

    if spec.render.own_text_required and not overlay:
        overlay = _template_copy(spec, plan)['overlay_text']
        hook_source = 'none'

    # Requirements that live in the video, not the caption.
    overlay = _inject_phrases(overlay, spec.render.must_appear_in_video)

    caption, added = enforce_caption(caption or overlay, spec)
    banned = sorted(set(banned_hits(overlay, spec)
                        + banned_hits(caption, spec)))
    if banned:
        logger.warning('COPY_BANNED_TERMS campaign=%s terms=%s', spec.id,
                       ','.join(banned))
    if added:
        logger.info('CAPTION_ENFORCED campaign=%s added=%s', spec.id,
                    ','.join(added))

    highlight = (spec.render.must_appear_in_video[0]
                 if spec.render.must_appear_in_video else '')
    return {'overlay_text': overlay, 'caption': caption,
            'caption_added': added, 'banned': banned,
            'hook_source': hook_source,
            'highlight': highlight}


def build_title(spec: CampaignSpec, copy: Dict, clip_id: int = 0) -> str:
    """YouTube title. Shorts titles are short; the caption carries the tokens.

    The raw overlay hook is passed through the Shorts lane's rule-based title
    optimizer (vendored as :mod:`title_optimizer`), which strips filler, rejects
    non-English/hallucinated hooks, and shapes curiosity-gap frames keyed on the
    campaign's niche. The result is deterministic per (hook, niche, clip).
    """
    hook = (copy.get('overlay_text') or spec.name).strip()
    hook = re.sub(r'\s+', ' ', hook)[:80]
    try:
        from .title_optimizer import optimize_title
        base = optimize_title(hook, niche=spec.niche,
                              keywords=spec.caption.all_required(),
                              clip_index=clip_id, max_len=72)
    except Exception as exc:
        logger.warning('TITLE_OPTIMIZER_FAILED campaign=%s error=%s',
                       spec.id, str(exc)[:120])
        base = hook
    base = (base or hook).strip()
    return f'{base} #shorts' if '#shorts' not in base.lower() else base
