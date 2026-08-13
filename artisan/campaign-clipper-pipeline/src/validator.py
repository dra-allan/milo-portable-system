"""The pre-submit compliance gate.

This module is the reason the rest of the lane is safe to automate. Submitting a
non-compliant clip is not a retryable error: it burns one of a small number of
daily submissions, and repeated failures cost the linked account, which is the
only asset in this whole setup that cannot be rebuilt from a git branch.

So the rule is: **measure the artefact, never trust the intent.**

* duration and geometry come from ffprobe on the finished file, not from what
  the renderer was asked to produce;
* text presence comes from counting non-transparent pixels on the rendered
  sheet, because the historical silent failure in this family of pipelines is a
  text stage that draws nothing and exits zero;
* logo presence comes from a template match against the *output* file, so a
  logo stage that ran but composited off-frame is caught.

Anything that genuinely cannot be verified locally (audience geography,
engagement rate, whether the account is eligible) is reported as unverifiable
rather than quietly passed. A gate that pretends to check something it cannot is
worse than no gate.
"""

from pathlib import Path
from typing import Dict, List, Optional

from . import captions, overlay as ov
from .config import config
from .spec import CampaignSpec, MUSIC_NATIVE
from .utils import probe_media, setup_logger

logger = setup_logger(__name__)


def validate(spec: CampaignSpec, video_path, copy: Dict,
             render_report: Optional[Dict] = None,
             audience_shares: Optional[Dict[str, float]] = None) -> Dict:
    """Check one rendered clip against one campaign spec.

    Returns ``{passed, errors, warnings, unverifiable, manual, measured}``.
    ``errors`` block the submission. ``warnings`` do not. ``unverifiable`` is
    the honest middle: requirements that exist and were not checked.
    """
    errors: List[str] = []
    warnings: List[str] = []
    unverifiable: List[str] = []
    render_report = render_report or {}

    path = Path(video_path)
    if not path.exists() or path.stat().st_size == 0:
        return {'passed': False, 'errors': ['output file missing or empty'],
                'warnings': [], 'unverifiable': [],
                'manual': list(spec.manual_steps), 'measured': {}}

    media = probe_media(str(path))

    # -- duration -------------------------------------------------------
    duration = media['duration']
    if duration <= 0:
        errors.append('output has no measurable duration')
    else:
        # No tolerance below the minimum. "9.97 is basically 10" is exactly how
        # a clip that looks compliant gets rejected.
        if duration < spec.render.min_duration:
            errors.append(f'duration {duration:.2f}s below campaign minimum '
                          f'{spec.render.min_duration:g}s')
        if duration > spec.render.max_duration:
            errors.append(f'duration {duration:.2f}s above campaign maximum '
                          f'{spec.render.max_duration:g}s')
        elif duration - spec.render.min_duration < 0.5:
            warnings.append(f'duration {duration:.2f}s is within 0.5s of the '
                            f'minimum; no margin for a platform re-encode')

    # -- geometry -------------------------------------------------------
    if not media['has_video']:
        errors.append('output has no video stream')
    else:
        width, height = media['width'], media['height']
        if height <= width:
            errors.append(f'output is not vertical ({width}x{height}); '
                          'Shorts requires portrait')
        elif (width, height) != (config.width, config.height):
            warnings.append(f'output is {width}x{height}, expected '
                            f'{config.width}x{config.height}')
    if not media['has_audio']:
        warnings.append('output has no audio stream; some platforms treat a '
                        'silent video as broken')

    # -- required on-screen text ----------------------------------------
    if spec.render.own_text_required:
        sheets = render_report.get('sheets') or []
        ink = sum(int(s.get('ink') or 0) for s in sheets)
        if not sheets:
            errors.append('campaign requires your own text but no text sheet '
                          'was rendered')
        elif ink <= 0:
            errors.append('text sheet rendered but contains no visible '
                          'pixels')
        if not (copy.get('overlay_text') or '').strip():
            errors.append('campaign requires your own text but overlay text '
                          'is empty')

    # -- phrases required inside the video -------------------------------
    overlay_text = (copy.get('overlay_text') or '').lower()
    for phrase in spec.render.must_appear_in_video:
        if phrase.lower() not in overlay_text:
            errors.append(f'required in-video phrase missing from burned '
                          f'text: {phrase}')

    # -- logo -------------------------------------------------------------
    if spec.assets.logo_required:
        logo_path = render_report.get('logo_path')
        if not logo_path:
            errors.append('campaign requires a logo but none was resolved')
        else:
            detected = ov.logo_present(str(path), logo_path)
            if detected is True:
                pass
            elif detected is False and render_report.get('logo_stamped'):
                # The stage ran and the logo is not findable in the output. That
                # is a real defect (off-frame overlay, zero-alpha source), not a
                # detector quirk, so it blocks.
                errors.append('logo stage ran but the logo is not detectable '
                              'in the output')
            elif detected is False:
                errors.append('required logo is not present in the output')
            else:
                unverifiable.append('logo presence (OpenCV not available)')

    # -- caption ----------------------------------------------------------
    caption = copy.get('caption') or ''
    missing = [token for token in spec.caption.all_required()
               if token.lower() not in caption.lower()]
    if missing:
        errors.append('caption is missing required tokens: '
                      + ', '.join(missing))
    if spec.caption.max_length and len(caption) > spec.caption.max_length:
        warnings.append(f'caption is {len(caption)} chars, over the '
                        f'{spec.caption.max_length} target')

    banned = sorted(set(captions.banned_hits(caption, spec)
                        + captions.banned_hits(
                            copy.get('overlay_text', ''), spec)))
    if banned:
        errors.append('copy contains forbidden topics: ' + ', '.join(banned))

    # -- platform / language ----------------------------------------------
    if spec.render.platforms and 'youtube' not in spec.render.platforms:
        errors.append('this pipeline publishes to YouTube Shorts, but the '
                      'campaign platforms are '
                      + '/'.join(spec.render.platforms))
    if spec.render.language and spec.render.language != 'en':
        unverifiable.append(f'spoken language is {spec.render.language} '
                            '(source audio not checked)')
    else:
        unverifiable.append('spoken language of the source audio')

    # -- things only the account can satisfy -------------------------------
    for problem in spec.audience_problems(audience_shares):
        if problem.startswith('unknown'):
            unverifiable.append(problem)
        else:
            errors.append(problem)
    if spec.account_gates.min_engagement_pct:
        unverifiable.append(
            f'engagement floor {spec.account_gates.min_engagement_pct:g}% '
            '(post-publish metric)')
    if spec.account_gates.min_views_for_earnings:
        unverifiable.append(
            f'{spec.account_gates.min_views_for_earnings} view earnings '
            'threshold (post-publish metric)')
    if spec.policy.keep_live_days:
        warnings.append(f'keep this video live for at least '
                        f'{spec.policy.keep_live_days} days')

    # -- prohibitions ------------------------------------------------------
    if spec.policy.prohibitions:
        unverifiable.append('quality/spam prohibitions require human review: '
                            + '; '.join(spec.policy.prohibitions[:3]))

    manual = list(spec.manual_steps)
    if spec.render.music == MUSIC_NATIVE:
        warnings.append('trending audio must be added in the platform '
                        'composer at publish time; FFmpeg cannot do this')

    passed = not errors
    if config.strict_validation and unverifiable and not errors:
        # Unverifiable items never fail the clip, but under strict validation
        # they are surfaced loudly enough that nobody auto-submits believing
        # everything was machine-checked.
        logger.warning('VALIDATION_UNVERIFIABLE campaign=%s items=%d',
                       spec.id, len(unverifiable))

    report = {'passed': passed, 'errors': errors, 'warnings': warnings,
              'unverifiable': unverifiable, 'manual': manual,
              'measured': media, 'caption': caption,
              'overlay_text': copy.get('overlay_text', '')}
    _log(spec, path, report)
    return report


