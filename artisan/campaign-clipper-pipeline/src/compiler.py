"""Compile a campaign's free-prose requirements into a :class:`CampaignSpec`.

Order of operations: **rules first, model second.**

The requirement blocks are heavily templated (the same dozen phrasings recur
across campaigns), so a deterministic pass gets most of it and, crucially,
cannot invent a requirement that was never written. For this lane that is the
expensive failure mode: a hallucinated rule wastes a render, but a *missed*
rule loses a submission and possibly the account. The model is only shown the
lines the rules could not place, and its output is merged additively.

Parser notes, every one of them from real campaign text
-------------------------------------------------------
* ``MINIMUN LENGTH`` is misspelled on live campaigns, so the duration matcher
  accepts the typo, and duration appears in both orders (``10s MINIMUM
  LENGTH`` in the body, ``Min. Duration 8 secs`` in the card header).
* ``0,4% ENGAGEMENT MINIMUM`` uses a comma decimal separator. Parsed naively
  that becomes 4%, a tenfold error in the strict direction, which would park
  every clip forever.
* ``3,000 VIEWS FOR EARNINGS`` uses a comma thousands separator in the same
  document. So separators are disambiguated by digit grouping, not globally.
* Prohibitions are detected by *content*, never by position. Clipster draws a
  red cross beside them; a copy-pasted block has lost the cross, and
  ``POST SPAM/LOW QUALITY`` then reads as an instruction to post spam.
* Any line that matches nothing lands in ``spec.unparsed``. Silence there would
  make the operator review step pointless.
"""

import json
import re
from typing import Dict, List, Optional, Tuple

from .spec import (CampaignSpec, MUSIC_NATIVE, UGC, CLIPPING,
                   parse_audience_line)
from .utils import safe_slug, setup_logger

logger = setup_logger(__name__)

_MD_LINK = re.compile(r'\[([^\]]*)\]\((https?://[^)\s]+)\)')
_BARE_URL = re.compile(r'(https?://[^\s)\]]+)')

# Duration, both orders, typo tolerated.
_DUR_A = re.compile(r'(\d{1,3})\s*(?:s|sec|secs|second|seconds)\b'
                    r'[^\n]{0,24}?\bmin', re.IGNORECASE)
_DUR_B = re.compile(r'\bmin[a-z.]*\s*(?:duration|length)\b\D{0,12}'
                    r'(\d{1,3})', re.IGNORECASE)
_DUR_MAX = re.compile(r'\bmax[a-z.]*\s*(?:duration|length)\b\D{0,12}'
                      r'(\d{1,3})', re.IGNORECASE)

_ENGAGEMENT = re.compile(r'(\d{1,3}(?:[.,]\d{1,2})?)\s*%\s*engagement',
                         re.IGNORECASE)
_VIEWS = re.compile(r'([\d][\d.,]{2,})\s*views', re.IGNORECASE)
_DAYS = re.compile(r'at least\s*(\d{1,3})\s*days', re.IGNORECASE)
_LINKED = re.compile(r'(\d{1,2})\s*linked account', re.IGNORECASE)

_PROHIBITION_HINTS = ('spam', 'low quality', 'low-quality', 'trash',
                      'reupload', 'stolen', 'watermark')

_BANNED_LIST = re.compile(r'\bno\s+([a-z0-9 ,/&\'-]{6,200})', re.IGNORECASE)

_MUST_IN_VIDEO = (
    re.compile(r'mention the app name\s+([A-Za-z0-9 :\'-]{2,40}?)\s+'
               r'somewhere in the video', re.IGNORECASE),
    re.compile(r'say\s+"([^"]{2,60})"\s+in full', re.IGNORECASE),
    re.compile(r'say\s+\'([^\']{2,60})\'\s+in full', re.IGNORECASE),
)

_MUST_IN_CAPTION = re.compile(
    r'must mention\s+([A-Za-z0-9 #@_\'-]{2,40}?)\s+in (?:the )?caption',
    re.IGNORECASE)
_TAG_ACCOUNT = re.compile(
    r'tag the official\s+([A-Za-z0-9 _-]{2,40}?)\s+account', re.IGNORECASE)
_REQUIRED_KEYWORDS = re.compile(r'required keywords?\s*:\s*(.+)',
                                re.IGNORECASE)

_PLATFORM_WORDS = {
    'youtube': ('youtube', 'yt shorts', 'yt-shorts', 'shorts'),
    'tiktok': ('tiktok', 'tik tok'),
    'instagram': ('instagram', 'reels', 'ig '),
    'x': ('twitter', ' x ', '(x)'),
}

