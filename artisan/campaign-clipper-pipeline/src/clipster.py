"""Driving the campaign board: browse, read requirements, submit the link.

Session handling
----------------
A **persistent** Playwright profile under the runtime root. You log into the
board once by hand, headful, and every later run reuses that session. The
pipeline never stores, types or sees a password, which also keeps MFA and device
checks entirely out of its path. That is the right trade: automating a login form
would be both the most brittle and the most account-risky thing this lane could
do.

Reading the requirement marks
-----------------------------
The board renders obligations with a green check and prohibitions with a red
cross. Getting that distinction right is not cosmetic: read a prohibition as an
obligation and ``POST SPAM/LOW QUALITY`` becomes an instruction to post spam.

So the mark is derived from each row's **computed colour**, not from a CSS class.
Class names change every time a site is restyled; red and green do not. Rows
whose colour is ambiguous come back as ``unknown`` and the compiler's keyword
heuristics decide, which is the correct order of preference.

Degradation
-----------
Every browser step falls back to a JSON queue on disk rather than failing the
run. A restyle should cost one manual paste, not a dead pipeline. All DOM
assumptions live in ``SELECTORS`` at the top of this file so a break is a
one-place fix.
"""

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from .config import config
from .utils import ensure_dir, read_json, safe_slug, setup_logger, write_json

logger = setup_logger(__name__)

# Every DOM assumption in this module, in one place. Attribute values are left
# unquoted and text matches use single quotes so nothing here needs escaping.
SELECTORS = {
    'campaign_link': 'a[href*=campaign]',
    'requirement_row': ('[class*=requirement] li, [class*=Requirement] li, '
                        "section:has-text('Requirements') li, "
                        "section:has-text('Requirements') div[class*=row]"),
    'submission_input': ('input[placeholder*=link i], input[name*=link i], '
                         'input[type=url], form input[type=text]'),
    'submit_button': ("button:has-text('Submit Content'), "
                      "button:has-text('Submit')"),
    'external_link': 'a[href^=http]',
}

_COLOUR = re.compile(r'rgba?\((\d+),\s*(\d+),\s*(\d+)')

# Colour classification has to run in the page, because computed style only
# exists there. The script returns raw colours and Python decides, so the
# threshold stays reviewable instead of buried in a JS string.
_ROW_SCRIPT = """
(selector) => {
  const rows = Array.from(document.querySelectorAll(selector));
  return rows.map(row => {
    const marker = row.querySelector('svg, i, span[class*=icon]') || row;
    const style = window.getComputedStyle(marker);
    return {
      text: (row.innerText || '').trim(),
      color: style.color || '',
      fill: style.fill || ''
    };
  }).filter(r => r.text.length > 2);
}
"""

_LINK_SCRIPT = """
(els) => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))
"""


def _mark_from_colour(*values: str) -> str:
    """'check' | 'cross' | 'unknown' from any number of colour strings.

    Thresholds are deliberately loose. The board's exact greens and reds shift
    between themes; what stays true is that a prohibition marker is dominantly
    red and an obligation marker is dominantly green.
    """
    for value in values:
        match = _COLOUR.search(value or '')
        if not match:
            continue
        r, g, b = (int(match.group(i)) for i in (1, 2, 3))
        if r > 140 and r > g * 1.6 and r > b * 1.6:
            return 'cross'
        if g > 110 and g > r * 1.3 and g > b * 1.1:
            return 'check'
    return 'unknown'


