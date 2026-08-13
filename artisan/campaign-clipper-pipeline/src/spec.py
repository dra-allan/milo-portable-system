"""The campaign spec: one schema every campaign's requirements compile into.

Why this module exists at all
----------------------------
Campaign requirement blocks are free prose and they vary per campaign. One
wants a logo, the next wants a hashtag, the next wants gameplay visible and
trending audio added at publish time. If the renderer read that prose it would
need a new branch per campaign forever.

So prose is compiled *once* (see :mod:`compiler`) into a ``CampaignSpec``, the
operator eyeballs the compiled YAML, and from then on the renderer, the caption
writer and the validator read only structured fields. Adding a campaign becomes
data entry, not code.

Two design rules that came straight out of the real campaign cards
------------------------------------------------------------------
**Strictest wins, and the loser is recorded.** The Castle card header says
``Min. Duration 8 secs`` while its own requirements text says ``10s MINIMUM
LENGTH``. Picking one source as authoritative is a coin flip that eventually
costs a submission, so :meth:`CampaignSpec.merge_limit` keeps the stricter
number and logs the looser one into ``conflicts`` rather than silently dropping
it.

**Requirements are tri-state.** The board draws green checks for "do this" and
red crosses for "do not do this". Copy-pasting the block loses the marks, and
the prohibition ``POST SPAM/LOW QUALITY`` then reads as an instruction to post
spam. Prohibitions therefore live in their own field and are never inferred
from line order.
"""

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from .utils import safe_slug, setup_logger

logger = setup_logger(__name__)

# Campaign types that change the *shape* of the deliverable, not just its text.
CLIPPING = 'clipping'
UGC = 'ugc'

# Music policy. ``native`` is the important one: several campaigns require
# trending audio added inside the platform's own composer at publish time.
# That cannot be burned into the file by FFmpeg, so it is a manual gate, and
# the pipeline must say so out loud instead of pretending it handled it.
MUSIC_NONE = 'none'
MUSIC_SOURCE = 'source'
MUSIC_NATIVE = 'native'


@dataclass
class AudienceGate:
    """An account-level audience requirement, e.g. ``US >= 40%``.

    These are facts about the *account*, not about the render, so the pipeline
    cannot fix them. It uses them to refuse a campaign early instead of
    spending an hour rendering something that will be rejected.
    """

    country: str
    operator: str  # '>=' or '<='
    percent: float

    def satisfied_by(self, share_percent: Optional[float]) -> Optional[bool]:
        """None means "unknown", which is not the same as "failed"."""
        if share_percent is None:
            return None
        if self.operator == '>=':
            return share_percent >= self.percent
        return share_percent <= self.percent

    def describe(self) -> str:
        return f'{self.country} {self.operator} {self.percent:g}%'


@dataclass
class Sources:
    """Where the clippable footage comes from.

    ``content_folders`` are share links (Drive today). ``local_folders`` covers
    the campaigns whose content sits behind a Discord invite: there is no API
    to scrape, so you drop the files in a folder once and the pipeline treats
    them exactly like a downloaded folder.
    """

    content_folders: List[str] = field(default_factory=list)
    local_folders: List[str] = field(default_factory=list)
    manual_only: bool = False
    manual_reason: str = ''
    brief_url: str = ''
    discord_url: str = ''

    def has_any(self) -> bool:
        return bool(self.content_folders or self.local_folders)


@dataclass
class Assets:
    logo_folders: List[str] = field(default_factory=list)
    logo_required: bool = False
    # 'if-absent' matches "ADD LOGO IF NOT ALREADY ON CLIP": the source clips
    # in these folders are frequently pre-branded, and double-stamping looks
    # like exactly the low-effort output the campaigns reject.
    logo_mode: str = 'always'  # always | if-absent
    logo_position: str = 'top-right'
    logo_scale: float = 0.14
    logo_margin: float = 0.04
    logo_opacity: float = 1.0


@dataclass
class Render:
    min_duration: float = 8.0
    max_duration: float = 60.0
    platforms: List[str] = field(default_factory=lambda: ['youtube'])
    shorts_only: bool = True
    language: str = 'en'
    own_text_required: bool = False
    gameplay_visible: bool = False
    music: str = MUSIC_NONE
    keep_source_audio: bool = True
    # Words that must be spoken or shown *inside* the video, distinct from
    # caption keywords. Campaign 4 wants the app name in the video; campaign 5
    # wants the full product name in the hook or CTA. The pipeline satisfies
    # these by burning them into the on-screen text, which is the only lever it
    # has without a voice track.
    must_appear_in_video: List[str] = field(default_factory=list)