_LANGUAGES = {'english': 'en', 'spanish': 'es', 'portuguese': 'pt',
              'french': 'fr', 'german': 'de', 'hindi': 'hi',
              'luganda': 'lg', 'arabic': 'ar'}


def _decimal(raw: str) -> float:
    """Parse a number whose separator could be either convention.

    ``0,4`` is four tenths; ``3,000`` is three thousand. The discriminator is
    the digit grouping after the separator, not a global locale setting - both
    forms appear inside the *same* campaign block.
    """
    text = raw.strip()
    if ',' in text and '.' not in text:
        head, _, tail = text.partition(',')
        return float(f'{head}.{tail}') if len(tail) <= 2 and len(tail) != 3 \
            else float(head + tail)
    return float(text.replace(',', ''))


def _integer(raw: str) -> int:
    return int(round(_decimal(raw)))


def extract_links(text: str) -> List[Tuple[str, str]]:
    """[(label, url)] for markdown links, plus bare URLs with an empty label."""
    out = [(m.group(1).strip(), m.group(2).strip())
           for m in _MD_LINK.finditer(text or '')]
    known = {url for _, url in out}
    for m in _BARE_URL.finditer(text or ''):
        url = m.group(1).rstrip('.,);')
        if url not in known:
            out.append(('', url))
            known.add(url)
    return out


def _label_kind(label: str, line: str) -> str:
    """Classify a link by its label *and* its line.

    Label alone is not enough: ``CLIP THIS -> CONTENT TO CLIP`` and
    ``ONLY CLIPS FROM CONTENT FOLDER`` both mean "source pool" but share no
    common label token.
    """
    blob = f'{label} {line}'.lower()
    if 'logo' in blob:
        return 'logo'
    if 'brief' in blob or 'notion' in blob or 'specification' in blob:
        return 'brief'
    if 'discord' in blob:
        return 'discord'
    if any(k in blob for k in ('content', 'clip this', 'clips from',
                              'footage', 'assets')):
        return 'content'
    return 'unknown'