@contextmanager
def browser():
    """Persistent Playwright context, or a clear error explaining the setup."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            'playwright is not installed. Run: pip install playwright && '
            'python -m playwright install chromium') from exc
    profile = ensure_dir(Path(config.clipster_profile))
    with sync_playwright() as play:
        context = play.chromium.launch_persistent_context(
            str(profile), headless=config.clipster_headless,
            viewport={'width': 1440, 'height': 900},
            args=['--disable-blink-features=AutomationControlled'])
        context.set_default_timeout(config.clipster_timeout)
        try:
            yield context
        finally:
            context.close()


def login(timeout_seconds: int = 300) -> bool:
    """Open the board headful and wait while you sign in.

    Run once per machine. Everything after this reuses the saved profile.
    """
    with browser() as context:
        page = context.new_page()
        page.goto(f'{config.clipster_base}/discover', wait_until='load')
        logger.info('LOGIN_WAIT sign in in the open browser window; waiting '
                    'up to %ds', timeout_seconds)
        try:
            page.wait_for_url(re.compile(r'discover|activity|dashboard'),
                              timeout=timeout_seconds * 1000)
        except Exception:
            pass
        ok = 'clipster' in (page.url or '')
        logger.info('LOGIN_%s url=%s', 'OK' if ok else 'UNCLEAR', page.url)
        return ok


def list_campaigns(platform: str = 'youtube', limit: int = 40) -> List[Dict]:
    """Scrape the discover grid for campaigns on one platform.

    Fields that cannot be parsed are omitted rather than guessed. This is only a
    menu; the campaign page is authoritative for anything that matters.
    """
    url = f'{config.clipster_base}/discover?platforms={platform}'
    out: List[Dict] = []
    try:
        with browser() as context:
            page = context.new_page()
            page.goto(url, wait_until='networkidle')
            page.wait_for_timeout(1500)
            links = page.eval_on_selector_all(SELECTORS['campaign_link'],
                                              _LINK_SCRIPT)
            seen = set()
            for item in links or []:
                href = item.get('href') or ''
                text = item.get('text') or ''
                if not href or href in seen or len(text) < 3:
                    continue
                seen.add(href)
                out.append(_parse_card(href, text))
                if len(out) >= limit:
                    break
    except Exception as exc:
        logger.error('BROWSE_FAILED error=%s', str(exc)[:200])
        return []
    logger.info('BROWSE_OK platform=%s campaigns=%d', platform, len(out))
    return out


_RATE = re.compile(r'\$([\d,.]+)\s*/\s*1M', re.IGNORECASE)
_BUDGET = re.compile(r'/\s*\$([\d,.]+)')
_PROGRESS = re.compile(r'(\d{1,3})\s*%')


def _parse_card(href: str, text: str) -> Dict:
    flat = ' '.join(text.split())
    name = flat.split('$')[0].strip() or flat[:60]
    card: Dict = {'id': safe_slug(name), 'name': name, 'url': href,
                  'raw': flat}
    rate = _RATE.search(flat)
    if rate:
        card['rate_per_1m'] = float(rate.group(1).replace(',', ''))
    budget = _BUDGET.search(flat)
    if budget:
        card['budget_total'] = float(budget.group(1).replace(',', ''))
    progress = _PROGRESS.search(flat)
    if progress:
        card['progress'] = int(progress.group(1))
    low = flat.lower()
    card['type'] = 'ugc' if 'ugc' in low else 'clipping'
    platforms = [name for name, words in (('youtube', ('youtube', 'shorts')),
                                          ('tiktok', ('tiktok',)),
                                          ('instagram', ('instagram',
                                                         'reels')),
                                          ('x', ('twitter',)))
                 if any(word in low for word in words)]
    if platforms:
        card['platforms'] = platforms
    return card


def read_campaign(url: str) -> Optional[Dict]:
    """Open one campaign and return its requirements plus card metadata.

    ``requirements`` is a reconstructed text block with prohibitions prefixed
    ``NO:``, so the sign survives into the compiler even though only the colour
    read knew it.
    """
    try:
        with browser() as context:
            page = context.new_page()
            page.goto(url, wait_until='networkidle')
            page.wait_for_timeout(1200)
            body = page.inner_text('body')
            rows = page.evaluate(_ROW_SCRIPT, SELECTORS['requirement_row'])
            links = page.eval_on_selector_all(SELECTORS['external_link'],
                                              _LINK_SCRIPT)
    except Exception as exc:
        logger.error('READ_CAMPAIGN_FAILED url=%s error=%s', url,
                     str(exc)[:200])
        return None

    obligations: List[str] = []
    prohibitions: List[str] = []
    unknown: List[str] = []
    for row in rows or []:
        text = ' '.join((row.get('text') or '').split())
        if not text:
            continue
        mark = _mark_from_colour(row.get('color', ''), row.get('fill', ''))
        if mark == 'cross':
            prohibitions.append(text)
        elif mark == 'check':
            obligations.append(text)
        else:
            unknown.append(text)

    if not rows:
        logger.warning('REQUIREMENT_ROWS_EMPTY url=%s selectors are likely '
                       'stale; falling back to whole-page text', url)

    # Re-attach links in markdown form so the compiler's link classifier works
    # on scraped text exactly as it does on a block you pasted by hand.
    lines = obligations + [f'NO: {item}' for item in prohibitions] + unknown
    for item in links or []:
        label = ' '.join((item.get('text') or '').split())
        href = item.get('href') or ''
        if label and href and 'clipster' not in href:
            lines.append(f'[{label}]({href})')

    requirements = '\n'.join(lines) if lines else body
    logger.info('READ_CAMPAIGN url=%s obligations=%d prohibitions=%d '
                'unknown=%d', url, len(obligations), len(prohibitions),
                len(unknown))
    return {'url': url, 'requirements': requirements,
            'card': _card_from_body(body), 'obligations': obligations,
            'prohibitions': prohibitions, 'unknown_marks': unknown,
            'body': body}


_MIN_DUR = re.compile(r'Min\.?\s*Duration\s*(\d{1,3})\s*sec', re.IGNORECASE)
_CAP_POST = re.compile(r'Cap per Post\s*\$?([\d,.]+)', re.IGNORECASE)
_CAP_PROFILE = re.compile(r'Cap per Profile\s*\$?([\d,.]+)', re.IGNORECASE)
_ELIGIBLE = re.compile(r'Eligible\s*\n?\s*([A-Za-z0-9_.]{3,40})')


def _card_from_body(body: str) -> Dict:
    """Pull the structured header numbers out of the page text.

    These are the values that disagree with the requirements prose - the header
    minimum duration in particular - which is exactly why they are captured
    separately and merged with strictest-wins instead of blended here.
    """
    card: Dict = {}
    flat = body or ''
    found = _MIN_DUR.search(flat)
    if found:
        card['min_duration'] = float(found.group(1))
    for key, pattern in (('cap_per_post', _CAP_POST),
                         ('cap_per_profile', _CAP_PROFILE)):
        found = pattern.search(flat)
        if found:
            card[key] = float(found.group(1).replace(',', ''))
    found = _RATE.search(flat)
    if found:
        card['rate_per_1m'] = float(found.group(1).replace(',', ''))
    accounts = _ELIGIBLE.findall(flat)
    if accounts:
        card['eligible_accounts'] = sorted(set(accounts))
    return card


def submit_link(campaign_url: str, video_url: str,
                confirm: bool = True) -> bool:
    """Paste a published link into the campaign's submission field.

    ``confirm=False`` fills the field and waits without clicking. That is the
    mode worth using until you trust the validator more than your own eyes: the
    form is filled, you press the button.
    """
    try:
        with browser() as context:
            page = context.new_page()
            page.goto(campaign_url, wait_until='networkidle')
            page.wait_for_timeout(1000)
            field = page.locator(SELECTORS['submission_input']).first
            field.wait_for(state='visible')
            field.fill(video_url)
            if not confirm:
                logger.info('SUBMIT_FILLED_ONLY url=%s link=%s click Submit '
                            'yourself', campaign_url, video_url)
                page.wait_for_timeout(120000)
                return False
            page.locator(SELECTORS['submit_button']).first.click()
            page.wait_for_timeout(2500)
            body = page.inner_text('body').lower()
            if any(word in body for word in ('not eligible', 'invalid',
                                             'rejected')):
                logger.error('SUBMIT_REJECTED url=%s link=%s', campaign_url,
                             video_url)
                return False
            logger.info('SUBMIT_OK url=%s link=%s', campaign_url, video_url)
            return True
    except Exception as exc:
        logger.error('SUBMIT_FAILED url=%s error=%s', campaign_url,
                     str(exc)[:200])
        return False


# -- manual fallback ----------------------------------------------------
def _queue_path() -> Path:
    return config.data_dir / 'manual_submissions.json'


def queue_manual(campaign_id: str, campaign_url: str, video_url: str,
                 caption: str) -> Path:
    """Park a submission for a human when the browser path is unavailable.

    Plain JSON so it is trivially readable and editable. A stuck submission must
    never be invisible.
    """
    path = _queue_path()
    queue = read_json(path, []) or []
    queue.append({'campaign_id': campaign_id, 'campaign_url': campaign_url,
                  'video_url': video_url, 'caption': caption})
    write_json(path, queue)
    logger.warning('SUBMIT_QUEUED_MANUAL campaign=%s file=%s', campaign_id,
                   path)
    return path


def manual_queue() -> List[Dict]:
    return read_json(_queue_path(), []) or []


def clear_manual(campaign_id: str, video_url: str) -> None:
    queue = [item for item in manual_queue()
             if not (item.get('campaign_id') == campaign_id
                     and item.get('video_url') == video_url)]
    write_json(_queue_path(), queue)