@dataclass
class Caption:
    required_keywords: List[str] = field(default_factory=list)
    required_mentions: List[str] = field(default_factory=list)
    required_hashtags: List[str] = field(default_factory=list)
    must_mention: List[str] = field(default_factory=list)
    template: str = ''
    max_length: int = 400

    def all_required(self) -> List[str]:
        seen, out = set(), []
        for item in (self.required_keywords + self.required_mentions
                     + self.required_hashtags + self.must_mention):
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(item.strip())
        return out


@dataclass
class AccountGates:
    audience: List[AudienceGate] = field(default_factory=list)
    min_engagement_pct: float = 0.0
    min_views_for_earnings: int = 0
    eligible_accounts: List[str] = field(default_factory=list)
    max_linked_accounts: int = 0


@dataclass
class Policy:
    keep_live_days: int = 0
    prohibitions: List[str] = field(default_factory=list)
    banned_topics: List[str] = field(default_factory=list)
    native_feel: bool = False
    no_competitor_attacks: bool = False


@dataclass
class CampaignSpec:
    id: str
    name: str = ''
    url: str = ''
    type: str = CLIPPING
    enabled: bool = True
    rate_per_1m: float = 0.0
    budget_total: float = 0.0
    cap_per_post: float = 0.0
    cap_per_profile: float = 0.0
    sources: Sources = field(default_factory=Sources)
    assets: Assets = field(default_factory=Assets)
    render: Render = field(default_factory=Render)
    caption: Caption = field(default_factory=Caption)
    account_gates: AccountGates = field(default_factory=AccountGates)
    policy: Policy = field(default_factory=Policy)
    raw_requirements: str = ''
    conflicts: List[str] = field(default_factory=list)
    unparsed: List[str] = field(default_factory=list)
    manual_steps: List[str] = field(default_factory=list)

    # -- construction -------------------------------------------------
    @classmethod
    def from_dict(cls, data: Dict) -> 'CampaignSpec':
        data = dict(data or {})
        campaign = dict(data.get('campaign') or {})
        cid = str(campaign.get('id') or data.get('id') or '').strip()
        if not cid:
            raise ValueError('campaign spec has no id')

        sources_raw = dict(data.get('sources') or {})
        assets_raw = dict(data.get('assets') or {})
        render_raw = dict(data.get('render') or {})
        caption_raw = dict(data.get('caption') or {})
        gates_raw = dict(data.get('account_gates') or {})
        policy_raw = dict(data.get('policy') or {})

        audience = []
        for entry in gates_raw.get('audience') or []:
            if isinstance(entry, dict):
                audience.append(AudienceGate(
                    country=str(entry.get('country') or '').strip(),
                    operator=str(entry.get('operator') or '>=').strip(),
                    percent=float(entry.get('percent') or 0)))
            elif isinstance(entry, str):
                parsed = parse_audience_line(entry)
                if parsed:
                    audience.append(parsed)

        spec = cls(
            id=cid,
            name=str(campaign.get('name') or cid),
            url=str(campaign.get('url') or ''),
            type=str(campaign.get('type') or CLIPPING).lower(),
            enabled=bool(campaign.get('enabled', True)),
            rate_per_1m=float(campaign.get('rate_per_1m') or 0),
            budget_total=float(campaign.get('budget_total') or 0),
            cap_per_post=float(campaign.get('cap_per_post') or 0),
            cap_per_profile=float(campaign.get('cap_per_profile') or 0),
            sources=Sources(
                content_folders=_strlist(sources_raw.get('content_folders')),
                local_folders=_strlist(sources_raw.get('local_folders')),
                manual_only=bool(sources_raw.get('manual_only', False)),
                manual_reason=str(sources_raw.get('manual_reason') or ''),
                brief_url=str(sources_raw.get('brief_url') or ''),
                discord_url=str(sources_raw.get('discord_url') or '')),
            assets=Assets(
                logo_folders=_strlist(assets_raw.get('logo_folders')),
                logo_required=bool(assets_raw.get('logo_required', False)),
                logo_mode=str(assets_raw.get('logo_mode') or 'always'),
                logo_position=str(assets_raw.get('logo_position')
                                  or 'top-right'),
                logo_scale=float(assets_raw.get('logo_scale') or 0.14),
                logo_margin=float(assets_raw.get('logo_margin') or 0.04),
                logo_opacity=float(assets_raw.get('logo_opacity') or 1.0)),
            render=Render(
                min_duration=float(render_raw.get('min_duration') or 8),
                max_duration=float(render_raw.get('max_duration') or 60),
                platforms=_strlist(render_raw.get('platforms')) or ['youtube'],
                shorts_only=bool(render_raw.get('shorts_only', True)),
                language=str(render_raw.get('language') or 'en').lower(),
                own_text_required=bool(render_raw.get('own_text_required',
                                                     False)),
                gameplay_visible=bool(render_raw.get('gameplay_visible',
                                                     False)),
                music=str(render_raw.get('music') or MUSIC_NONE).lower(),
                keep_source_audio=bool(render_raw.get('keep_source_audio',
                                                     True)),
                must_appear_in_video=_strlist(
                    render_raw.get('must_appear_in_video'))),
            caption=Caption(
                required_keywords=_strlist(
                    caption_raw.get('required_keywords')),
                required_mentions=_strlist(
                    caption_raw.get('required_mentions')),
                required_hashtags=_strlist(
                    caption_raw.get('required_hashtags')),
                must_mention=_strlist(caption_raw.get('must_mention')),
                template=str(caption_raw.get('template') or ''),
                max_length=int(caption_raw.get('max_length') or 400)),
            account_gates=AccountGates(
                audience=audience,
                min_engagement_pct=float(
                    gates_raw.get('min_engagement_pct') or 0),
                min_views_for_earnings=int(
                    gates_raw.get('min_views_for_earnings') or 0),
                eligible_accounts=_strlist(
                    gates_raw.get('eligible_accounts')),
                max_linked_accounts=int(
                    gates_raw.get('max_linked_accounts') or 0)),
            policy=Policy(
                keep_live_days=int(policy_raw.get('keep_live_days') or 0),
                prohibitions=_strlist(policy_raw.get('prohibitions')),
                banned_topics=_strlist(policy_raw.get('banned_topics')),
                native_feel=bool(policy_raw.get('native_feel', False)),
                no_competitor_attacks=bool(
                    policy_raw.get('no_competitor_attacks', False))),
            raw_requirements=str(data.get('raw_requirements') or ''),
            conflicts=_strlist(data.get('conflicts')),
            unparsed=_strlist(data.get('unparsed')),
            manual_steps=_strlist(data.get('manual_steps')))
        spec.normalize()
        return spec

    @classmethod
    def load(cls, path) -> 'CampaignSpec':
        import yaml
        p = Path(path)
        data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        if not (data.get('campaign') or {}).get('id'):
            data.setdefault('campaign', {})['id'] = safe_slug(p.stem)
        return cls.from_dict(data)

    def to_dict(self) -> Dict:
        return {
            'campaign': {
                'id': self.id, 'name': self.name, 'url': self.url,
                'type': self.type, 'enabled': self.enabled,
                'rate_per_1m': self.rate_per_1m,
                'budget_total': self.budget_total,
                'cap_per_post': self.cap_per_post,
                'cap_per_profile': self.cap_per_profile},
            'sources': asdict(self.sources),
            'assets': asdict(self.assets),
            'render': asdict(self.render),
            'caption': asdict(self.caption),
            'account_gates': {
                'audience': [asdict(a) for a in self.account_gates.audience],
                'min_engagement_pct': self.account_gates.min_engagement_pct,
                'min_views_for_earnings':
                    self.account_gates.min_views_for_earnings,
                'eligible_accounts': self.account_gates.eligible_accounts,
                'max_linked_accounts':
                    self.account_gates.max_linked_accounts},
            'policy': asdict(self.policy),
            'raw_requirements': self.raw_requirements,
            'conflicts': self.conflicts,
            'unparsed': self.unparsed,
            'manual_steps': self.manual_steps,
        }

    def save(self, path) -> Path:
        import yaml
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False,
                           allow_unicode=True, width=100),
            encoding='utf-8')
        return p

    # -- merging / normalising ----------------------------------------
    def merge_limit(self, name: str, value: Optional[float],
                    stricter: str = 'max') -> None:
        """Merge a numeric limit from a second source, strictest wins.

        The board header and the requirements text disagree in the wild (Castle
        says 8s in the header and 10s in the text). Recording the looser value
        in ``conflicts`` instead of discarding it means a surprising render is
        explainable after the fact.
        """
        if value is None:
            return
        current = getattr(self.render, name, None)
        if current is None:
            setattr(self.render, name, float(value))
            return
        chosen = max(current, float(value)) if stricter == 'max' \
            else min(current, float(value))
        if abs(chosen - current) > 1e-9:
            self.conflicts.append(
                f'{name}: kept {chosen:g} (stricter), saw {current:g}')
            setattr(self.render, name, chosen)
        elif abs(float(value) - current) > 1e-9:
            self.conflicts.append(
                f'{name}: kept {current:g} (stricter), saw {float(value):g}')

    def normalize(self) -> None:
        self.type = self.type if self.type in (CLIPPING, UGC) else CLIPPING
        if self.render.music not in (MUSIC_NONE, MUSIC_SOURCE, MUSIC_NATIVE):
            self.render.music = MUSIC_NONE
        if self.render.max_duration <= self.render.min_duration:
            self.render.max_duration = self.render.min_duration + 10.0
        self.render.platforms = [p.lower().strip()
                                 for p in self.render.platforms if p.strip()]
        if self.assets.logo_folders and not self.assets.logo_required:
            # A logo folder was published but nothing said it was mandatory.
            # Stamping it is the safe default: campaigns reject missing
            # branding far more often than they object to present branding.
            self.assets.logo_required = True
        # Hashtags are just keywords with a sigil; keeping them in two places
        # makes the validator's job ambiguous.
        for tag in list(self.caption.required_hashtags):
            if tag not in self.caption.required_keywords:
                self.caption.required_keywords.append(tag)
        self.caption.required_hashtags = []
        if self.render.music == MUSIC_NATIVE:
            step = ('Add trending audio inside the platform composer at '
                    'publish time. FFmpeg cannot satisfy this requirement.')
            if step not in self.manual_steps:
                self.manual_steps.append(step)
        if self.sources.manual_only and self.sources.manual_reason:
            step = f'Drop source files manually: {self.sources.manual_reason}'
            if step not in self.manual_steps:
                self.manual_steps.append(step)

    # -- gating ---------------------------------------------------------
    def blocking_problems(self) -> List[str]:
        """Reasons this campaign cannot be built right now."""
        problems = []
        if not self.enabled:
            problems.append('campaign disabled in spec')
        if not self.sources.has_any():
            problems.append('no content folder and no local folder configured')
        if self.assets.logo_required and not self.assets.logo_folders:
            problems.append('logo required but no logo folder configured')
        return problems

    def audience_problems(self, shares: Optional[Dict[str, float]] = None
                          ) -> List[str]:
        """Audience gates that the given account demographics fail.

        Unknown shares are reported as unknown, never as a pass. Guessing in
        the optimistic direction here is how you burn an eligible account.
        """
        out = []
        shares = {k.upper(): v for k, v in (shares or {}).items()}
        for gate in self.account_gates.audience:
            value = shares.get(gate.country.upper())
            verdict = gate.satisfied_by(value)
            if verdict is None:
                out.append(f'unknown audience share for '
                           f'{gate.describe()}')
            elif not verdict:
                out.append(f'audience gate failed: {gate.describe()} '
                           f'(actual {value:g}%)')
        return out

    def describe(self) -> str:
        bits = [f'{self.name} [{self.id}]',
                f'type={self.type}',
                f'{self.render.min_duration:g}-'
                f'{self.render.max_duration:g}s',
                f'platforms={"/".join(self.render.platforms)}',
                f'lang={self.render.language}']
        if self.assets.logo_required:
            bits.append(f'logo={self.assets.logo_mode}')
        if self.render.own_text_required:
            bits.append('own-text')
        if self.caption.all_required():
            bits.append('caption:' + ','.join(self.caption.all_required()))
        if self.render.music == MUSIC_NATIVE:
            bits.append('native-audio(manual)')
        return ' | '.join(bits)