def compile_requirements(raw: str, campaign_id: str = '',
                         name: str = '', url: str = '',
                         card: Optional[Dict] = None,
                         use_model: bool = False) -> CampaignSpec:
    """Turn a requirements blob (plus optional card metadata) into a spec.

    ``card`` is the structured header Clipster already renders (rate, caps,
    platforms, min duration). When present it is merged with strictest-wins so
    a header/body disagreement is resolved deterministically and recorded.
    """
    text = (raw or '').replace('\r\n', '\n')
    card = card or {}
    cid = safe_slug(campaign_id or name or 'campaign')

    data: Dict = {
        'campaign': {'id': cid, 'name': name or cid, 'url': url,
                     'type': CLIPPING},
        'sources': {}, 'assets': {}, 'render': {}, 'caption': {},
        'account_gates': {}, 'policy': {},
        'raw_requirements': text,
    }
    render: Dict = data['render']
    caption: Dict = data['caption']
    gates: Dict = data['account_gates']
    policy: Dict = data['policy']
    sources: Dict = data['sources']
    assets: Dict = data['assets']

    content_folders: List[str] = []
    logo_folders: List[str] = []
    prohibitions: List[str] = []
    banned: List[str] = []
    must_video: List[str] = []
    must_caption: List[str] = []
    keywords: List[str] = []
    audience: List[Dict] = []
    unparsed: List[str] = []
    platforms: List[str] = []

    min_dur: Optional[float] = None
    max_dur: Optional[float] = None

    lines = [ln.strip() for ln in text.split('\n')]
    for line in lines:
        if not line or line.lower().startswith('###'):
            continue
        stripped = re.sub(r'^[\*\-\u2022\u2713\u2714\u2717\u2718\s]+', '',
                          line)
        if not stripped:
            continue
        low = stripped.lower()
        matched = False

        # -- links -------------------------------------------------------
        for label, link in extract_links(stripped):
            kind = _label_kind(label, stripped)
            if kind == 'logo':
                logo_folders.append(link)
                assets['logo_required'] = True
                assets['logo_mode'] = ('if-absent'
                                       if 'if not already' in low
                                       else 'always')
                matched = True
            elif kind == 'brief':
                sources['brief_url'] = link
                matched = True
            elif kind == 'discord':
                sources['discord_url'] = link
                matched = True
            elif kind == 'content':
                content_folders.append(link)
                matched = True

        # -- audience gates ----------------------------------------------
        gate = parse_audience_line(stripped)
        if gate:
            audience.append({'country': gate.country,
                             'operator': gate.operator,
                             'percent': gate.percent})
            continue

        # -- duration -----------------------------------------------------
        for pattern in (_DUR_A, _DUR_B):
            found = pattern.search(stripped)
            if found:
                value = float(found.group(1))
                min_dur = value if min_dur is None else max(min_dur, value)
                matched = True
                break
        found = _DUR_MAX.search(stripped)
        if found:
            value = float(found.group(1))
            max_dur = value if max_dur is None else min(max_dur, value)
            matched = True

        # -- engagement / views / retention -------------------------------
        found = _ENGAGEMENT.search(stripped)
        if found:
            gates['min_engagement_pct'] = _decimal(found.group(1))
            matched = True
        if 'views' in low and ('earning' in low or 'payout' in low):
            found = _VIEWS.search(stripped)
            if found:
                gates['min_views_for_earnings'] = _integer(found.group(1))
                matched = True
        found = _DAYS.search(stripped)
        if found and ('live' in low or 'keep' in low):
            policy['keep_live_days'] = int(found.group(1))
            matched = True
        found = _LINKED.search(stripped)
        if found:
            gates['max_linked_accounts'] = int(found.group(1))
            matched = True

        # -- platform / language ------------------------------------------
        for platform, words in _PLATFORM_WORDS.items():
            if any(word in low for word in words):
                if platform not in platforms:
                    platforms.append(platform)
                matched = True
        if 'shorts only' in low or 'shorts only!' in low:
            render['shorts_only'] = True
            matched = True
        for word, code in _LANGUAGES.items():
            if word in low:
                render['language'] = code
                matched = True

        # -- own text -----------------------------------------------------
        if ('own text' in low or 'own caption' in low
                or ('add' in low and 'text' in low and 'logo' not in low)):
            render['own_text_required'] = True
            matched = True
        if 'gameplay' in low:
            render['gameplay_visible'] = True
            matched = True
        if 'trending music' in low or 'trending audio' in low:
            # Only the platform's own composer can attach native audio, so
            # this is recorded as a manual step, never as something rendered.
            render['music'] = MUSIC_NATIVE
            matched = True
        if 'native to the platform' in low or 'feel native' in low:
            policy['native_feel'] = True
            matched = True
        if 'competitor' in low:
            policy['no_competitor_attacks'] = True
            matched = True
        if 'ugc' in low or 'concept' in low:
            data['campaign']['type'] = UGC
            matched = True

        # -- caption obligations ------------------------------------------
        found = _REQUIRED_KEYWORDS.search(stripped)
        if found:
            for token in re.split(r'[,\s]+', found.group(1).strip()):
                token = token.strip()
                if token and token not in keywords:
                    keywords.append(token)
            continue
        found = _MUST_IN_CAPTION.search(stripped)
        if found:
            must_caption.append(found.group(1).strip())
            matched = True
        found = _TAG_ACCOUNT.search(stripped)
        if found:
            must_caption.append(found.group(1).strip())
            matched = True
        for pattern in _MUST_IN_VIDEO:
            found = pattern.search(stripped)
            if found:
                must_video.append(found.group(1).strip())
                matched = True
                break

        # -- prohibitions and banned topics --------------------------------
        if any(hint in low for hint in _PROHIBITION_HINTS):
            prohibitions.append(stripped)
            matched = True
        if low.startswith('no ') or ' no ' in f' {low}':
            found = _BANNED_LIST.search(stripped)
            if found:
                for token in re.split(r'[,/]| and ', found.group(1)):
                    token = token.strip().strip('.').lower()
                    token = re.sub(r'\s*content$', '', token)
                    if 2 < len(token) < 40 and token not in banned:
                        banned.append(token)
                matched = True
        if 'only clips from' in low or 'clip this' in low:
            matched = True
            if 'discord' in low and not content_folders:
                sources['manual_only'] = True
                sources['manual_reason'] = (
                    'content folder is published in the campaign Discord; '
                    'no shareable folder link')
        if 'must read' in low or 'read the full' in low:
            matched = True

        if not matched:
            unparsed.append(stripped)

    # -- merge card header ------------------------------------------------
    if card.get('platforms'):
        for platform in card['platforms']:
            if platform not in platforms:
                platforms.append(platform)
    card_min = card.get('min_duration')
    if card_min:
        min_dur = float(card_min) if min_dur is None \
            else max(min_dur, float(card_min))
    for key in ('rate_per_1m', 'budget_total', 'cap_per_post',
                'cap_per_profile'):
        if card.get(key):
            data['campaign'][key] = card[key]
    if card.get('eligible_accounts'):
        gates['eligible_accounts'] = card['eligible_accounts']
    if card.get('type'):
        data['campaign']['type'] = card['type']

    # -- assemble ---------------------------------------------------------
    if min_dur is not None:
        render['min_duration'] = min_dur
    if max_dur is not None:
        render['max_duration'] = max_dur
    if platforms:
        render['platforms'] = platforms
    if content_folders:
        sources['content_folders'] = _dedupe(content_folders)
    if logo_folders:
        assets['logo_folders'] = _dedupe(logo_folders)
    if keywords:
        caption['required_keywords'] = [k for k in keywords
                                        if not k.startswith('@')]
        caption['required_mentions'] = [k for k in keywords
                                       if k.startswith('@')]
    if must_caption:
        caption['must_mention'] = _dedupe(must_caption)
    if must_video:
        render['must_appear_in_video'] = _dedupe(must_video)
    if audience:
        gates['audience'] = audience
    if prohibitions:
        policy['prohibitions'] = _dedupe(prohibitions)
    if banned:
        policy['banned_topics'] = _dedupe(banned)
    data['unparsed'] = unparsed

    if use_model and unparsed:
        extra = _model_pass(unparsed)
        if extra:
            _merge_model(data, extra)

    spec = CampaignSpec.from_dict(data)

    # A campaign that demands the operator's own text but published no content
    # folder link is not an error, it is the Discord case. Flag it as manual so
    # the run refuses early with a reason instead of rendering nothing.
    if not spec.sources.has_any() and not spec.sources.manual_only:
        spec.sources.manual_only = True
        spec.sources.manual_reason = ('no content folder link found in the '
                                      'requirements')
        spec.normalize()
    return spec