def _log(spec: CampaignSpec, path: Path, report: Dict) -> None:
    verdict = 'PASS' if report['passed'] else 'FAIL'
    logger.info('VALIDATE_%s campaign=%s file=%s errors=%d warnings=%d '
                'unverifiable=%d', verdict, spec.id, path.name,
                len(report['errors']), len(report['warnings']),
                len(report['unverifiable']))
    for item in report['errors']:
        logger.error('  [BLOCK] %s', item)
    for item in report['warnings']:
        logger.warning('  [WARN ] %s', item)
    for item in report['unverifiable']:
        logger.info('  [CHECK] %s', item)
    for item in report['manual']:
        logger.info('  [MANUAL] %s', item)


def preflight(spec: CampaignSpec,
              audience_shares: Optional[Dict[str, float]] = None) -> Dict:
    """Can this campaign be built at all, before spending a render on it?

    Called before sourcing. Rendering for an hour and then discovering the
    campaign needs an audience you do not have is pure waste, and the spec
    already carries everything needed to know that up front.
    """
    blocking = spec.blocking_problems()
    audience = spec.audience_problems(audience_shares)
    hard = blocking + [a for a in audience if not a.startswith('unknown')]
    soft = [a for a in audience if a.startswith('unknown')]
    if hard:
        for item in hard:
            logger.error('PREFLIGHT_BLOCK campaign=%s %s', spec.id, item)
    for item in soft:
        logger.warning('PREFLIGHT_WARN campaign=%s %s', spec.id, item)
    return {'ok': not hard, 'blocking': hard, 'warnings': soft,
            'manual': list(spec.manual_steps)}