# -- helpers -------------------------------------------------------------
def _strlist(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(v).strip() for v in value if str(v).strip()]


_AUDIENCE_RE = re.compile(
    r'^[\*\-\s]*(?P<country>[A-Za-z][A-Za-z .\'-]{1,40}?)\s*'
    r'(?P<op><=|>=|=<|=>|<|>|at least|no more than)\s*'
    r'(?P<pct>\d{1,3})\s*%',
    re.IGNORECASE)


def parse_audience_line(line: str) -> Optional[AudienceGate]:
    """Parse ``India <= 15%`` / ``United States >= 40%`` style gates.

    ``=<`` and ``=>`` are accepted because operators typo them and the board
    renders whatever was typed.
    """
    match = _AUDIENCE_RE.match(line or '')
    if not match:
        return None
    raw_op = match.group('op').lower()
    operator = '<=' if raw_op in ('<=', '=<', '<', 'no more than') else '>='
    return AudienceGate(country=match.group('country').strip(),
                        operator=operator,
                        percent=float(match.group('pct')))


def load_all(spec_dir) -> List[CampaignSpec]:
    """Load every campaign spec in a directory, skipping broken ones loudly."""
    out = []
    for path in sorted(Path(spec_dir).glob('*.yaml')):
        if path.name.startswith('_') or path.name.endswith('.example.yaml'):
            continue
        try:
            out.append(CampaignSpec.load(path))
        except Exception as exc:
            logger.error('SPEC_LOAD_FAILED file=%s error=%s', path.name, exc)
    return out