def _dedupe(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


_MODEL_PROMPT = """You are compiling social-media campaign requirements into
strict JSON. Below are requirement lines a deterministic parser could not
classify. Extract ONLY what is explicitly stated. Never infer, never add a
requirement that is not written. Omit any key you are unsure about.

Return JSON with any of these optional keys:
  render: {min_duration, max_duration, language, own_text_required,
           gameplay_visible, must_appear_in_video: []}
  caption: {required_keywords: [], required_mentions: [], must_mention: []}
  policy: {keep_live_days, prohibitions: [], banned_topics: []}
  account_gates: {min_engagement_pct, min_views_for_earnings}

Lines:
{lines}

JSON only, no prose, no code fence."""


def _model_pass(lines: List[str]) -> Optional[Dict]:
    """Ask a model about the leftovers only.

    Scoped to unmatched lines on purpose. Handing the model the whole block
    would let it restate rules the parser already captured correctly, and
    disagreements between the two passes would then need arbitration.
    """
    from .config import config
    if not config.script_api_key:
        logger.warning('MODEL_PASS_SKIPPED reason=no_api_key')
        return None
    prompt = _MODEL_PROMPT.replace('{lines}',
                                   '\n'.join(f'- {ln}' for ln in lines))
    for model in [config.script_model] + config.script_model_fallbacks:
        try:
            import google.generativeai as genai
            genai.configure(api_key=config.script_api_key)
            response = genai.GenerativeModel(model).generate_content(prompt)
            body = (response.text or '').strip()
            body = re.sub(r'^```(?:json)?|```$', '', body,
                          flags=re.MULTILINE).strip()
            parsed = json.loads(body)
            logger.info('MODEL_PASS_OK model=%s keys=%s', model,
                        ','.join(parsed.keys()))
            return parsed
        except Exception as exc:
            logger.warning('MODEL_PASS_FAILED model=%s error=%s', model,
                           str(exc)[:160])
    return None


def _merge_model(data: Dict, extra: Dict) -> None:
    """Merge model output additively; the rules pass always wins a conflict."""
    for section in ('render', 'caption', 'policy', 'account_gates'):
        block = extra.get(section)
        if not isinstance(block, dict):
            continue
        target = data.setdefault(section, {})
        for key, value in block.items():
            if key in target:
                continue
            if isinstance(value, list):
                target[key] = [str(v).strip() for v in value if str(v).strip()]
            else:
                target[key] = value


def compile_to_file(raw: str, campaign_id: str, name: str = '', url: str = '',
                    card: Optional[Dict] = None,
                    use_model: bool = False):
    """Compile and write ``config/campaigns/<id>.yaml``, returning the spec."""
    from .config import config
    spec = compile_requirements(raw, campaign_id=campaign_id, name=name,
                                url=url, card=card, use_model=use_model)
    path = config.campaign_spec_dir / f'{spec.id}.yaml'
    spec.save(path)
    logger.info('SPEC_WRITTEN file=%s unparsed=%d conflicts=%d', path.name,
                len(spec.unparsed), len(spec.conflicts))
    return spec, path
