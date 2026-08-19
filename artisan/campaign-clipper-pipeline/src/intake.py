"""Autopilot campaign intake: find fresh, ungated campaigns and add them.

Wraps the existing board scraping and requirement compilation so the daily loop
can discover campaigns without a human. Three rules drive everything here.

**Eligibility is data, not code.** All 47 submissions under Roobet came back
Ineligible because that campaign demands 1000 followers per social profile and
the channels have 0-22. The reject list lives in ``clipper.yaml`` so the wall can
be relaxed the day the channels clear it, without a code change.

**A gate hidden in an ambiguous row is still a gate.** ``read_campaign`` returns
rows whose marker colour it could not classify as ``unknown_marks``. Those are
scanned too: treating an unreadable row as clean is how a submission slot gets
spent on a campaign that was never going to pay.

**Gates are checked twice, by wording and by meaning.** The keyword list catches
"Min Followers per Social Profile"; the compiler's structured
``account_gates.min_views_for_earnings`` and ``min_engagement_pct`` catch the
phrasings nobody thought to list.

``clipster`` and ``compiler`` are injected into :func:`run` rather than imported
at module scope, so the gate logic is testable without Playwright, FFmpeg or a
model key.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import config
from .utils import safe_slug, setup_logger

logger = setup_logger(__name__)


@dataclass
class IntakeReport:
    """What one intake pass did, in the shape the Telegram report needs."""

    added: List = field(default_factory=list)
    rejected: List[Dict] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    waiting_content: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def describe(self) -> str:
        return ' | '.join([
            f'added={len(self.added)}',
            f'rejected={len(self.rejected)}',
            f'skipped={len(self.skipped)}',
            f'waiting_content={len(self.waiting_content)}',
            f'errors={len(self.errors)}'])


def is_fresh(card: Dict, max_progress: float) -> bool:
    """True when the card reports less than ``max_progress`` percent used.

    A missing or unparseable progress figure counts as fresh: the discover card
    omits the number often enough that treating absence as "used up" would hide
    most of the board, and the campaign page is authoritative anyway.

    Note this is a *freshness* proxy, not a payout one. A 5%-used campaign with a
    small pool is worth less than a 60%-used one with a large pool, so this
    threshold decides what to look at, never what to prefer.
    """
    progress = card.get('progress')
    if progress is None:
        return True
    try:
        return float(progress) < float(max_progress)
    except (TypeError, ValueError):
        return True


def gate_campaign(page: Dict, reject: List[str],
                  card: Optional[Dict] = None,
                  platform: str = 'youtube') -> Dict:
    """Return ``{'ok': bool, 'reasons': [...]}`` for a campaign page.

    ``page`` is the dict from ``clipster.read_campaign``. Obligations,
    prohibitions and unclassified rows are all searched, because the mark a row
    carries does not change whether it describes an account gate.
    """
    reasons: List[str] = []
    blob = ' '.join(
        (page.get('obligations') or [])
        + (page.get('prohibitions') or [])
        + (page.get('unknown_marks') or [])).lower()
    for keyword in reject or []:
        keyword = (keyword or '').strip().lower()
        if keyword and keyword in blob and keyword not in reasons:
            reasons.append(keyword)

    platforms = [p.lower() for p in ((card or {}).get('platforms') or [])]
    if platforms and platform.lower() not in platforms:
        reasons.append(f'platform mismatch: {"/".join(platforms)}')

    return {'ok': not reasons, 'reasons': reasons}


def spec_gate_problems(spec) -> List[str]:
    """Account gates the compiler already parsed into structured fields.

    The keyword list catches gates by their wording; this catches them by their
    meaning, after compilation. Cheap second net.
    """
    problems: List[str] = []
    gates = getattr(spec, 'account_gates', None)
    if not gates:
        return problems
    if getattr(gates, 'min_views_for_earnings', 0):
        problems.append(
            f'min views for earnings: {gates.min_views_for_earnings}')
    if getattr(gates, 'min_engagement_pct', 0):
        problems.append(f'min engagement: {gates.min_engagement_pct:g}%')
    return problems


def _known_urls(db) -> set:
    try:
        return {row.get('url') or '' for row in db.campaigns()
                if row.get('url')}
    except Exception as exc:
        logger.error('INTAKE_DB_READ_FAILED error=%s', exc)
        return set()


def run(db, platform: str = 'youtube',
        max_progress: Optional[float] = None,
        reject: Optional[List[str]] = None,
        seen_urls: Optional[set] = None,
        board=None, spec_compiler=None) -> IntakeReport:
    """Scan the board, gate candidates, compile and persist the survivors.

    Every per-campaign failure is recorded and the scan continues: one bad page
    must never cost the rest of the cycle. Gates run cheapest-first so a stale
    campaign never costs a page load and an ineligible one never costs a compile.
    """
    if board is None:
        from . import clipster as board
    if spec_compiler is None:
        from . import compiler as spec_compiler

    report = IntakeReport()
    max_progress = (config.intake_max_progress if max_progress is None
                    else max_progress)
    reject = (config.intake_reject_keywords if reject is None else reject)
    seen_urls = _known_urls(db) if seen_urls is None else seen_urls

    try:
        cards = board.list_campaigns(platform) or []
    except Exception as exc:
        logger.error('INTAKE_BOARD_FAILED error=%s', exc)
        report.errors.append(f'board scan failed: {str(exc)[:120]}')
        return report

    for card in cards:
        url = (card.get('url') or '').strip()
        cid = safe_slug(card.get('id') or card.get('name') or '')
        if not url:
            continue
        if url in seen_urls:
            report.skipped.append(cid)
            continue
        seen_urls.add(url)

        if not is_fresh(card, max_progress):
            report.rejected.append({
                'id': cid, 'name': card.get('name', ''), 'url': url,
                'reasons': [f'progress {card.get("progress")}% >= '
                            f'{float(max_progress):g}%']})
            continue

        try:
            page = board.read_campaign(url)
        except Exception as exc:
            logger.error('INTAKE_READ_FAILED url=%s error=%s', url, exc)
            page = None
        if not page:
            report.rejected.append({
                'id': cid, 'name': card.get('name', ''), 'url': url,
                'reasons': ['campaign page unreadable']})
            continue

        verdict = gate_campaign(page, reject,
                               card=page.get('card') or card,
                               platform=platform)
        if not verdict['ok']:
            logger.info('INTAKE_GATED id=%s reasons=%s', cid,
                        ','.join(verdict['reasons']))
            report.rejected.append({
                'id': cid, 'name': card.get('name', ''), 'url': url,
                'reasons': verdict['reasons']})
            continue

        try:
            spec, _path = spec_compiler.compile_to_file(
                page['requirements'], campaign_id=cid,
                name=card.get('name', cid), url=url,
                card=page.get('card'), use_model=False)
        except Exception as exc:
            logger.error('INTAKE_COMPILE_FAILED url=%s error=%s', url, exc)
            report.rejected.append({
                'id': cid, 'name': card.get('name', ''), 'url': url,
                'reasons': [f'compile failed: {str(exc)[:80]}']})
            continue

        structured = spec_gate_problems(spec)
        if structured:
            logger.info('INTAKE_GATED_STRUCTURED id=%s reasons=%s', cid,
                        ','.join(structured))
            report.rejected.append({
                'id': cid, 'name': card.get('name', ''), 'url': url,
                'reasons': structured})
            continue

        channel = config.resolve_channel(spec.upload_channel, spec.niche)
        if not channel:
            report.rejected.append({
                'id': cid, 'name': card.get('name', ''), 'url': url,
                'reasons': [f'niche "{spec.niche or "?"}" maps to a '
                            'non-campaign channel']})
            continue

        try:
            db.upsert_campaign(spec.id, spec.name, url, spec.to_dict(),
                               page['requirements'])
        except Exception as exc:
            logger.error('INTAKE_DB_WRITE_FAILED id=%s error=%s', cid, exc)
            report.errors.append(f'{cid}: db write failed')
            continue

        report.added.append(spec)
        if not spec.sources.has_any():
            report.waiting_content.append(spec.id)
        logger.info('INTAKE_ADDED id=%s channel=%s url=%s', spec.id, channel,
                    url)

    logger.info('INTAKE_DONE %s', report.describe())
    return report
